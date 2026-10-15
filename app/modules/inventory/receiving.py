"""Physical warehouse receipt of non-batch, QC-cleared purchase orders."""
import json
from datetime import date
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.batches import ProductionBatch
from app.models.catalog import Product, ProductVariant
from app.models.inventory import Location, StockEntry, StockLevel
from app.models.procurement import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.production import ProductionOrder
from app.models.sales import SalesOrder
from app.modules.inventory.dto import WarehouseReceiptCreate


def received_quantities(session, tenant_id, line_ids):
    if not line_ids:
        return {}
    return dict(session.execute(select(GoodsReceiptLine.po_line_id,
        func.sum(GoodsReceiptLine.received)).join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.grn_id)
        .where(GoodsReceiptLine.tenant_id == tenant_id, GoodsReceipt.tenant_id == tenant_id,
            GoodsReceipt.is_deleted.is_(False), GoodsReceiptLine.po_line_id.in_(line_ids))
        .group_by(GoodsReceiptLine.po_line_id)).all())


def _batch_managed(session, po):
    return session.scalar(select(ProductionBatch.id).join(ProductionOrder,
        ProductionOrder.id == ProductionBatch.order_id).where(
        ProductionBatch.tenant_id == po.tenant_id, ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.purchase_order_id == po.id).limit(1)) is not None


def queue(session, *, tenant_id, limit=50, offset=0):
    from app.modules.sales import fulfillment
    tid = UUID(str(tenant_id))
    batch_pos = select(ProductionOrder.purchase_order_id).join(ProductionBatch,
        ProductionBatch.order_id == ProductionOrder.id).where(
        ProductionOrder.tenant_id == tid, ProductionBatch.tenant_id == tid,
        ProductionOrder.purchase_order_id.is_not(None))
    filters = (PurchaseOrder.tenant_id == tid, PurchaseOrder.is_deleted.is_(False),
        PurchaseOrder.inventory_received.is_(False),
        PurchaseOrder.status.not_in(["Cancelled", "Rejected", "Inactive"]),
        PurchaseOrder.id.not_in(batch_pos))
    total = session.scalar(select(func.count()).select_from(PurchaseOrder).where(*filters))
    pos = session.scalars(select(PurchaseOrder).where(*filters)
        .order_by(PurchaseOrder.id).limit(limit).offset(offset)).all()
    result = []
    for po in pos:
        lines = session.scalars(select(PurchaseOrderLine).where(
            PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.tenant_id == tid)
            .order_by(PurchaseOrderLine.id)).all()
        received = received_quantities(session, tid, [line.id for line in lines])
        ready = fulfillment.qc_cleared(session, po)
        result.append({"id": str(po.public_id), "poNo": po.po_no, "supplier": po.supplier_name,
            "plannedLocation": session.get(Location, po.incoming_location_id).name if po.incoming_location_id else None,
            "ready": ready, "status": "Ready for warehouse receipt" if ready else "Awaiting production / final QC",
            "lines": [{"id": str(line.public_id), "name": line.name, "color": line.color,
                "size": line.size, "sku": line.sku, "ordered": line.qty,
                "received": received.get(line.id, 0),
                "remaining": max(0, line.qty - received.get(line.id, 0))} for line in lines]})
    locations = session.scalars(select(Location).where(Location.tenant_id == tid,
        Location.is_deleted.is_(False)).order_by(Location.name)).all()
    return {"orders": result, "total": total,
        "locations": [{"id": str(loc.public_id), "name": loc.name} for loc in locations]}


def post(session, *, tenant_id, public_id, payload: WarehouseReceiptCreate, actor=None):
    # Savepoint keeps a failed receipt atomic even for service callers that
    # catch a validation or concurrency error inside a larger transaction.
    try:
        with session.begin_nested():
            return _post(session, tenant_id=tenant_id, public_id=public_id, payload=payload, actor=actor)
    except IntegrityError as exc:
        raise HTTPException(409, "The receipt changed concurrently. Reload before trying again.") from exc


def _post(session, *, tenant_id, public_id, payload, actor):
    from app.core.audit import write_audit
    from app.modules.inventory.repository import add_stock
    from app.modules.sales import fulfillment
    from app.modules.procurement import repository as procurement
    tid, pid = UUID(str(tenant_id)), UUID(str(public_id))
    body = payload.model_dump(mode="json", exclude={"requestId", "po", "supplier"})
    body["poId"] = str(pid)
    body["lines"] = sorted(body["lines"], key=lambda line: line["poLineId"])
    fingerprint = json.dumps(body, sort_keys=True)

    def replay():
        previous = session.scalar(select(GoodsReceipt).where(GoodsReceipt.tenant_id == tid,
            GoodsReceipt.request_id == payload.requestId))
        if previous and previous.request_payload != fingerprint:
            raise HTTPException(409, "This receipt request was already used with different quantities or a different location.")
        return previous

    previous = replay()
    if previous:
        return previous
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.public_id == pid,
        PurchaseOrder.tenant_id == tid, PurchaseOrder.is_deleted.is_(False)))
    if po is None:
        raise HTTPException(404, "Purchase order not found")
    # Match fulfilment's order -> PO -> stock lock order. The source order
    # receives its manufactured pieces before competing waiting orders.
    orders = session.scalars(select(SalesOrder).where(SalesOrder.tenant_id == tid,
        SalesOrder.is_deleted.is_(False), SalesOrder.fulfillment_tracked.is_(True),
        (SalesOrder.status.in_(["PO needed", "Awaiting stock"])) | (SalesOrder.id == po.sales_order_id))
        .order_by(SalesOrder.id).with_for_update().execution_options(populate_existing=True)).all()
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po.id,
        PurchaseOrder.tenant_id == tid).with_for_update().execution_options(populate_existing=True))
    previous = replay()
    if previous:
        return previous
    if po.is_deleted or po.inventory_received or po.status in ("Cancelled", "Rejected", "Inactive"):
        raise HTTPException(409, "This purchase order cannot receive more stock.")
    source = next((order for order in orders if order.id == po.sales_order_id), None)
    if source and source.status in ("Cancelled", "Inactive"):
        raise HTTPException(409, "The source sales order is cancelled or inactive.")
    if _batch_managed(session, po):
        raise HTTPException(409, "Receive this order using its batch warehouse controls.")
    if not fulfillment.qc_cleared(session, po):
        raise HTTPException(409, "Complete production and pass final QC before warehouse receipt.")
    location = session.scalar(select(Location).where(Location.public_id == payload.locationId,
        Location.tenant_id == tid, Location.is_deleted.is_(False)))
    if location is None:
        raise HTTPException(404, "Choose an active warehouse location.")
    if len(payload.reference.strip()) < 3 or len(payload.reason.strip()) < 3:
        raise HTTPException(422, "Enter a receipt reference and reason of at least three characters.")
    lines = session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id,
        PurchaseOrderLine.tenant_id == tid).order_by(PurchaseOrderLine.id).with_for_update()).all()
    by_public = {line.public_id: line for line in lines}
    ids = [entry.poLineId for entry in payload.lines]
    if len(ids) != len(set(ids)) or any(line_id not in by_public for line_id in ids):
        raise HTTPException(422, "Choose each PO line once, from this purchase order.")
    received = received_quantities(session, tid, [line.id for line in lines])
    planned, quantities = [], {}
    for entry in payload.lines:
        line = by_public[entry.poLineId]
        remaining = line.qty - received.get(line.id, 0)
        if entry.quantity > remaining:
            raise HTTPException(409, f"Only {remaining} units remain to receive for {line.name} / {line.color} / {line.size}.")
        variant = fulfillment.variant_for(session, source or SimpleNamespace(tenant_id=tid), line)
        if variant is None:
            raise HTTPException(409, f"Restore the Catalog variant for {line.name} / {line.color} / {line.size} before receiving.")
        planned.append((line, variant, entry.quantity, remaining))
        quantities[variant.id] = quantities.get(variant.id, 0) + entry.quantity
    products = session.scalars(select(Product).where(Product.tenant_id == tid,
        Product.id.in_({variant.product_id for _, variant, _, _ in planned}))
        .order_by(Product.id).with_for_update().execution_options(populate_existing=True)).all()
    if any(product.is_deleted for product in products):
        raise HTTPException(409, "Restore the retired Catalog article before receiving this PO.")
    supplier = session.get(Supplier, po.supplier_id) if po.supplier_id else None
    grn = procurement.create_receipt(session, tenant_id=tid, po_ref=po.po_no,
        supplier=supplier, supplier_name=po.supplier_name, lines=len(planned))
    grn.request_id, grn.request_payload = payload.requestId, fingerprint
    grn.location_id, grn.location = location.id, location.name
    grn.reference, grn.reason, grn.actor = payload.reference.strip(), payload.reason.strip(), actor
    grn.received_count, grn.received_date = len(planned), date.today().strftime("%d %b %Y")
    for line, variant, qty, remaining in planned:
        session.add(GoodsReceiptLine(tenant_id=tid, grn_id=grn.id, po_line_id=line.id,
            variant_id=variant.id, name=" · ".join(v for v in (line.name, line.color, line.size) if v),
            sku=variant.sku, ordered=remaining, received=qty))
        received[line.id] = received.get(line.id, 0) + qty
    for variant_id, qty in sorted(quantities.items()):
        level = add_stock(session, tenant_id=tid, variant_id=variant_id, location_id=location.id, qty=qty)
        from app.modules.inventory import ledger
        ledger.record(session, level=level, before=level.on_hand-qty, kind="Receipt",
            request_id=payload.requestId, reference=grn.reference, reason=f"{grn.grn_no}: {grn.reason}", actor=actor,
            source_module="procurement", source_type="receipt", source_id=grn.public_id, source_ref=grn.grn_no)
        variant = session.get(ProductVariant, variant_id)
        variant.qty_on_hand = session.scalar(select(func.coalesce(func.sum(StockLevel.on_hand), 0))
            .where(StockLevel.tenant_id == tid, StockLevel.variant_id == variant_id)) or 0
    complete = all(received.get(line.id, 0) == line.qty for line in lines)
    po.inventory_received = complete
    po.status = "Received" if complete else "Partially received"
    grn.status = "Received"  # This document records one completed physical receipt.
    session.flush()
    write_audit(session, tenant_id=tid, action="RECEIVE", entity_type="goods_receipt",
        entity_id=str(grn.public_id), detail=f"{grn.grn_no}: {sum(quantities.values())} units from {po.po_no} at {location.name}; {grn.reference}")
    shipment = fulfillment.refresh_order(session, source) if source else None
    for order in orders:
        if source is None or order.id != source.id:
            fulfillment.refresh_order(session, order)
    if shipment:
        from app.models.quality import Inspection
        work = session.scalar(select(ProductionOrder).where(ProductionOrder.tenant_id == tid,
            ProductionOrder.purchase_order_id == po.id, ProductionOrder.is_deleted.is_(False)))
        if work:
            for inspection in session.scalars(select(Inspection).where(Inspection.tenant_id == tid,
                Inspection.order_ref == work.order_no, Inspection.stage == "Final",
                Inspection.result == "Pass", Inspection.is_deleted.is_(False))).all():
                inspection.shipment_ref = shipment.shipment_no
    session.flush()
    return grn

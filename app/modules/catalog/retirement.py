"""Guarded catalog retirement. Historical commercial and quality records remain."""
from uuid import UUID, uuid4
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import Location, OrderStockReservation, ReorderAlert, StockLevel, StockTransfer, StockTransferLine
from app.models.sales import Invoice, SalesInvoice, SalesOrder, SalesOrderLine
from app.models.procurement import GoodsReceipt, PoInvoice, PurchaseOrder, PurchaseOrderLine
from app.models.finance import SupplierBill
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.batches import ProductionBatch, BatchLine, BatchStage, BatchLot, BatchAllocation, InventoryAllocation, BatchEvent
from app.models.shipments import Shipment, ShipmentLine
from app.models.quality import Inspection
from app.modules.catalog.dto import StockDisposition


_CLOSED_SALES = {"Shipped", "Cancelled", "Inactive"}
_CLOSED_POS = {"Received", "Rejected", "Cancelled", "Inactive"}
_CLOSED_INSPECTIONS = {"Pass", "Fail", "Cancelled"}


def _product(session: Session, tenant_id: UUID, public_id: str, *, lock: bool = False):
    tenant_id = UUID(str(tenant_id))
    try:
        pid = UUID(str(public_id))
    except ValueError:
        raise HTTPException(404, "Article not found") from None
    stmt = select(Product).where(Product.tenant_id == tenant_id,
                                 Product.public_id == pid, Product.is_deleted.is_(False))
    if lock:
        stmt = stmt.with_for_update()
    product = session.scalar(stmt)
    if product is None:
        raise HTTPException(404, "Article not found")
    return product


def _dependencies(session: Session, product: Product, *, lock: bool = False):
    tid = product.tenant_id
    variants = session.scalars(select(ProductVariant).where(
        ProductVariant.tenant_id == tid, ProductVariant.product_id == product.id)).all()
    ids = [v.id for v in variants]
    skus = [v.sku for v in variants]
    stock_stmt = select(StockLevel).where(
        StockLevel.tenant_id == tid, StockLevel.variant_id.in_(ids))
    stock = session.scalars(stock_stmt.with_for_update() if lock else stock_stmt).all()

    sales_lines = session.scalars(select(SalesOrderLine).where(
        SalesOrderLine.tenant_id == tid,
        (SalesOrderLine.variant_id.in_(ids)) | (SalesOrderLine.sku.in_(skus)) |
        (SalesOrderLine.variant_id.is_(None) & SalesOrderLine.sku.is_(None) &
         (SalesOrderLine.name == product.title)))).all()
    production_line_ids = session.scalars(select(ProductionOrderLine.id).where(
        ProductionOrderLine.tenant_id == tid,
        ProductionOrderLine.sku.in_(skus))).all()
    sales_ids = [line.order_id for line in sales_lines]
    orders = session.scalars(select(SalesOrder).where(
        SalesOrder.tenant_id == tid, SalesOrder.id.in_(sales_ids),
        SalesOrder.is_deleted.is_(False))).all()
    po_lines = session.scalars(select(PurchaseOrderLine).where(
        PurchaseOrderLine.tenant_id == tid,
        (PurchaseOrderLine.sku.in_(skus)) |
        (PurchaseOrderLine.sales_order_line_id.in_([line.id for line in sales_lines])) |
        (PurchaseOrderLine.production_line_id.in_(production_line_ids)))).all()
    # Multi-variant POs may store no base SKU. Resolve their exact color/size
    # through the existing variant resolver, which rejects ambiguous names.
    from app.modules.sales.fulfillment import variant_for
    linked_ids = {line.id for line in po_lines}
    for candidate in session.scalars(select(PurchaseOrderLine).where(
            PurchaseOrderLine.tenant_id == tid, PurchaseOrderLine.sku.is_(None))).all():
        resolved = variant_for(session, product, candidate)
        if resolved is not None and resolved.id in ids and candidate.id not in linked_ids:
            po_lines.append(candidate)
            linked_ids.add(candidate.id)
    pos = session.scalars(select(PurchaseOrder).where(
        PurchaseOrder.tenant_id == tid,
        PurchaseOrder.id.in_([line.po_id for line in po_lines]),
        PurchaseOrder.is_deleted.is_(False))).all()
    active_orders = [o for o in orders if o.status not in _CLOSED_SALES]
    active_pos = [p for p in pos if p.status not in _CLOSED_POS]
    transfer_lines = session.scalars(select(StockTransferLine).where(
        StockTransferLine.tenant_id == tid, StockTransferLine.sku.in_(skus),
        StockTransferLine.is_deleted.is_(False))).all()
    transfers = session.scalars(select(StockTransfer).where(
        StockTransfer.tenant_id == tid,
        StockTransfer.id.in_([line.transfer_id for line in transfer_lines]),
        StockTransfer.is_deleted.is_(False),
        StockTransfer.status.not_in(["Received", "Cancelled"]))).all()
    holds = session.scalars(select(OrderStockReservation).where(
        OrderStockReservation.tenant_id == tid,
        OrderStockReservation.stock_level_id.in_([s.id for s in stock]))).all()
    return variants, stock, active_orders, active_pos, transfers, holds, skus, sales_lines, po_lines


def article_shipments(session: Session, product: Product):
    variant_ids = select(ProductVariant.id).where(ProductVariant.tenant_id == product.tenant_id,
        ProductVariant.product_id == product.id)
    skus = select(ProductVariant.sku).where(ProductVariant.tenant_id == product.tenant_id,
        ProductVariant.product_id == product.id)
    lot_ids = select(BatchLot.id).join(BatchLine, BatchLine.id == BatchLot.line_id).where(
        BatchLot.tenant_id == product.tenant_id, BatchLine.variant_id.in_(variant_ids))
    batch_ships = select(BatchAllocation.shipment_id).where(BatchAllocation.tenant_id == product.tenant_id,
        BatchAllocation.lot_id.in_(lot_ids), BatchAllocation.status == "Reserved")
    stock_ids = select(StockLevel.id).where(StockLevel.tenant_id == product.tenant_id,
        StockLevel.variant_id.in_(variant_ids))
    stock_ships = select(InventoryAllocation.shipment_id).where(InventoryAllocation.tenant_id == product.tenant_id,
        InventoryAllocation.stock_level_id.in_(stock_ids), InventoryAllocation.status == "Reserved")
    legacy_ships = select(ShipmentLine.shipment_id).where(ShipmentLine.tenant_id == product.tenant_id,
        ShipmentLine.sku.in_(skus))
    return session.scalars(select(Shipment).where(Shipment.tenant_id == product.tenant_id,
        Shipment.is_deleted.is_(False), Shipment.status.in_(["Pending", "Label created"]),
        Shipment.id.in_(batch_ships) | Shipment.id.in_(stock_ships) | Shipment.id.in_(legacy_ships))).all()


def article_only_shipment(session: Session, product: Product, ship: Shipment):
    # This operation must never cancel another article's shipment commitment.
    skus = set(session.scalars(select(ProductVariant.sku).where(
        ProductVariant.tenant_id == product.tenant_id, ProductVariant.product_id == product.id)).all())
    lines = session.scalars(select(ShipmentLine).where(ShipmentLine.tenant_id == product.tenant_id,
        ShipmentLine.shipment_id == ship.id)).all()
    return bool(lines) and all(line.sku in skus for line in lines)


def cancel_article_shipment(session: Session, *, tenant_id, public_id, shipment_id, reason):
    from app.modules.catalog.commitments import cancel_pending, _reason
    reason = _reason(reason)
    product = _product(session, tenant_id, public_id, lock=True)
    ship = next((s for s in article_shipments(session, product) if s.public_id == shipment_id), None)
    if ship is None:
        raise HTTPException(409, "This article has no matching pending shipment. Refresh the dialog.")
    if ship.sales_order_id:
        from app.modules.sales.fulfillment import lock_order
        lock_order(session, tenant_id=product.tenant_id, order_id=ship.sales_order_id)
    ship = session.scalar(select(Shipment).where(Shipment.id == ship.id).with_for_update().execution_options(populate_existing=True))
    if not article_only_shipment(session, product, ship):
        raise HTTPException(409, "This shipment includes other articles. Resolve the shipment separately before deleting this article.")
    cancel_pending(session, product.tenant_id, [ship], reason)
    return {"cancelled": True}


def preview(session: Session, *, tenant_id: UUID, public_id: str) -> dict:
    product = _product(session, tenant_id, public_id)
    tenant_id = product.tenant_id
    variants, stock, orders, pos, transfers, holds, skus, sales_lines, po_lines = _dependencies(session, product)
    order_ids = [o.id for o in orders]
    po_ids = [p.id for p in pos]
    all_order_lines = session.scalars(select(SalesOrderLine).where(
        SalesOrderLine.tenant_id == tenant_id, SalesOrderLine.order_id.in_(order_ids))).all()
    all_po_lines = session.scalars(select(PurchaseOrderLine).where(
        PurchaseOrderLine.tenant_id == tenant_id, PurchaseOrderLine.po_id.in_(po_ids))).all()
    order_refs = [o.order_no for o in orders if o.order_no]
    po_refs = [p.po_no for p in pos if p.po_no]
    paid_orders = set(session.scalars(select(Invoice.order_no).where(
        Invoice.tenant_id == tenant_id, Invoice.order_no.in_(order_refs),
        Invoice.is_deleted.is_(False), Invoice.status == "Paid")).all())
    paid_orders.update(session.scalars(select(SalesInvoice.order_no).where(
        SalesInvoice.tenant_id == tenant_id, SalesInvoice.order_no.in_(order_refs),
        SalesInvoice.is_deleted.is_(False), SalesInvoice.status == "Paid")).all())
    received_pos = set(session.scalars(select(GoodsReceipt.po_ref).where(
        GoodsReceipt.tenant_id == tenant_id, GoodsReceipt.po_ref.in_(po_refs),
        GoodsReceipt.is_deleted.is_(False), GoodsReceipt.received_count > 0)).all())
    paid_pos = set(session.scalars(select(PoInvoice.po_no).join(
        SupplierBill, SupplierBill.id == PoInvoice.supplier_bill_id).where(
        PoInvoice.tenant_id == tenant_id, PoInvoice.po_no.in_(po_refs),
        PoInvoice.is_deleted.is_(False), SupplierBill.tenant_id == tenant_id,
        SupplierBill.is_deleted.is_(False), SupplierBill.status == "Paid")).all())
    active_linked_pos = session.scalars(select(PurchaseOrder).where(
        PurchaseOrder.tenant_id == tenant_id, PurchaseOrder.is_deleted.is_(False),
        PurchaseOrder.status.not_in(list(_CLOSED_POS)),
        PurchaseOrder.sales_order_id.in_(order_ids))).all()
    active_linked_order_ids = {p.sales_order_id for p in active_linked_pos}
    for po in pos:
        for line in po_lines:
            if line.po_id == po.id and line.sales_order_line_id:
                active_linked_order_ids.update(sales_line.order_id for sales_line in sales_lines
                                               if sales_line.id == line.sales_order_line_id)
    locations = {loc.id: loc.name for loc in session.scalars(select(Location).where(
        Location.tenant_id == tenant_id, Location.id.in_([s.location_id for s in stock]))).all()}
    attributes = {attr.id: attr.value for attr in session.scalars(select(AttributeValue).where(
        AttributeValue.tenant_id == tenant_id,
        AttributeValue.id.in_([value for variant in variants for value in (variant.color_id, variant.size_id) if value]))).all()}
    variant_by_id = {variant.id: variant for variant in variants}
    totals_by_variant = {variant.id: sum(level.on_hand for level in stock if level.variant_id == variant.id)
                         for variant in variants}
    legacy_mismatch = any((variant.qty_on_hand or 0) != totals_by_variant[variant.id] for variant in variants)
    line_ids = session.scalars(select(ProductionOrderLine.id).where(
        ProductionOrderLine.tenant_id == tenant_id, ProductionOrderLine.sku.in_(skus) |
        ProductionOrderLine.id.in_([line.production_line_id for line in po_lines if line.production_line_id]),
        ProductionOrderLine.is_deleted.is_(False))).all()
    open_stages = session.scalars(select(ProductionStage.id).where(
        ProductionStage.tenant_id == tenant_id, ProductionStage.line_id.in_(line_ids),
        ProductionStage.is_deleted.is_(False),
        ProductionStage.status.not_in(["Completed", "Received", "Cancelled"]))).all()
    open_inspections = session.scalars(select(Inspection.id).where(
        Inspection.tenant_id == tenant_id, Inspection.sku.in_(skus),
        Inspection.is_deleted.is_(False),
        Inspection.result.not_in(list(_CLOSED_INSPECTIONS)))).all()
    # A dispatched document must be reconciled through returns, not withdrawn.
    from app.modules.catalog.commitments import pending_shipments
    order_shipments = {o.id: pending_shipments(session, tenant_id, order_id=o.id) for o in orders}
    dispatched_orders = {oid for oid, ships in order_shipments.items()
                         if any(s.status not in ("Pending", "Label created") for s in ships)}
    received_batch_pos = set(session.scalars(select(PurchaseOrder.id).join(
        ProductionOrder, ProductionOrder.purchase_order_id == PurchaseOrder.id).join(
        ProductionBatch, ProductionBatch.order_id == ProductionOrder.id).join(
        BatchLine, BatchLine.batch_id == ProductionBatch.id).where(
        PurchaseOrder.tenant_id == tenant_id, PurchaseOrder.id.in_(po_ids), BatchLine.received > 0)).all())
    lot_balance = session.scalars(select(BatchLot).join(BatchLine, BatchLine.id == BatchLot.line_id).where(
        BatchLot.tenant_id == tenant_id, BatchLine.variant_id.in_([v.id for v in variants]))).all()
    batch_allocated = sum(lot.allocated for lot in lot_balance)
    batch_mismatch = any(sum(lot.on_hand for lot in lot_balance
                            if lot.location_id == level.location_id and
                            session.get(BatchLine, lot.line_id).variant_id == level.variant_id) > level.on_hand for level in stock)
    batch_mismatch = batch_mismatch or any(lot.on_hand and not any(
        level.location_id == lot.location_id and level.variant_id == session.get(BatchLine, lot.line_id).variant_id
        for level in stock) for lot in lot_balance)
    batch_reserved = sum(min(level.reserved, sum(lot.on_hand for lot in lot_balance
        if lot.location_id == level.location_id and session.get(BatchLine, lot.line_id).variant_id == level.variant_id)) for level in stock)
    pending = article_shipments(session, product)
    return {
        "article": product.title,
        "skus": skus,
        "onHand": sum(s.on_hand for s in stock),
        "legacyMismatch": legacy_mismatch,
        "batchAllocated": batch_allocated,
        "batchMismatch": batch_mismatch,
        "batchReserved": batch_reserved,
        "shipments": [{"id": str(s.public_id), "ref": s.shipment_no, "status": s.status,
            "canCancel": article_only_shipment(session, product, s)} for s in pending],
        "stockRows": [{"id": str(level.public_id), "sku": variant_by_id[level.variant_id].sku,
                       "color": attributes.get(variant_by_id[level.variant_id].color_id),
                       "size": attributes.get(variant_by_id[level.variant_id].size_id),
                       "location": locations.get(level.location_id, f"Location #{level.location_id}"),
                       "onHand": level.on_hand, "reserved": level.reserved,
                       "available": max(0, level.on_hand - level.reserved) + (min(level.reserved, sum(lot.on_hand for lot in lot_balance
                           if lot.location_id == level.location_id and session.get(BatchLine, lot.line_id).variant_id == level.variant_id))
                           if not (orders or pos or transfers or holds or batch_allocated or pending) else 0)} for level in stock if level.on_hand],
        "reserved": sum(s.reserved for s in stock),
        "stockHolds": len(holds),
        "orders": [{"id": str(o.public_id), "ref": o.order_no or str(o.public_id), "status": o.status,
                    "otherLines": sum(line.order_id == o.id for line in all_order_lines) -
                                  sum(line.order_id == o.id for line in sales_lines),
                    "heldUnits": sum(hold.qty for hold in holds if hold.order_id == o.id),
                    "canMarkInactive": o.order_no not in paid_orders and o.id not in active_linked_order_ids and o.id not in dispatched_orders,
                    "blockReason": ("Resolve linked POs first" if o.id in active_linked_order_ids
                                    else "Dispatched shipments require a recorded return before withdrawal" if o.id in dispatched_orders
                                    else "Paid invoice must be reconciled first" if o.order_no in paid_orders else None)} for o in orders],
        "purchaseOrders": [{"id": str(p.public_id), "ref": p.po_no or str(p.public_id), "status": p.status,
                            "otherLines": sum(line.po_id == p.id for line in all_po_lines) -
                                          sum(line.po_id == p.id for line in po_lines),
                            "canMarkInactive": not (p.inventory_received or p.po_no in received_pos or p.po_no in paid_pos or p.id in received_batch_pos or p.status == "Partially received"),
                            "blockReason": ("Received goods must be reconciled first" if p.inventory_received or p.po_no in received_pos or p.id in received_batch_pos or p.status == "Partially received"
                                            else "Paid supplier bill must be reconciled first" if p.po_no in paid_pos else None)} for p in pos],
        "transfers": [{"id": str(t.public_id), "ref": t.transfer_no or str(t.public_id), "status": t.status} for t in transfers],
        "openStages": len(open_stages),
        "openInspections": len(open_inspections),
        "canDelete": not (any(s.on_hand or s.reserved for s in stock) or
                          legacy_mismatch or batch_mismatch or any(lot.on_hand or lot.allocated for lot in lot_balance) or pending or holds or orders or pos or transfers),
    }


def dispose_stock(session: Session, *, tenant_id: UUID, public_id: str,
                  payload: StockDisposition, actor=None) -> dict:
    product = _product(session, tenant_id, public_id, lock=True)
    tenant_id = product.tenant_id
    variants, stock, orders, pos, transfers, holds, _, _, _ = _dependencies(session, product, lock=True)
    if orders or pos or transfers or holds or article_shipments(session, product):
        raise HTTPException(409, "Resolve active documents and reservations before disposing of stock.")
    level = next((row for row in stock if row.public_id == payload.stockLevelId), None)
    if level is None:
        raise HTTPException(404, "Stock row not found for this article")
    if payload.quantity > level.on_hand:
        raise HTTPException(409, "Quantity exceeds the stock currently on hand at this location.")
    reason = payload.reason.strip()
    if len(reason) < 3:
        raise HTTPException(400, "Enter a reason for the stock disposition.")
    reference = (payload.reference or "").strip()
    if payload.kind == "Return to supplier" and not reference:
        raise HTTPException(400, "A supplier return reference is required.")
    lots = session.scalars(select(BatchLot).join(BatchLine, BatchLine.id == BatchLot.line_id).where(
        BatchLot.tenant_id == tenant_id, BatchLine.variant_id == level.variant_id,
        BatchLot.location_id == level.location_id).order_by(BatchLot.id).with_for_update()).all()
    if any(lot.allocated for lot in lots):
        raise HTTPException(409, "Cancel pending batch shipments before disposing of their stock.")
    batch_stock = sum(lot.on_hand for lot in lots)
    if batch_stock > level.on_hand:
        raise HTTPException(409, "Reconcile batch stock with warehouse inventory before recording a disposition.")
    if level.reserved > batch_stock:
        raise HTTPException(409, "Resolve unexplained stock reservations before recording a disposition.")
    remaining = max(0, payload.quantity - (level.on_hand - batch_stock))
    for lot in lots:
        disposed = min(lot.on_hand, remaining)
        if not disposed:
            continue
        lot.on_hand -= disposed
        remaining -= disposed
        level.reserved -= min(level.reserved, disposed)
        batch_line = session.get(BatchLine, lot.line_id)
        session.add(BatchEvent(tenant_id=tenant_id, batch_id=batch_line.batch_id, request_id=uuid4(),
            kind="Stock disposition", payload=json.dumps({"reason": reason, "kind": payload.kind,
                "qty": disposed, "lotId": lot.id, "reference": reference}), result=json.dumps({"disposed": disposed})))
    before = level.on_hand
    level.on_hand -= payload.quantity
    from app.modules.inventory import ledger
    ledger.record(session, level=level, before=before,
        kind="Return to supplier" if payload.kind == "Return to supplier" else "Write off",
        reference=reference or payload.kind, reason=reason, actor=actor,
        source_module="catalog", source_type="article", source_id=product.public_id)
    variant = next(v for v in variants if v.id == level.variant_id)
    variant.qty_on_hand = sum(row.on_hand for row in stock if row.variant_id == variant.id)
    location = session.scalar(select(Location).where(
        Location.tenant_id == tenant_id, Location.id == level.location_id))
    detail = (f"{payload.kind}: {payload.quantity} units of {variant.sku} at "
              f"{location.name if location else f'location #{level.location_id}'}; "
              f"reason: {reason}" + (f"; reference: {reference}" if reference else ""))
    write_audit(session, tenant_id=tenant_id, action="ADJUST", entity_type="product",
                entity_id=str(product.public_id), detail=detail)
    write_audit(session, tenant_id=tenant_id, action="ADJUST", entity_type="stock_level",
                entity_id=str(level.public_id), detail=detail)
    session.flush()
    return {"remaining": level.on_hand, "sku": variant.sku}


def reconcile_stock_cache(session: Session, *, tenant_id: UUID, public_id: str,
                          reason: str) -> dict:
    product = _product(session, tenant_id, public_id, lock=True)
    tenant_id = product.tenant_id
    variants, stock, orders, pos, transfers, holds, _, _, _ = _dependencies(session, product, lock=True)
    if orders or pos or transfers or holds or article_shipments(session, product) or any(level.on_hand or level.reserved for level in stock):
        raise HTTPException(409, "Clear physical stock and active commitments before reconciling the legacy balance.")
    if len(reason.strip()) < 3:
        raise HTTPException(400, "Enter a reason for the balance reconciliation.")
    for variant in variants:
        variant.qty_on_hand = 0
    write_audit(session, tenant_id=tenant_id, action="ADJUST", entity_type="product",
                entity_id=str(product.public_id),
                detail=f"Reconciled legacy quantity to zero after physical stock cleared; reason: {reason.strip()}")
    session.flush()
    return {"reconciled": True}


def retire(session: Session, *, tenant_id: UUID, public_id: str) -> dict:
    product = _product(session, tenant_id, public_id, lock=True)
    tenant_id = product.tenant_id
    variants, stock, orders, pos, transfers, holds, skus, _, po_lines = _dependencies(session, product, lock=True)
    legacy_mismatch = any((variant.qty_on_hand or 0) != sum(level.on_hand for level in stock if level.variant_id == variant.id)
                          for variant in variants)
    if (any(s.on_hand or s.reserved for s in stock) or legacy_mismatch
            or holds or orders or pos or transfers or article_shipments(session, product)):
        raise HTTPException(409, "Resolve stock, reservations, and active orders, POs, or transfers before deleting this article.")
    lots = session.scalars(select(BatchLot).join(BatchLine, BatchLine.id == BatchLot.line_id).where(
        BatchLot.tenant_id == tenant_id, BatchLine.variant_id.in_([v.id for v in variants])).with_for_update()).all()
    if any(lot.on_hand or lot.allocated for lot in lots):
        raise HTTPException(409, "Resolve batch stock and shipment allocations before deleting this article.")

    # Stop only the matching item in a multi-item production order. Keep its
    # timeline and inspection findings available for audit and order history.
    lines = session.scalars(select(ProductionOrderLine).where(
        ProductionOrderLine.tenant_id == tenant_id,
        ProductionOrderLine.sku.in_(skus) | ProductionOrderLine.id.in_([line.production_line_id for line in po_lines if line.production_line_id]),
        ProductionOrderLine.is_deleted.is_(False))).all()
    for line in lines:
        stages = session.scalars(select(ProductionStage).where(
            ProductionStage.tenant_id == tenant_id, ProductionStage.line_id == line.id,
            ProductionStage.is_deleted.is_(False))).all()
        for stage in stages:
            if stage.status not in {"Completed", "Received", "Cancelled"}:
                stage.status = "Cancelled"
                stage.notes = ((stage.notes or "") + " Article retired.").strip()[:400]
    order_refs = session.scalars(select(ProductionOrder.order_no).where(ProductionOrder.tenant_id == tenant_id,
        ProductionOrder.id.in_([line.order_id for line in lines]))).all()
    inspections = session.scalars(select(Inspection).where(
        Inspection.tenant_id == tenant_id, Inspection.sku.in_(skus) |
        ((Inspection.item.in_([line.name for line in lines])) & Inspection.order_ref.in_(order_refs)),
        Inspection.is_deleted.is_(False))).all()
    for inspection in inspections:
        if inspection.result not in _CLOSED_INSPECTIONS:
            inspection.result = "Cancelled"
            inspection.disposition_notes = "Article retired."

    batch_ids = session.scalars(select(BatchLine.batch_id).where(BatchLine.tenant_id == tenant_id,
        BatchLine.variant_id.in_([v.id for v in variants])).distinct()).all()
    for batch in session.scalars(select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id,
            ProductionBatch.id.in_(batch_ids))).all():
        all_lines = session.scalars(select(BatchLine).where(BatchLine.tenant_id == tenant_id,
            BatchLine.batch_id == batch.id)).all()
        if all(session.get(ProductVariant, line.variant_id).product_id == product.id or
               session.get(Product, session.get(ProductVariant, line.variant_id).product_id).is_deleted for line in all_lines):
            batch.status = "Cancelled"
            for stage in session.scalars(select(BatchStage).where(BatchStage.tenant_id == tenant_id,
                    BatchStage.batch_id == batch.id, BatchStage.status != "Completed")).all():
                stage.status = "Cancelled"
                stage.notes = "Article retired."
    for level in stock:
        # Shipment inventory origins have an FK to this row. Keep the zero
        # balance for history; archived variants remove it from active inventory.
        if session.scalar(select(InventoryAllocation.id).where(InventoryAllocation.tenant_id == tenant_id,
                InventoryAllocation.stock_level_id == level.id).limit(1)) is None:
            session.delete(level)
    for alert in session.scalars(select(ReorderAlert).where(
        ReorderAlert.tenant_id == tenant_id,
        (ReorderAlert.variant_id.in_([v.id for v in variants])) | (ReorderAlert.sku.in_(skus)),
        ReorderAlert.is_deleted.is_(False))).all():
        alert.is_deleted = True
    for variant in variants:
        variant.status = "Archived"
    product.is_deleted = True
    write_audit(session, tenant_id=tenant_id, action="DELETE", entity_type="product",
                entity_id=str(product.public_id), detail=f"Retired article “{product.title}”")
    session.flush()
    return {"deleted": True, "article": product.title}

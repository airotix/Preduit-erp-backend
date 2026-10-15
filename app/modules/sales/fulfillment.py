"""Order-owned stock holds and shortage procurement, in the request transaction."""
from datetime import date, timedelta
from uuid import UUID
import re

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.catalog import Product, ProductVariant, AttributeValue
from app.models.inventory import Location, OrderStockReservation, StockLevel
from app.models.sales import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.production import ProductionOrder, ProductionOrderLine
from app.models.quality import Inspection
from app.models.shipments import Shipment, ShipmentLine
from app.models.batches import ProductionBatch
from app.modules.sales.ordinary_shipping import dispatched as normal_dispatched


def lock_order(session, *, tenant_id, public_id=None, order_id=None):
    stmt = select(SalesOrder).where(SalesOrder.tenant_id == UUID(str(tenant_id)), SalesOrder.is_deleted.is_(False))
    stmt = stmt.where(SalesOrder.id == order_id) if order_id is not None else stmt.where(SalesOrder.public_id == UUID(str(public_id)))
    order = session.scalar(stmt.with_for_update().execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


def lock_waiting_orders(session, *, tenant_id):
    """Take order locks before stock locks; allocate restocks oldest-first."""
    return session.scalars(select(SalesOrder).where(
        SalesOrder.tenant_id == UUID(str(tenant_id)), SalesOrder.is_deleted.is_(False),
        SalesOrder.fulfillment_tracked.is_(True),
        SalesOrder.status.in_(["PO needed", "Awaiting stock", "Partially shipped", "Ready to pack", "Packed"])
    ).order_by(SalesOrder.id).with_for_update().execution_options(populate_existing=True)).all()


def refresh_waiting_orders(session, orders):
    """Use the caller's locked queue after its stock writes, in the same transaction."""
    session.flush()
    for order in orders:
        refresh_order(session, order)


def lines_for(session, order):
    return session.scalars(select(SalesOrderLine).where(
        SalesOrderLine.order_id == order.id, SalesOrderLine.tenant_id == order.tenant_id
    ).order_by(SalesOrderLine.id)).all()


def variant_for(session, order, line):
    """An explicit color/size takes precedence over a style's base SKU."""
    source = session.get(SalesOrderLine, line.sales_order_line_id) if isinstance(line, PurchaseOrderLine) and line.sales_order_line_id else line
    variant_id = getattr(source, "variant_id", None)
    if variant_id:
        return session.scalar(select(ProductVariant).join(Product, Product.id == ProductVariant.product_id).where(
            ProductVariant.id == variant_id, ProductVariant.tenant_id == order.tenant_id,
            Product.tenant_id == order.tenant_id, Product.is_deleted.is_(False)))
    product = session.scalar(select(Product).join(ProductVariant, ProductVariant.product_id == Product.id).where(
        Product.tenant_id == order.tenant_id, ProductVariant.tenant_id == order.tenant_id,
        ProductVariant.sku == line.sku, Product.is_deleted.is_(False))) if line.sku else None
    if product is None:
        products = session.scalars(select(Product).where(Product.tenant_id == order.tenant_id,
            Product.title == line.name, Product.is_deleted.is_(False)).limit(2)).all()
        product = products[0] if len(products) == 1 else None
    stmt = select(ProductVariant).join(Product, Product.id == ProductVariant.product_id).where(
        ProductVariant.tenant_id == order.tenant_id, Product.tenant_id == order.tenant_id,
        Product.is_deleted.is_(False))
    if product is not None and (line.color or line.size):
        stmt = stmt.where(ProductVariant.product_id == product.id)
        for kind, value, column in [("Color", line.color, ProductVariant.color_id), ("Size", line.size, ProductVariant.size_id)]:
            if value:
                stmt = stmt.where(column.in_(select(AttributeValue.id).where(
                    AttributeValue.tenant_id == order.tenant_id, AttributeValue.attr_type == kind,
                    func.lower(AttributeValue.value) == value.strip().lower())))
    elif line.sku:
        stmt = stmt.where(ProductVariant.sku == line.sku)
    else:
        return None
    matches = session.scalars(stmt.order_by(ProductVariant.id).limit(2)).all()
    return matches[0] if len(matches) == 1 else None


def holds_for(session, order):
    return session.scalars(select(OrderStockReservation).where(
        OrderStockReservation.order_id == order.id, OrderStockReservation.tenant_id == order.tenant_id
    ).order_by(OrderStockReservation.stock_level_id)).all()


def stock_summary(session, order):
    from app.modules.batches.service import managed_sales, dispatched_sales
    batch_managed = managed_sales(session, order)
    sent = dispatched_sales(session, order.tenant_id, order.id) if batch_managed else normal_dispatched(session, order)
    lines = lines_for(session, order)
    totals = {}
    for hold in holds_for(session, order):
        totals[hold.order_line_id] = totals.get(hold.order_line_id, 0) + hold.qty
    return [{"lineId": line.id, "name": line.name, "color": line.color, "size": line.size,
             "sku": line.sku, "ordered": line.qty, "reserved": totals.get(line.id, 0),
             "dispatched": sent.get(line.id, 0),
             "outstanding": max(0, line.qty - sent.get(line.id, 0)),
             "missing": 0 if order.status in ("Shipped", "Cancelled", "Inactive") else max(0, line.qty - sent.get(line.id, 0) - totals.get(line.id, 0))} for line in lines]


def reserve_available(session, order):
    """Caller locks order; lock stock in a stable order to avoid overselling."""
    if not order.fulfillment_tracked or order.status in ("Cancelled", "Inactive", "Shipped"):
        return
    from app.modules.batches.service import managed_sales, dispatched_sales
    sent = dispatched_sales(session, order.tenant_id, order.id) if managed_sales(session, order) else normal_dispatched(session, order)
    lines = lines_for(session, order)
    variants = {line.id: variant_for(session, order, line) for line in lines}
    for line in lines:
        if variants[line.id] is not None:
            line.variant_id = variants[line.id].id
    vids = sorted({v.id for v in variants.values() if v is not None})
    stock = session.scalars(select(StockLevel).join(Location, Location.id == StockLevel.location_id).where(
        StockLevel.tenant_id == order.tenant_id, StockLevel.variant_id.in_(vids),
        Location.tenant_id == order.tenant_id, Location.is_deleted.is_(False)
    ).order_by(StockLevel.id).with_for_update(of=StockLevel).execution_options(populate_existing=True)).all()
    holds = holds_for(session, order)
    owned = {(h.order_line_id, h.stock_level_id): h for h in holds}
    for line in lines:
        missing = line.qty - sent.get(line.id, 0) - sum(h.qty for h in holds if h.order_line_id == line.id)
        variant = variants[line.id]
        if variant is None or missing <= 0:
            continue
        for level in stock:
            if level.variant_id != variant.id:
                continue
            qty = min(missing, max(0, level.on_hand - level.reserved))
            if qty <= 0:
                continue
            hold = owned.get((line.id, level.id))
            if hold is None:
                hold = OrderStockReservation(tenant_id=order.tenant_id, order_id=order.id,
                    order_line_id=line.id, stock_level_id=level.id, qty=qty)
                session.add(hold)
                holds.append(hold)
            else:
                hold.qty += qty
            level.reserved += qty
            missing -= qty
            if not missing:
                break
    session.flush()


def release_holds(session, order, *, consume=False, shipment=None, actor=None):
    holds = holds_for(session, order)
    stock = {s.id: s for s in session.scalars(select(StockLevel).where(
        StockLevel.id.in_([h.stock_level_id for h in holds]), StockLevel.tenant_id == order.tenant_id
    ).order_by(StockLevel.id).with_for_update().execution_options(populate_existing=True)).all()}
    before = {level.id: level.on_hand for level in stock.values()}
    for hold in holds:
        level = stock[hold.stock_level_id]
        if level.reserved < hold.qty or (consume and level.on_hand < hold.qty):
            raise HTTPException(409, "Reserved stock has changed; reconcile inventory before shipping.")
        level.reserved -= hold.qty
        if consume:
            level.on_hand -= hold.qty
        session.delete(hold)
    session.flush()
    if consume:
        from uuid import uuid4
        from app.modules.inventory import ledger
        movement_id = shipment.public_id if shipment else uuid4()
        for level in stock.values():
            ledger.record(session, level=level, before=before[level.id], kind="Dispatch",
                request_id=movement_id, reference=shipment.shipment_no if shipment else order.order_no,
                reason=f"Dispatched for {order.order_no}", actor=actor,
                source_module="shipments" if shipment else "sales",
                source_type="shipment" if shipment else "order",
                source_id=shipment.public_id if shipment else order.public_id)
        for variant_id in {level.variant_id for level in stock.values()}:
            variant = session.get(ProductVariant, variant_id)
            if variant is not None:
                variant.qty_on_hand = session.scalar(select(func.coalesce(func.sum(StockLevel.on_hand), 0))
                    .where(StockLevel.tenant_id == order.tenant_id, StockLevel.variant_id == variant_id)) or 0
        session.flush()


def purchase_orders(session, order):
    return session.scalars(select(PurchaseOrder).where(PurchaseOrder.tenant_id == order.tenant_id,
        PurchaseOrder.sales_order_id == order.id, PurchaseOrder.is_deleted.is_(False),
        PurchaseOrder.status.not_in(["Rejected", "Cancelled", "Inactive"])).order_by(PurchaseOrder.id)).all()


def procurement_summary(session, order):
    rows = stock_summary(session, order)
    sources = {line.id: line for line in lines_for(session, order)}
    outstanding = {}
    from app.modules.inventory.receiving import received_quantities
    for po in purchase_orders(session, order):
        if po.inventory_received:
            continue
        po_lines = session.scalars(select(PurchaseOrderLine).where(
            PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.tenant_id == order.tenant_id)).all()
        received = received_quantities(session, order.tenant_id, [line.id for line in po_lines])
        for line in po_lines:
            outstanding[line.sales_order_line_id] = outstanding.get(line.sales_order_line_id, 0) + max(0, line.qty - received.get(line.id, 0))
    for row in rows:
        row["toPurchase"] = max(0, row["missing"] - outstanding.get(row["lineId"], 0))
        variant = variant_for(session, order, sources[row["lineId"]])
        row["productId"] = variant.product_id if variant else None
    return rows


def qc_cleared(session, po):
    work = session.scalar(select(ProductionOrder).where(ProductionOrder.purchase_order_id == po.id,
        ProductionOrder.tenant_id == po.tenant_id, ProductionOrder.is_deleted.is_(False)))
    if work is None:
        return False
    items = session.scalars(select(ProductionOrderLine).where(ProductionOrderLine.order_id == work.id,
        ProductionOrderLine.tenant_id == po.tenant_id)).all()
    inspections = session.scalars(select(Inspection).where(Inspection.order_ref == work.order_no,
        Inspection.tenant_id == po.tenant_id, Inspection.is_deleted.is_(False))).all()
    if not items or not inspections:
        return False
    from app.modules.production.service import _line_is_complete
    latest = {}
    for ins in sorted(inspections, key=lambda row: row.id):
        if ins.stage == "Final" and ins.result != "Cancelled":
            latest[ins.item] = ins
    return all(_line_is_complete(session, line.id) and line.name in latest and latest[line.name].result == "Pass"
               for line in items)


def refresh_order(session, order):
    from app.modules.batches.service import managed_sales, update_sales
    if order.status == "Inactive":
        return None
    if managed_sales(session, order):
        if order.status != "Cancelled":
            reserve_available(session, order)
        update_sales(session, order.tenant_id, order.id)
        return None
    if not order.fulfillment_tracked or order.status in ("Cancelled", "Inactive"):
        return None
    from app.modules.sales.ordinary_shipping import refresh
    return refresh(session, order)


def fulfillment_detail(session, order):
    if not order.fulfillment_tracked:
        return None
    from app.modules.batches.service import managed_sales
    rows = procurement_summary(session, order)
    pos = purchase_orders(session, order)
    shipments = session.scalars(select(Shipment).where(Shipment.sales_order_id == order.id,
        Shipment.tenant_id == order.tenant_id, Shipment.is_deleted.is_(False)).order_by(Shipment.id)).all()
    shipment = next((s for s in shipments if not s.inventory_dispatched and s.status != "Cancelled"), None)
    return {"lines": rows, "canCreatePO": order.status not in ("Shipped", "Cancelled", "Inactive") and any(r["toPurchase"] for r in rows),
        "purchaseOrders": [{"publicId": str(po.public_id), "poNo": po.po_no} for po in pos],
        "shipment": {"publicId": str(shipment.public_id), "shipmentNo": shipment.shipment_no} if shipment else None,
        "allowPartialShipments": order.allow_partial_shipments,
        "batchManaged": managed_sales(session, order),
        "shipments": [{"publicId": str(s.public_id), "shipmentNo": s.shipment_no, "status": s.status,
            "packed": bool(s.packed_at), "dispatched": s.inventory_dispatched} for s in shipments]}


def create_shortage_po(session, *, tenant_id, public_id, supplier_id):
    pos = create_shortage_pos(session, tenant_id=tenant_id, public_id=public_id, supplier_id=supplier_id)
    return pos[0] if pos else None


def create_shortage_pos(session, *, tenant_id, public_id, supplier_id=None, assignments=None):
    order = lock_order(session, tenant_id=tenant_id, public_id=public_id)
    if not order.fulfillment_tracked or order.status in ("Cancelled", "Inactive", "Shipped"):
        raise HTTPException(409, "This order cannot create a shortage PO.")
    reserve_available(session, order)
    source_lines = {ln.id: ln for ln in lines_for(session, order)}
    shortages = [r for r in procurement_summary(session, order) if r["toPurchase"]]
    if not shortages:
        refresh_order(session, order)
        return purchase_orders(session, order)
    chosen = {}
    if assignments is not None:
        for entry in assignments:
            line_id, supplier = entry.lineId, entry.supplierId
            if line_id in chosen or line_id not in source_lines:
                raise HTTPException(400, "Invalid or duplicate sales order line.")
            chosen[line_id] = supplier
        if set(chosen) != {r["lineId"] for r in shortages}:
            raise HTTPException(409, "Shortages changed. Reload and select a supplier for every remaining item.")
    else:
        chosen = {r["lineId"]: supplier_id for r in shortages}
    groups = {}
    for row in shortages:
        variant = variant_for(session, order, source_lines[row["lineId"]])
        if variant is None or variant.supplier_price is None:
            raise HTTPException(409, f"Set a catalog variant and supplier purchase price for {row['name']} ({row['color'] or 'no color'}, {row['size'] or 'no size'}) before creating its PO.")
        group = groups.setdefault(variant.product_id, {"supplierId": chosen[row["lineId"]], "lines": [], "sources": []})
        if group["supplierId"] != chosen[row["lineId"]]:
            raise HTTPException(400, "Choose one supplier for all variants of an item.")
        group["lines"].append({"name": row["name"], "color": row["color"], "size": row["size"],
            "sku": variant.sku, "qty": row["toPurchase"], "price": variant.supplier_price})
        group["sources"].append(row["lineId"])
    from app.modules.procurement.dto import PurchaseOrderCreate
    from app.modules.procurement import service as procurement
    # Validate every supplier before creating any of the linked POs.
    for group in groups.values():
        group["supplier"] = session.scalar(select(Supplier).where(
            Supplier.public_id == UUID(str(group["supplierId"])),
            Supplier.tenant_id == order.tenant_id, Supplier.is_deleted.is_(False)))
        if group["supplier"] is None:
            raise HTTPException(404, "Supplier not found")
    created = []
    for group in groups.values():
        supplier = group["supplier"]
        lead = re.search(r"\d+", supplier.lead_time or "")
        days = int(lead.group()) if lead else 30
        if lead and "week" in supplier.lead_time.lower():
            days *= 7
        po = procurement.create_po(session, tenant_id=order.tenant_id, resolved_supplier=supplier, payload=PurchaseOrderCreate(
            supplier=supplier.name, expected=(date.today() + timedelta(days=min(days, 3650))).strftime("%d %b %Y"), lines=group["lines"]))
        po.sales_order_id = order.id
        po_lines = session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id,
            PurchaseOrderLine.tenant_id == order.tenant_id).order_by(PurchaseOrderLine.id)).all()
        for source_id, target in zip(group["sources"], po_lines):
            target.sales_order_line_id = source_id
        created.append(po)
    refresh_order(session, order)
    session.flush()
    return created


def complete_shipment(session, order, *, shipment=None, actor=None):
    if order.status in ("Cancelled", "Inactive"):
        raise HTTPException(409, "A cancelled order cannot ship.")
    if shipment is None:
        raise HTTPException(409, "Choose a quantity-based shipment before dispatch")
    from app.modules.sales.ordinary_shipping import dispatch
    dispatch(session, order, shipment, actor)

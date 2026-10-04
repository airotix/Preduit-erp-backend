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
        SalesOrder.status.in_(["PO needed", "Awaiting stock"])
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
    totals = {}
    for hold in holds_for(session, order):
        totals[hold.order_line_id] = totals.get(hold.order_line_id, 0) + hold.qty
    return [{"lineId": line.id, "name": line.name, "color": line.color, "size": line.size,
             "sku": line.sku, "ordered": line.qty, "reserved": totals.get(line.id, 0),
             "missing": 0 if order.status in ("Shipped", "Cancelled") else max(0, line.qty - totals.get(line.id, 0))} for line in lines_for(session, order)]


def reserve_available(session, order):
    """Caller locks order; lock stock in a stable order to avoid overselling."""
    if not order.fulfillment_tracked or order.status in ("Cancelled", "Shipped"):
        return
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
        missing = line.qty - sum(h.qty for h in holds if h.order_line_id == line.id)
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


def release_holds(session, order, *, consume=False):
    holds = holds_for(session, order)
    stock = {s.id: s for s in session.scalars(select(StockLevel).where(
        StockLevel.id.in_([h.stock_level_id for h in holds]), StockLevel.tenant_id == order.tenant_id
    ).order_by(StockLevel.id).with_for_update().execution_options(populate_existing=True)).all()}
    for hold in holds:
        level = stock[hold.stock_level_id]
        if level.reserved < hold.qty or (consume and level.on_hand < hold.qty):
            raise HTTPException(409, "Reserved stock has changed; reconcile inventory before shipping.")
        level.reserved -= hold.qty
        if consume:
            level.on_hand -= hold.qty
        session.delete(hold)
    session.flush()


def purchase_orders(session, order):
    return session.scalars(select(PurchaseOrder).where(PurchaseOrder.tenant_id == order.tenant_id,
        PurchaseOrder.sales_order_id == order.id, PurchaseOrder.is_deleted.is_(False)).order_by(PurchaseOrder.id)).all()


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
    if not order.fulfillment_tracked or order.status == "Cancelled":
        return None
    if order.status == "Shipped":
        return session.scalar(select(Shipment).where(Shipment.tenant_id == order.tenant_id,
            Shipment.sales_order_id == order.id, Shipment.is_deleted.is_(False)))
    reserve_available(session, order)
    missing = any(row["missing"] for row in stock_summary(session, order))
    pos = purchase_orders(session, order)
    if missing:
        order.status = "Awaiting stock" if pos else "PO needed"
        session.flush()
        return None
    existing = session.scalar(select(Shipment).where(Shipment.tenant_id == order.tenant_id,
        Shipment.sales_order_id == order.id, Shipment.is_deleted.is_(False)))
    if existing is None:
        customer = session.get(Customer, order.customer_id) if order.customer_id else None
        existing = Shipment(tenant_id=order.tenant_id, sales_order_id=order.id, order_ref=order.order_no,
            carrier=None, destination=(customer.address or customer.region)[:160] if customer and (customer.address or customer.region) else None,
            status="Pending")
        session.add(existing)
        session.flush()
        existing.shipment_no = f"SHP-{9900 + existing.id}"
        for line in lines_for(session, order):
            session.add(ShipmentLine(tenant_id=order.tenant_id, shipment_id=existing.id,
                sku=line.sku, description=" · ".join(v for v in [line.name, line.color, line.size] if v), qty=line.qty))
    order.status = "Shipped"
    session.flush()
    return existing


def fulfillment_detail(session, order):
    if not order.fulfillment_tracked:
        return None
    rows = stock_summary(session, order)
    pos = purchase_orders(session, order)
    shipment = session.scalar(select(Shipment).where(Shipment.sales_order_id == order.id,
        Shipment.tenant_id == order.tenant_id, Shipment.is_deleted.is_(False)))
    return {"lines": rows, "canCreatePO": order.status == "PO needed" and not pos and any(r["missing"] for r in rows),
        "purchaseOrders": [{"publicId": str(po.public_id), "poNo": po.po_no} for po in pos],
        "shipment": {"publicId": str(shipment.public_id), "shipmentNo": shipment.shipment_no} if shipment else None}


def create_shortage_po(session, *, tenant_id, public_id, supplier_id):
    order = lock_order(session, tenant_id=tenant_id, public_id=public_id)
    if not order.fulfillment_tracked or order.status in ("Cancelled", "Shipped"):
        raise HTTPException(409, "This order cannot create a shortage PO.")
    existing = purchase_orders(session, order)
    if existing:
        return existing[0]
    supplier = session.scalar(select(Supplier).where(Supplier.public_id == UUID(str(supplier_id)),
        Supplier.tenant_id == order.tenant_id, Supplier.is_deleted.is_(False)))
    if supplier is None:
        raise HTTPException(404, "Supplier not found")
    reserve_available(session, order)
    source_lines = {ln.id: ln for ln in lines_for(session, order)}
    shortages = [r for r in stock_summary(session, order) if r["missing"]]
    if not shortages:
        refresh_order(session, order)
        return None
    lines = []
    for row in shortages:
        variant = variant_for(session, order, source_lines[row["lineId"]])
        if variant is None or variant.supplier_price is None:
            raise HTTPException(409, f"Set a catalog variant and supplier purchase price for {row['name']} ({row['color'] or 'no color'}, {row['size'] or 'no size'}) before creating its PO.")
        lines.append({"name": row["name"], "color": row["color"], "size": row["size"],
            "sku": variant.sku, "qty": row["missing"], "price": variant.supplier_price})
    from app.modules.procurement.dto import PurchaseOrderCreate
    from app.modules.procurement import service as procurement
    lead = re.search(r"\d+", supplier.lead_time or "")
    days = int(lead.group()) if lead else 30
    if lead and "week" in supplier.lead_time.lower():
        days *= 7
    days = min(days, 3650)
    po = procurement.create_po(session, tenant_id=order.tenant_id, payload=PurchaseOrderCreate(
        supplier=supplier.name, expected=(date.today() + timedelta(days=days)).strftime("%d %b %Y"), lines=lines))
    po.sales_order_id = order.id
    po_lines = session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id,
        PurchaseOrderLine.tenant_id == order.tenant_id).order_by(PurchaseOrderLine.id)).all()
    for source, target in zip(shortages, po_lines):
        target.sales_order_line_id = source["lineId"]
    order.status = "Awaiting stock"
    session.flush()
    return po


def complete_shipment(session, order):
    if order.status == "Cancelled":
        raise HTTPException(409, "A cancelled order cannot ship.")
    if order.status == "Shipped" and not holds_for(session, order):
        return
    if any(row["reserved"] < row["ordered"] for row in stock_summary(session, order)):
        raise HTTPException(409, "The order is waiting for stock, final QC or inventory receipt.")
    release_holds(session, order, consume=True)
    order.status = "Shipped"
    session.flush()


def receive_inspected_production(session, *, tenant_id, order_ref):
    """Receive any cleared procurement PO once, then allocate stock to waiting orders."""
    tid = UUID(str(tenant_id))
    po = session.scalar(select(PurchaseOrder).join(ProductionOrder,
        ProductionOrder.purchase_order_id == PurchaseOrder.id).where(
        ProductionOrder.order_no == order_ref, ProductionOrder.tenant_id == tid,
        ProductionOrder.is_deleted.is_(False), PurchaseOrder.tenant_id == tid,
        PurchaseOrder.is_deleted.is_(False)))
    if po is None:
        return None
    # Lock the source and other waiting orders before taking any stock locks.
    # Stable order prevents competing QC receipts from allocating the same units.
    orders = session.scalars(select(SalesOrder).where(SalesOrder.tenant_id == tid,
        SalesOrder.is_deleted.is_(False), SalesOrder.fulfillment_tracked.is_(True),
        (SalesOrder.status.in_(["Awaiting stock", "PO needed"])) | (SalesOrder.id == po.sales_order_id)
    ).order_by(SalesOrder.id).with_for_update().execution_options(populate_existing=True)).all()
    source = next((order for order in orders if order.id == po.sales_order_id), None)
    if (source is not None and source.status == "Cancelled") or not qc_cleared(session, po):
        return None
    from app.modules.procurement import service as procurement
    from app.modules.procurement.dto import GoodsReceiptCreate
    count = session.scalar(select(func.count()).select_from(PurchaseOrderLine).where(
        PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.tenant_id == tid))
    procurement.create_receipt(session, tenant_id=tid, payload=GoodsReceiptCreate(
        po=po.po_no, supplier=po.supplier_name, lines=count))
    # The source order receives its manufactured units first. Other orders can
    # reserve only stock left available, in oldest-order-first order.
    shipment = refresh_order(session, source) if source is not None else None
    for order in orders:
        if source is None or order.id != source.id:
            refresh_order(session, order)
    if shipment:
        for ins in session.scalars(select(Inspection).where(Inspection.tenant_id == tid,
            Inspection.order_ref == order_ref, Inspection.stage == "Final", Inspection.result == "Pass",
            Inspection.is_deleted.is_(False))).all():
            ins.shipment_ref = shipment.shipment_no
        session.flush()
    return shipment

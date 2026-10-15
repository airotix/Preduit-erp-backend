"""Quantity-based ordinary shipments; batch stock keeps its existing workflow."""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func, select
from app.models.sales import SalesOrderLine, Customer
from app.models.shipments import Shipment, ShipmentLine
from app.models.inventory import StockLevel
from app.models.catalog import ProductVariant


def dispatched(session, order):
    query = select(ShipmentLine.order_line_id, func.sum(ShipmentLine.qty)).join(Shipment,
        Shipment.id == ShipmentLine.shipment_id).where(Shipment.tenant_id == order.tenant_id,
        ShipmentLine.tenant_id == order.tenant_id, Shipment.sales_order_id == order.id,
        Shipment.batch_managed.is_(False), Shipment.inventory_dispatched.is_(True),
        Shipment.is_deleted.is_(False), ShipmentLine.order_line_id.is_not(None)).group_by(ShipmentLine.order_line_id)
    totals = dict(session.execute(query).all())
    # Pre-migration ordinary shipments represented the entire order. Preserve
    # that historical fact when old contents could not be matched unambiguously.
    legacy = session.scalar(select(Shipment.id).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id).where(
        Shipment.tenant_id == order.tenant_id, ShipmentLine.tenant_id == order.tenant_id,
        Shipment.sales_order_id == order.id, Shipment.batch_managed.is_(False), Shipment.inventory_dispatched.is_(True),
        Shipment.is_deleted.is_(False), ShipmentLine.order_line_id.is_(None)).limit(1))
    if legacy:
        return {l.id: l.qty for l in session.scalars(select(SalesOrderLine).where(
            SalesOrderLine.tenant_id == order.tenant_id, SalesOrderLine.order_id == order.id))}
    return totals


def active(session, order):
    return session.scalar(select(Shipment).where(Shipment.tenant_id == order.tenant_id, Shipment.sales_order_id == order.id,
        Shipment.batch_managed.is_(False), Shipment.is_deleted.is_(False), Shipment.inventory_dispatched.is_(False),
        Shipment.status != "Cancelled").order_by(Shipment.id))


def refresh(session, order):
    from app.modules.sales import fulfillment as f
    if order.status in ("Cancelled", "Inactive"):
        return None
    rows = f.stock_summary(session, order)
    sent = any(r["dispatched"] for r in rows)
    if rows and all(r["outstanding"] == 0 for r in rows):
        order.status = "Shipped"
        session.flush()
        return None
    f.reserve_available(session, order)
    rows = f.stock_summary(session, order)
    current = active(session, order)
    if current is None and (all(r["missing"] == 0 for r in rows) or
            (order.allow_partial_shipments and any(r["reserved"] for r in rows))):
        customer = session.get(Customer, order.customer_id) if order.customer_id else None
        current = Shipment(tenant_id=order.tenant_id, sales_order_id=order.id, order_ref=order.order_no,
            carrier=None, destination=(customer.address or customer.region)[:160] if customer and (customer.address or customer.region) else None,
            status="Pending")
        session.add(current)
        session.flush()
        current.shipment_no = f"SHP-{9900 + current.id}"
        source = {l.id: l for l in f.lines_for(session, order)}
        for row in rows:
            quantity = min(row["reserved"], row["outstanding"])
            if quantity:
                l = source[row["lineId"]]
                session.add(ShipmentLine(tenant_id=order.tenant_id, shipment_id=current.id,
                    order_line_id=l.id, sku=l.sku,
                    description=" · ".join(v for v in [l.name, l.color, l.size] if v), qty=quantity))
        session.flush()
    if sent:
        order.status = "Partially shipped"
    elif current:
        order.status = "Packed" if current.packed_at else "Ready to pack"
    else:
        order.status = "PO needed" if any(r["toPurchase"] for r in f.procurement_summary(session, order)) else "Awaiting stock"
    session.flush()
    return current


def contents(session, order, shipment):
    from app.modules.sales import fulfillment as f
    lines = session.scalars(select(ShipmentLine).where(ShipmentLine.tenant_id == order.tenant_id,
        ShipmentLine.shipment_id == shipment.id).order_by(ShipmentLine.id)).all()
    source = {l.id: l for l in f.lines_for(session, order)}
    # An undispatched legacy full-order shipment can be reconciled from its
    # original contents. Ambiguous rows require reconciliation, never guessing.
    for line in lines:
        if line.order_line_id is None:
            matches = [l for l in source.values() if line.description == " · ".join(v for v in [l.name, l.color, l.size] if v)]
            if len(matches) != 1:
                raise HTTPException(409, "Legacy shipment contents need reconciliation before packing")
            line.order_line_id = matches[0].id
    totals = {}
    for line in lines:
        if line.order_line_id not in source or line.qty <= 0:
            raise HTTPException(409, "Shipment contains invalid order lines")
        totals[line.order_line_id] = totals.get(line.order_line_id, 0) + line.qty
    if not totals:
        raise HTTPException(409, "Shipment has no quantities to pack")
    summary = {r["lineId"]: r for r in f.stock_summary(session, order)}
    if any(qty > summary[lid]["reserved"] or qty > summary[lid]["outstanding"] for lid, qty in totals.items()):
        raise HTTPException(409, "Shipment exceeds the reserved or outstanding quantities")
    if not order.allow_partial_shipments and any(totals.get(lid, 0) != row["outstanding"] for lid, row in summary.items()):
        raise HTTPException(409, "This order requires a complete shipment")
    return totals


def pack(session, order, shipment, payload, actor):
    if shipment.inventory_dispatched or shipment.status == "Cancelled":
        raise HTTPException(409, "This shipment cannot be packed")
    if shipment.packed_at:
        return shipment
    if payload.lines:
        requested = {l.orderLineId: l.quantity for l in payload.lines}
        if len(requested) != len(payload.lines) or not any(requested.values()):
            raise HTTPException(422, "Choose distinct order lines and at least one quantity")
        source = {l.id: l for l in session.scalars(select(SalesOrderLine).where(
            SalesOrderLine.tenant_id == order.tenant_id, SalesOrderLine.order_id == order.id))}
        if any(lid not in source for lid in requested):
            raise HTTPException(422, "Order line not found")
        for old in session.scalars(select(ShipmentLine).where(ShipmentLine.tenant_id == order.tenant_id, ShipmentLine.shipment_id == shipment.id)):
            session.delete(old)
        for lid, qty in requested.items():
            if qty:
                l = source[lid]
                session.add(ShipmentLine(tenant_id=order.tenant_id, shipment_id=shipment.id, order_line_id=lid,
                    sku=l.sku, description=" · ".join(v for v in [l.name, l.color, l.size] if v), qty=qty))
        session.flush()
    contents(session, order, shipment)
    shipment.packed_at, shipment.packed_by = datetime.now(timezone.utc), actor
    session.flush()
    refresh(session, order)
    return shipment


def dispatch(session, order, shipment, actor):
    from app.modules.sales import fulfillment as f
    from app.modules.inventory import ledger
    if not shipment.packed_at:
        raise HTTPException(409, "Confirm the actual packed quantities before dispatch")
    needed = contents(session, order, shipment)
    holds = f.holds_for(session, order)
    stock = {row.id: row for row in session.scalars(select(StockLevel).where(
        StockLevel.tenant_id == order.tenant_id, StockLevel.id.in_([h.stock_level_id for h in holds]))
        .order_by(StockLevel.id).with_for_update().execution_options(populate_existing=True))}
    before = {lid: row.on_hand for lid, row in stock.items()}
    for hold in holds:
        qty = min(hold.qty, needed.get(hold.order_line_id, 0))
        if not qty:
            continue
        row = stock[hold.stock_level_id]
        if row.reserved < qty or row.on_hand < qty:
            raise HTTPException(409, "Reserved stock has changed; reconcile before dispatch")
        row.on_hand -= qty
        row.reserved -= qty
        hold.qty -= qty
        needed[hold.order_line_id] -= qty
        if not hold.qty:
            session.delete(hold)
    if any(needed.values()):
        raise HTTPException(409, "Not enough order-owned stock to dispatch these quantities")
    session.flush()
    for row in stock.values():
        if row.on_hand != before[row.id]:
            ledger.record(session, level=row, before=before[row.id], kind="Dispatch", request_id=shipment.public_id,
                reference=shipment.shipment_no, reason=f"Dispatched for {order.order_no}", actor=actor,
                source_module="shipments", source_type="shipment", source_id=shipment.public_id)
    for vid in {row.variant_id for row in stock.values()}:
        v = session.get(ProductVariant, vid)
        v.qty_on_hand = session.scalar(select(func.coalesce(func.sum(StockLevel.on_hand), 0)).where(
            StockLevel.tenant_id == order.tenant_id, StockLevel.variant_id == vid)) or 0
    shipment.inventory_dispatched = True
    shipment.dispatched_at = datetime.now(timezone.utc)
    session.flush()
    refresh(session, order)

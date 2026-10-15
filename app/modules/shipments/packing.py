from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from pydantic import BaseModel, Field
from app.models.shipments import Shipment, ShipmentLine
from app.models.sales import SalesOrder
from app.modules.sales import fulfillment as f, ordinary_shipping as shipping
from app.modules.inventory import workflows as flow


class PackLine(BaseModel):
    orderLineId: int = Field(gt=0)
    quantity: int = Field(ge=0)


class PackInput(BaseModel):
    requestId: UUID
    reason: str = Field(default="Physical packing confirmation", min_length=3, max_length=400)
    lines: list[PackLine] = Field(default_factory=list, max_length=500)


class PolicyInput(BaseModel):
    requestId: UUID
    allowPartialShipments: bool
    reason: str = Field(default="Update shipment policy", min_length=3, max_length=400)


def source(session, tid, pid, lock=False):
    s = session.scalar(select(Shipment).where(Shipment.tenant_id == tid, Shipment.public_id == pid,
        Shipment.is_deleted.is_(False)))
    if not s or not s.sales_order_id:
        raise HTTPException(404, "Linked sales shipment not found")
    order = f.lock_order(session, tenant_id=tid, order_id=s.sales_order_id) if lock else session.get(SalesOrder, s.sales_order_id)
    from app.modules.batches.service import managed_sales
    if s.batch_managed or managed_sales(session, order):
        raise HTTPException(409, "Use the batch shipment workspace for this order")
    if lock:
        s = session.scalar(select(Shipment).where(Shipment.id == s.id, Shipment.tenant_id == tid)
            .with_for_update().execution_options(populate_existing=True))
    return order, s


def detail(session, tid, pid):
    order, s = source(session, tid, pid)
    packed = {}
    for l in session.scalars(select(ShipmentLine).where(ShipmentLine.tenant_id == tid, ShipmentLine.shipment_id == s.id)):
        packed[l.order_line_id] = packed.get(l.order_line_id, 0) + l.qty
    return {"shipment": s.shipment_no, "order": order.order_no, "status": s.status,
        "packedAt": s.packed_at.isoformat() if s.packed_at else None, "packedBy": s.packed_by,
        "dispatched": s.inventory_dispatched, "allowPartialShipments": order.allow_partial_shipments,
        "lines": [{**r, "quantity": packed.get(r["lineId"], 0)} for r in f.stock_summary(session, order)]}


def pack(session, tid, pid, payload, principal):
    order, s = source(session, tid, pid, True)
    if flow.replay(session, tid, pid, "Shipment packed", payload):
        return detail(session, tid, pid)
    if order.status in ("Cancelled", "Inactive"):
        raise HTTPException(409, "This order is closed")
    if s.packed_at:
        raise HTTPException(409, "Packing is already confirmed; its quantities cannot be changed")
    who = flow.actor(principal)
    shipping.pack(session, order, s, payload, who)
    flow.event(session, tid, pid, "Shipment packed", payload, who)
    return detail(session, tid, pid)


def policy(session, tid, pid, payload, principal):
    order = f.lock_order(session, tenant_id=tid, public_id=pid)
    if flow.replay(session, tid, pid, "Shipment policy", payload):
        return {"allowPartialShipments": order.allow_partial_shipments}
    from app.modules.batches.service import managed_sales
    if managed_sales(session, order) or order.status in ("Shipped", "Cancelled", "Inactive"):
        raise HTTPException(409, "This order's shipment policy cannot be changed")
    shipments = session.scalars(select(Shipment).where(Shipment.tenant_id == tid, Shipment.sales_order_id == order.id,
        Shipment.is_deleted.is_(False), Shipment.status != "Cancelled").with_for_update()).all()
    if any(s.packed_at or s.inventory_dispatched for s in shipments):
        raise HTTPException(409, "Set the shipment policy before packing or dispatch")
    order.allow_partial_shipments = payload.allowPartialShipments
    for s in shipments:
        s.status = "Cancelled"
    session.flush()
    f.refresh_order(session, order)
    flow.event(session, tid, pid, "Shipment policy", payload, flow.actor(principal))
    return {"allowPartialShipments": order.allow_partial_shipments}

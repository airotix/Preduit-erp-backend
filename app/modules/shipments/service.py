"""Shipments business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.production import ProductionOrder
from app.models.sales import SalesOrder
from app.models.shipments import Shipment
from app.modules.shipments import repository as repo
from app.modules.shipments.dto import CarrierCreate, CarrierUpdate, ShipmentCreate
from app.presenters.screen import list_config, text_cell

_SHIP_TONE = {"Pending": "neutral", "Label created": "neutral", "Dispatched": "amber", "In transit": "amber",
              "Customs": "navy", "Out for delivery": "amber", "Delivered": "green"}
_CARRIER_TONE = {"Active": "green", "Paused": "amber", "Inactive": "red"}

# Tracking sequence used to build the timeline.
_TRACK = [
    ("Label created", "package"),
    ("Dispatched", "truck"),
    ("In transit", "truck"),
    ("Customs", "building-2"),
    ("Out for delivery", "map-pin"),
    ("Delivered", "check-circle-2"),
]


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def shipments_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_shipments(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["shipment_no"] or "—", strong=True, mono=True),
            r["order_ref"] or "—",
            r["carrier"] or "—",
            r["destination"] or "—",
            text_cell(r["status"], badge=_SHIP_TONE.get(r["status"], "neutral")),
            r["eta"] or "—",
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Shipment"}, {"label": "Order"}, {"label": "Carrier"},
                 {"label": "Destination"}, {"label": "Status"}, {"label": "ETA"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search shipments…", action="New shipment", filters=["Carrier", "Status"],
    )


def create_shipment(session, *, tenant_id, payload: ShipmentCreate):
    from app.modules.sales import fulfillment
    from fastapi import HTTPException
    order = session.scalar(select(SalesOrder).where(SalesOrder.order_no == payload.order,
        SalesOrder.tenant_id == _tid(tenant_id), SalesOrder.is_deleted.is_(False)).with_for_update())
    if order and order.status in ("Cancelled", "Inactive"):
        raise HTTPException(409, "A cancelled or inactive order cannot create shipments.")
    if order and order.fulfillment_tracked:
        shipment = fulfillment.refresh_order(session, order)
        if shipment is None:
            raise HTTPException(409, "This order is waiting for stock, final QC or inventory receipt.")
        shipment.carrier, shipment.destination = payload.carrier, payload.destination
        session.flush()
        return shipment
    return repo.create_shipment(session, tenant_id=_tid(tenant_id), order_ref=payload.order,
                                carrier=payload.carrier, destination=payload.destination)


def _shipment_order(session, shipment):
    query = select(SalesOrder).where(SalesOrder.tenant_id == shipment.tenant_id,
        SalesOrder.is_deleted.is_(False))
    if shipment.sales_order_id:
        return session.scalar(query.where(SalesOrder.id == shipment.sales_order_id))
    order = session.scalar(query.where(SalesOrder.order_no == shipment.order_ref))
    if order is None:
        work = session.scalar(select(ProductionOrder).where(ProductionOrder.tenant_id == shipment.tenant_id,
            ProductionOrder.order_no == shipment.order_ref, ProductionOrder.is_deleted.is_(False)))
        if work and work.sales_order_id:
            order = session.scalar(query.where(SalesOrder.id == work.sales_order_id))
    return order


def search_carriers(session, *, q, limit: int = 50):
    return repo.search_carriers(session, q=q or "", limit=limit)


def _transition(session, *, public_id, status, actor=None):
    from datetime import datetime, timezone
    from app.core.audit import write_audit
    from app.modules.batches import service as batches
    from app.modules.sales import fulfillment
    allowed = {"Pending", "Label created", "Dispatched", "In transit", "Customs", "Out for delivery", "Delivered"}
    if status not in allowed:
        raise HTTPException(422, "Choose a valid shipment status. Cancel through the source order before dispatch.")
    shipment = session.scalar(select(Shipment).where(Shipment.public_id == UUID(str(public_id)),
        Shipment.is_deleted.is_(False)))
    if shipment is None:
        return None
    if batches.is_batch_shipment(session, shipment):
        raise HTTPException(409, "Use the batch shipment dispatch controls to record quantities and history.")
    # Order locks precede shipment and stock locks, matching sales cancellation.
    source = _shipment_order(session, shipment)
    order = fulfillment.lock_order(session, tenant_id=shipment.tenant_id, order_id=source.id) if source else None
    shipment = session.scalar(select(Shipment).where(Shipment.id == shipment.id,
        Shipment.tenant_id == shipment.tenant_id).with_for_update().execution_options(populate_existing=True))
    if shipment.status == "Cancelled" or (order and order.status in ("Cancelled", "Inactive")):
        raise HTTPException(409, "A cancelled or inactive order cannot ship.")
    if shipment.inventory_dispatched and status == "Dispatched":
        return shipment  # A delayed dispatch retry must not reset tracking.
    if shipment.status == "Delivered" and status != "Delivered":
        raise HTTPException(409, "A delivered shipment cannot be reopened.")
    if shipment.inventory_dispatched and status in ("Pending", "Label created"):
        raise HTTPException(409, "These goods have left the warehouse. Dispatch cannot be undone by changing status.")
    if status in ("In transit", "Customs", "Out for delivery", "Delivered") and not shipment.inventory_dispatched:
        raise HTTPException(409, "Confirm Dispatched first to record that the goods physically left the warehouse.")
    if status == "Dispatched" and not shipment.inventory_dispatched:
        if order is None or not order.fulfillment_tracked:
            raise HTTPException(409, "Link this shipment to a sales order with reconciled stock reservations before dispatch.")
        if batches.managed_sales(session, order):
            raise HTTPException(409, "Use this order's batch shipment controls to preserve its batch stock records.")
        if not fulfillment.holds_for(session, order):
            raise HTTPException(409, "No warehouse stock is reserved for this shipment. Reconcile the order before dispatch.")
        fulfillment.complete_shipment(session, order, shipment=shipment, actor=actor)
        shipment.sales_order_id = order.id
        shipment.inventory_dispatched = True
        shipment.dispatched_at = datetime.now(timezone.utc)
        write_audit(session, tenant_id=shipment.tenant_id, action="DISPATCH", entity_type="shipment",
            entity_id=str(shipment.public_id), detail=f"{shipment.shipment_no}: warehouse stock dispatched for {order.order_no}; {actor or 'user'}")
    if status == "Delivered" and shipment.delivered_at is None:
        shipment.delivered_at = datetime.now(timezone.utc)
    shipment.status = status
    session.flush()
    return shipment


def set_status(session, *, public_id, status, actor=None):
    with session.begin_nested():
        return _transition(session, public_id=public_id, status=status, actor=actor)


def update_shipment(session, *, public_id, payload, actor=None):
    with session.begin_nested():
        shipment = _transition(session, public_id=public_id, status=payload.status, actor=actor)
        if shipment:
            shipment.carrier, shipment.destination, shipment.eta = payload.carrier, payload.destination, payload.eta
            session.flush()
        return shipment


def shipment_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_shipment_detail(session, public_id=public_id)
    if data is None:
        return None
    s, lines = data["shipment"], data["lines"]
    from app.modules.batches.service import managed_sales
    source = _shipment_order(session, s)
    order = [name for name, _ in _TRACK]
    idx = order.index(s.status) if s.status in order else 0
    timeline = []
    for i, (name, icon) in enumerate(_TRACK):
        done = i < idx
        current = i == idx and s.status != "Delivered"
        if s.status == "Delivered" and name == "Delivered":
            done, current = True, False
        time = "Done" if done else ("Now" if current else "Pending")
        if name == s.status and name == "Delivered":
            time = s.eta or "Delivered"
        timeline.append({
            "icon": icon,
            "tone": "green" if done or (current and s.status == "Delivered") else ("amber" if current else "neutral"),
            "title": name, "time": time, "done": done or current,
        })
    return {
        "variant": "shipment",
        "ref": s.shipment_no or "—",
        "title": f"{s.carrier or 'Shipment'} → {s.destination or '—'}",
        "statusLabel": s.status,
        "statusTone": _SHIP_TONE.get(s.status, "neutral"),
        "meta": [
            {"k": "Order", "v": s.order_ref or "—"},
            {"k": "Carrier", "v": s.carrier or "—"},
            {"k": "Destination", "v": s.destination or "—"},
            {"k": "ETA", "v": s.eta or "—"},
        ],
        "tabs": ["Tracking", "Contents", "Documents"],
        "shipment": {
            "tracking": timeline,
            "inventoryDispatched": s.inventory_dispatched,
            "ordinaryPacking": bool(source and source.fulfillment_tracked and not s.batch_managed and not managed_sales(session, source)),
            "packedAt": s.packed_at.isoformat() if s.packed_at else None,
            "packedBy": s.packed_by,
            "dispatchedAt": s.dispatched_at.isoformat() if s.dispatched_at else None,
            "deliveredAt": s.delivered_at.isoformat() if s.delivered_at else None,
            "contents": [
                {"name": ln["description"], "sku": ln["sku"] or "—", "qty": ln["qty"]}
                for ln in lines
            ],
            "form": None if s.batch_managed else {
                "carrier": s.carrier or "", "destination": s.destination or "",
                "eta": s.eta or "", "status": s.status,
            },
            "statusOptions": ["Pending"] + [name for name, _ in _TRACK],
        },
    }


# ---------- Carriers ----------

def carriers_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_carriers(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], strong=True),
            r["service"] or "—",
            r["avg_transit"] or "—",
            text_cell(f"{r['on_time_pct']}%", align="right", mono=True),
            text_cell(r["status"], badge=_CARRIER_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Carrier"}, {"label": "Service"}, {"label": "Avg transit"},
                 {"label": "On-time", "align": "right"}, {"label": "Status"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "service": r["service"], "avgTransit": r["avg_transit"]}
                 for r in rows],
        search="Search carriers…", action="New carrier", filters=["Service", "Status"],
    )


def _carrier_fields(p) -> dict:
    return {"name": p.name, "service": p.service, "avg_transit": p.avgTransit}


def create_carrier(session, *, tenant_id, payload: CarrierCreate):
    return repo.create_carrier(session, tenant_id=_tid(tenant_id), **_carrier_fields(payload))


def update_carrier(session, *, public_id, payload: CarrierUpdate):
    return repo.update_carrier(session, public_id=public_id, **_carrier_fields(payload))

"""Shipments business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.production import ProductionOrder
from app.models.sales import SalesOrder
from app.modules.shipments import repository as repo
from app.modules.shipments.dto import CarrierCreate, CarrierUpdate, ShipmentCreate
from app.presenters.screen import list_config, text_cell

_SHIP_TONE = {"Label created": "neutral", "In transit": "amber",
              "Customs": "navy", "Out for delivery": "amber", "Delivered": "green"}
_CARRIER_TONE = {"Active": "green", "Paused": "amber", "Inactive": "red"}

# Tracking sequence used to build the timeline.
_TRACK = [
    ("Label created", "package"),
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
    return repo.create_shipment(session, tenant_id=_tid(tenant_id), order_ref=payload.order,
                                carrier=payload.carrier, destination=payload.destination)


def _mark_sales_order_shipped(session: Session, order_ref: str | None) -> None:
    """When a shipment is Delivered, flip its sales order to 'Shipped'. The
    shipment's order_ref may be the sales order number directly, or a production
    order number that links back to a sales order."""
    if not order_ref:
        return
    so = session.execute(
        select(SalesOrder).where(SalesOrder.order_no == order_ref,
                                 SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if so is None:
        # Resolve via the production order (order_ref → production → sales order).
        po = session.execute(
            select(ProductionOrder).where(ProductionOrder.order_no == order_ref,
                                          ProductionOrder.is_deleted == False)  # noqa: E712
        ).scalar_one_or_none()
        if po is not None and po.sales_order_id:
            so = session.get(SalesOrder, po.sales_order_id)
    if so is not None and so.status != "Shipped":
        so.status = "Shipped"
        session.flush()
        # Relieve inventory: reduce on-hand and release the reservation for each
        # order line now that the goods have shipped.
        from app.models.sales import SalesOrderLine
        from app.modules.inventory import service as inv_service
        line_rows = session.execute(
            select(SalesOrderLine).where(SalesOrderLine.order_id == so.id)
        ).scalars().all()
        lines = [{"sku": ln.sku, "name": ln.name, "color": ln.color,
                  "size": ln.size, "qty": ln.qty} for ln in line_rows]
        inv_service.relieve_order_stock(session, tenant_id=so.tenant_id, lines=lines)


def search_carriers(session, *, q, limit: int = 50):
    return repo.search_carriers(session, q=q or "", limit=limit)


def set_status(session, *, public_id, status):
    s = repo.set_status(session, public_id=public_id, status=status)
    if s is not None and status == "Delivered":
        _mark_sales_order_shipped(session, s.order_ref)
    return s


def update_shipment(session, *, public_id, payload):
    s = repo.update_shipment(session, public_id=public_id, carrier=payload.carrier,
                             destination=payload.destination, eta=payload.eta, status=payload.status)
    if s is not None and payload.status == "Delivered":
        _mark_sales_order_shipped(session, s.order_ref)
    return s


def shipment_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_shipment_detail(session, public_id=public_id)
    if data is None:
        return None
    s, lines = data["shipment"], data["lines"]
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
            "contents": [
                {"name": ln["description"], "sku": ln["sku"] or "—", "qty": ln["qty"]}
                for ln in lines
            ],
            "form": {
                "carrier": s.carrier or "", "destination": s.destination or "",
                "eta": s.eta or "", "status": s.status,
            },
            "statusOptions": [name for name, _ in _TRACK],
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

"""Shipments data access (tenant-scoped by RLS)."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.shipments import Carrier, Shipment, ShipmentLine


# ---------- Shipments ----------

def list_shipments(session, *, limit, offset):
    stmt = (
        select(Shipment.public_id, Shipment.shipment_no, Shipment.order_ref, Shipment.carrier,
               Shipment.destination, Shipment.status, Shipment.eta)
        .where(Shipment.is_deleted == False)  # noqa: E712
        .order_by(Shipment.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Shipment).where(Shipment.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_shipment(session: Session, *, tenant_id: UUID, order_ref, carrier, destination) -> Shipment:
    s = Shipment(tenant_id=tenant_id, order_ref=order_ref, carrier=carrier,
                 destination=destination, status="Label created")
    session.add(s)
    session.flush()
    s.shipment_no = f"SHP-{9900 + s.id}"
    session.flush()
    session.refresh(s)
    return s


def set_status(session: Session, *, public_id: str, status: str) -> Shipment | None:
    s = session.execute(
        select(Shipment).where(Shipment.public_id == public_id,
                               Shipment.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if s is None:
        return None
    s.status = status
    session.flush()
    return s


def get_shipment_detail(session: Session, *, public_id: str) -> dict | None:
    s = session.execute(
        select(Shipment).where(Shipment.public_id == public_id,
                               Shipment.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if s is None:
        return None
    lines = [dict(r._mapping) for r in session.execute(
        select(ShipmentLine.sku, ShipmentLine.description, ShipmentLine.qty)
        .where(ShipmentLine.shipment_id == s.id)
    )]
    return {"shipment": s, "lines": lines}


# ---------- Carriers ----------

def list_carriers(session, *, limit, offset):
    stmt = (
        select(Carrier.public_id, Carrier.name, Carrier.service, Carrier.avg_transit,
               Carrier.on_time_pct, Carrier.status)
        .where(Carrier.is_deleted == False)  # noqa: E712
        .order_by(Carrier.name).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Carrier).where(Carrier.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def _apply_carrier(c: Carrier, *, name, service, avg_transit) -> None:
    c.name = name
    c.service = service
    c.avg_transit = avg_transit


def create_carrier(session: Session, *, tenant_id: UUID, **fields) -> Carrier:
    c = Carrier(tenant_id=tenant_id, status="Active")
    _apply_carrier(c, **fields)
    session.add(c)
    session.flush()
    session.refresh(c)
    return c


def update_carrier(session: Session, *, public_id: str, **fields) -> Carrier | None:
    c = session.execute(
        select(Carrier).where(Carrier.public_id == public_id,
                              Carrier.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if c is None:
        return None
    _apply_carrier(c, **fields)
    session.flush()
    session.refresh(c)
    return c

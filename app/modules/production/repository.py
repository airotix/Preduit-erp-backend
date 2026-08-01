"""Production data access (tenant-scoped by RLS)."""
import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.production import BomLine, ProductionOrder, ProductionStage
from app.models.shipments import Shipment


# ---------- Production orders ----------

def list_porders(session, *, limit, offset):
    stage_count = (
        select(func.count()).select_from(ProductionStage)
        .where(ProductionStage.order_id == ProductionOrder.id,
               ProductionStage.is_deleted == False)  # noqa: E712
        .correlate(ProductionOrder).scalar_subquery()
    )
    done_count = (
        select(func.count()).select_from(ProductionStage)
        .where(ProductionStage.order_id == ProductionOrder.id,
               ProductionStage.is_deleted == False, ProductionStage.status == "Completed")  # noqa: E712
        .correlate(ProductionOrder).scalar_subquery()
    )
    shipped_count = (
        select(func.count()).select_from(Shipment)
        .where(Shipment.order_ref == ProductionOrder.order_no,
               Shipment.is_deleted == False)  # noqa: E712
        .correlate(ProductionOrder).scalar_subquery()
    )
    stmt = (
        select(ProductionOrder.public_id, ProductionOrder.order_no, ProductionOrder.style,
               ProductionOrder.factory, ProductionOrder.qty, ProductionOrder.stage,
               ProductionOrder.progress, stage_count.label("stage_count"),
               done_count.label("done_count"), shipped_count.label("shipped_count"))
        .where(ProductionOrder.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrder.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    for r in rows:
        total = r.get("stage_count") or 0
        r["started"] = total > 0
        r["shippable"] = total > 0 and (r.get("done_count") or 0) == total
        r["shipped"] = (r.get("shipped_count") or 0) > 0
    total = session.execute(
        select(func.count()).select_from(ProductionOrder).where(ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def list_porders_all(session):
    return [dict(r._mapping) for r in session.execute(
        select(ProductionOrder.order_no, ProductionOrder.style, ProductionOrder.factory,
               ProductionOrder.qty, ProductionOrder.stage, ProductionOrder.progress)
        .where(ProductionOrder.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrder.id.desc())
    )]


def create_porder(session: Session, *, tenant_id: UUID, style, factory, qty) -> ProductionOrder:
    po = ProductionOrder(tenant_id=tenant_id, style=style, factory=factory, qty=qty,
                         stage="Trims", progress=0)
    session.add(po)
    session.flush()
    po.order_no = f"MO-{3300 + po.id}"
    session.flush()
    session.refresh(po)
    return po


def set_stage(session: Session, *, public_id: str, stage: str) -> ProductionOrder | None:
    po = session.execute(
        select(ProductionOrder).where(ProductionOrder.public_id == public_id,
                                      ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if po is None:
        return None
    po.stage = stage
    if stage == "Completed":
        po.progress = 100
    session.flush()
    return po


# ---------- Bill of materials ----------

def list_bom(session, *, limit, offset):
    stmt = (
        select(BomLine.public_id, BomLine.component, BomLine.style, BomLine.material,
               BomLine.qty_per_unit, BomLine.cost)
        .where(BomLine.is_deleted == False)  # noqa: E712
        .order_by(BomLine.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(BomLine).where(BomLine.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def get_porder_detail(session: Session, *, public_id: str) -> dict | None:
    po = session.execute(
        select(ProductionOrder).where(ProductionOrder.public_id == public_id,
                                      ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if po is None:
        return None
    materials = [dict(r._mapping) for r in session.execute(
        select(BomLine.component, BomLine.material, BomLine.qty_per_unit, BomLine.cost)
        .where(BomLine.style == po.style, BomLine.is_deleted == False)  # noqa: E712
    )]
    return {"order": po, "materials": materials, "stages": list_stages(session, po.id)}


# ---------- Production stage timeline ----------

def order_by_public(session: Session, public_id: str) -> ProductionOrder | None:
    return session.execute(
        select(ProductionOrder).where(ProductionOrder.public_id == public_id,
                                      ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def list_stages(session: Session, order_id: int) -> list[ProductionStage]:
    return list(session.execute(
        select(ProductionStage).where(ProductionStage.order_id == order_id,
                                      ProductionStage.is_deleted == False)  # noqa: E712
        .order_by(ProductionStage.seq)
    ).scalars())


def get_stage(session: Session, *, public_id: str) -> ProductionStage | None:
    return session.execute(
        select(ProductionStage).where(ProductionStage.public_id == public_id,
                                      ProductionStage.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def _recompute_order(session: Session, order_id: int) -> None:
    stages = list_stages(session, order_id)
    if not stages:
        return
    po = session.get(ProductionOrder, order_id)
    completed = sum(1 for s in stages if s.status == "Completed")
    po.progress = round(completed / len(stages) * 100)
    current = next((s for s in stages if s.status != "Completed"), None)
    po.stage = current.name if current else stages[-1].name


def _shift_downstream(session: Session, order_id: int, after_seq: int, days: int) -> None:
    if not days:
        return
    for s in list_stages(session, order_id):
        if s.seq > after_seq and s.status != "Completed":
            if s.start_on:
                s.start_on = s.start_on + datetime.timedelta(days=days)
            if s.end_on:
                s.end_on = s.end_on + datetime.timedelta(days=days)


def start_production(session: Session, *, order_public_id: str, stages: list[dict]) -> ProductionOrder | None:
    po = order_by_public(session, order_public_id)
    if po is None:
        return None
    for s in list_stages(session, po.id):  # clear any prior run
        s.is_deleted = True
    session.flush()
    cursor = datetime.date.today()
    for i, st in enumerate(stages):
        days = int(st.get("days") or 0)
        start = cursor
        end = start + datetime.timedelta(days=days)
        session.add(ProductionStage(
            tenant_id=po.tenant_id, order_id=po.id, seq=i + 1, name=st["name"],
            duration_days=days, status="In Progress" if i == 0 else "Pending",
            start_on=start, end_on=end,
        ))
        cursor = end
    po.stage = stages[0]["name"] if stages else po.stage
    po.progress = 0
    session.flush()
    return po


def start_stage(session: Session, *, public_id: str) -> ProductionStage | None:
    s = get_stage(session, public_id=public_id)
    if s is None:
        return None
    s.status = "In Progress"
    if not s.start_on:
        s.start_on = datetime.date.today()
    _recompute_order(session, s.order_id)
    session.flush()
    return s


def complete_stage(session: Session, *, public_id: str) -> ProductionStage | None:
    s = get_stage(session, public_id=public_id)
    if s is None:
        return None
    s.status = "Completed"
    s.end_on = datetime.date.today()
    _recompute_order(session, s.order_id)
    session.flush()
    return s


def extend_stage(session: Session, *, public_id: str, days: int) -> ProductionStage | None:
    s = get_stage(session, public_id=public_id)
    if s is None:
        return None
    days = int(days or 0)
    s.duration_days += days
    if s.end_on:
        s.end_on = s.end_on + datetime.timedelta(days=days)
    _shift_downstream(session, s.order_id, s.seq, days)
    session.flush()
    return s


def assign_stage(session: Session, *, public_id: str, worker: str) -> ProductionStage | None:
    s = get_stage(session, public_id=public_id)
    if s is None:
        return None
    s.worker = worker
    session.flush()
    return s


def notes_stage(session: Session, *, public_id: str, notes: str) -> ProductionStage | None:
    s = get_stage(session, public_id=public_id)
    if s is None:
        return None
    s.notes = notes
    session.flush()
    return s


def resolve_stage(session: Session, *, public_id: str) -> ProductionStage | None:
    """Re-baseline an overdue stage to today (clears the overdue flag)."""
    s = get_stage(session, public_id=public_id)
    if s is None:
        return None
    today = datetime.date.today()
    delta = (today - s.start_on).days if s.start_on else 0
    s.start_on = today
    s.end_on = today + datetime.timedelta(days=s.duration_days)
    _shift_downstream(session, s.order_id, s.seq, delta)
    session.flush()
    return s


def get_bom_detail(session: Session, *, public_id: str) -> dict | None:
    b = session.execute(
        select(BomLine).where(BomLine.public_id == public_id,
                              BomLine.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if b is None:
        return None
    orders = [dict(r._mapping) for r in session.execute(
        select(ProductionOrder.order_no, ProductionOrder.qty, ProductionOrder.factory,
               ProductionOrder.progress, ProductionOrder.stage)
        .where(ProductionOrder.style == b.style, ProductionOrder.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrder.id.desc())
    )]
    return {"bom": b, "orders": orders}


def _apply_bom(b: BomLine, *, component, style, material, cost) -> None:
    b.component = component
    b.style = style
    b.material = material
    b.cost = cost


def create_bom(session: Session, *, tenant_id: UUID, **fields) -> BomLine:
    b = BomLine(tenant_id=tenant_id)
    _apply_bom(b, **fields)
    session.add(b)
    session.flush()
    session.refresh(b)
    return b


def update_bom(session: Session, *, public_id: str, **fields) -> BomLine | None:
    b = session.execute(
        select(BomLine).where(BomLine.public_id == public_id,
                              BomLine.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if b is None:
        return None
    _apply_bom(b, **fields)
    session.flush()
    session.refresh(b)
    return b

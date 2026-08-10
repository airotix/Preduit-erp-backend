"""Production data access (tenant-scoped by RLS)."""
import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.production import BomLine, ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.sales import SalesOrder, SalesOrderLine
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
    customer = (
        select(SalesOrder.customer_name)
        .where(SalesOrder.id == ProductionOrder.sales_order_id)
        .correlate(ProductionOrder).scalar_subquery()
    )
    stmt = (
        select(ProductionOrder.public_id, ProductionOrder.order_no, ProductionOrder.style,
               ProductionOrder.factory, ProductionOrder.qty, ProductionOrder.stage,
               ProductionOrder.progress, customer.label("customer"),
               stage_count.label("stage_count"),
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


def create_porder(session: Session, *, tenant_id: UUID, style, factory, qty,
                  sales_order_id: int | None = None) -> ProductionOrder:
    po = ProductionOrder(tenant_id=tenant_id, style=style, factory=factory, qty=qty,
                         stage="Trims", progress=0, sales_order_id=sales_order_id)
    session.add(po)
    session.flush()
    po.order_no = f"MO-{3300 + po.id}"
    session.flush()
    session.refresh(po)
    return po


def create_porder_line(session: Session, *, tenant_id: UUID, order_id: int, name: str,
                       qty: int, sales_order_line_id: int | None = None, sku: str | None = None,
                       color: str | None = None, size: str | None = None) -> ProductionOrderLine:
    ln = ProductionOrderLine(
        tenant_id=tenant_id, order_id=order_id, name=name, qty=qty,
        sales_order_line_id=sales_order_line_id, sku=sku, color=color, size=size,
    )
    session.add(ln)
    session.flush()
    session.refresh(ln)
    return ln


def list_lines(session: Session, order_id: int) -> list[ProductionOrderLine]:
    return list(session.execute(
        select(ProductionOrderLine).where(ProductionOrderLine.order_id == order_id,
                                          ProductionOrderLine.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrderLine.id)
    ).scalars())


def list_line_stages(session: Session, line_id: int) -> list[ProductionStage]:
    return list(session.execute(
        select(ProductionStage).where(ProductionStage.line_id == line_id,
                                      ProductionStage.is_deleted == False)  # noqa: E712
        .order_by(ProductionStage.seq)
    ).scalars())


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
    # The originating sales order + its line items (for the Summary tab).
    sales_order, order_lines = None, []
    if po.sales_order_id:
        sales_order = session.get(SalesOrder, po.sales_order_id)
        order_lines = [dict(r._mapping) for r in session.execute(
            select(SalesOrderLine.name, SalesOrderLine.color, SalesOrderLine.size,
                   SalesOrderLine.qty, SalesOrderLine.price, SalesOrderLine.line_total)
            .where(SalesOrderLine.order_id == po.sales_order_id)
            .order_by(SalesOrderLine.id)
        )]
    # Per-style production lines, each with its own stage timeline.
    lines = [{"line": ln, "stages": list_line_stages(session, ln.id)}
             for ln in list_lines(session, po.id)]
    return {"order": po, "materials": materials, "stages": list_stages(session, po.id),
            "sales_order": sales_order, "order_lines": order_lines, "lines": lines}


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


def _shift(session: Session, stage: ProductionStage, days: int) -> None:
    """Shift the downstream (not-yet-complete) stages of this stage's line."""
    if not days:
        return
    siblings = (list_line_stages(session, stage.line_id) if stage.line_id
                else list_stages(session, stage.order_id))
    for s in siblings:
        if s.seq > stage.seq and s.status != "Completed":
            if s.start_on:
                s.start_on = s.start_on + datetime.timedelta(days=days)
            if s.end_on:
                s.end_on = s.end_on + datetime.timedelta(days=days)


def start_production(session: Session, *, order_public_id: str, stages: list[dict],
                     line_public_id: str | None = None) -> ProductionOrder | None:
    """Lay out the stage timeline. If ``line_public_id`` is given, only that line
    is (re)started — the other lines keep their own independent timelines. When
    omitted, every line of the order is started at once (used for manual orders
    and the whole-order start button)."""
    po = order_by_public(session, order_public_id)
    if po is None:
        return None
    all_lines = list_lines(session, po.id)
    if not all_lines:
        # Manual order with no lines → mirror the order as a single line.
        all_lines = [create_porder_line(session, tenant_id=po.tenant_id, order_id=po.id,
                                        name=po.style, qty=po.qty)]
    if line_public_id:
        target = [ln for ln in all_lines if str(ln.public_id) == str(line_public_id)]
        if not target:
            return None
    else:
        target = all_lines
    # Clear only the prior run of the target line(s) — leave the rest untouched.
    for line in target:
        for s in list_line_stages(session, line.id):
            s.is_deleted = True
    session.flush()
    for line in target:
        cursor = datetime.date.today()
        for i, st in enumerate(stages):
            days = int(st.get("days") or 0)
            start = cursor
            end = start + datetime.timedelta(days=days)
            session.add(ProductionStage(
                tenant_id=po.tenant_id, order_id=po.id, line_id=line.id, seq=i + 1,
                name=st["name"], duration_days=days,
                status="In Progress" if i == 0 else "Pending",
                start_on=start, end_on=end,
            ))
            cursor = end
    session.flush()
    _recompute_order(session, po.id)  # roll stage/progress up across all lines
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
    _shift(session, s, days)
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
    _shift(session, s, delta)
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

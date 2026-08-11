"""Quality data access (tenant-scoped by RLS)."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.quality import DefectType, Inspection


# ---------- Inspections ----------

def list_inspections(session, *, limit, offset):
    stmt = (
        select(Inspection.public_id, Inspection.inspection_no, Inspection.order_ref,
               Inspection.stage, Inspection.aql, Inspection.defect_count, Inspection.result)
        .where(Inspection.is_deleted == False)  # noqa: E712
        .order_by(Inspection.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Inspection).where(Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_inspection(session: Session, *, tenant_id: UUID, order_ref, stage, aql) -> Inspection:
    ins = Inspection(tenant_id=tenant_id, order_ref=order_ref, stage=stage, aql=aql,
                     result="Pending", defect_count=0)
    session.add(ins)
    session.flush()
    ins.inspection_no = f"QC-{7700 + ins.id}"
    session.flush()
    session.refresh(ins)
    return ins


def set_result(session: Session, *, public_id: str, result: str) -> Inspection | None:
    ins = session.execute(
        select(Inspection).where(Inspection.public_id == public_id,
                                 Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if ins is None:
        return None
    ins.result = result
    session.flush()
    return ins


def inspection_exists(session: Session, *, order_ref: str | None) -> bool:
    """Whether an inspection already exists for this order (idempotency)."""
    if not order_ref:
        return False
    return session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.order_ref == order_ref, Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one() > 0


def get_inspection(session: Session, *, public_id: str) -> Inspection | None:
    return session.execute(
        select(Inspection).where(Inspection.public_id == public_id,
                                 Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def inspection_stats(session) -> dict:
    total = session.execute(
        select(func.count()).select_from(Inspection).where(Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one()
    passed = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Pass")  # noqa: E712
    ).scalar_one()
    failed = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Fail")  # noqa: E712
    ).scalar_one()
    return {"total": total, "passed": passed, "failed": failed}


# ---------- Defect types ----------

def list_defects(session, *, limit, offset):
    stmt = (
        select(DefectType.public_id, DefectType.name, DefectType.category,
               DefectType.severity, DefectType.frequency)
        .where(DefectType.is_deleted == False)  # noqa: E712
        .order_by(DefectType.frequency.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(DefectType).where(DefectType.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def _apply_defect(d: DefectType, *, name, category, severity) -> None:
    d.name = name
    d.category = category
    d.severity = severity


def create_defect(session: Session, *, tenant_id: UUID, **fields) -> DefectType:
    d = DefectType(tenant_id=tenant_id)
    _apply_defect(d, **fields)
    session.add(d)
    session.flush()
    session.refresh(d)
    return d


def update_defect(session: Session, *, public_id: str, **fields) -> DefectType | None:
    d = session.execute(
        select(DefectType).where(DefectType.public_id == public_id,
                                 DefectType.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if d is None:
        return None
    _apply_defect(d, **fields)
    session.flush()
    session.refresh(d)
    return d

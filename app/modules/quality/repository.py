"""Quality data access (tenant-scoped by RLS)."""
import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.quality import DefectType, Inspection, InspectionCheck, InspectionDefect
from app.modules.quality import aql as aql_engine


# ---------- Inspections ----------

def list_inspections(session, *, limit, offset):
    stmt = (
        select(Inspection.public_id, Inspection.inspection_no, Inspection.order_ref,
               Inspection.stage, Inspection.aql, Inspection.defect_count, Inspection.result,
               Inspection.product, Inspection.inspector, Inspection.inspection_date,
               Inspection.item)
        .where(Inspection.is_deleted == False)  # noqa: E712
        .order_by(Inspection.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Inspection).where(Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_inspection(session: Session, *, tenant_id: UUID, order_ref, stage, aql,
                      sku=None, product=None, batch_lot=None, inspection_type=None,
                      prod_qty=None, inspector=None, parent_inspection_id=None,
                      item=None) -> Inspection:
    """Create a Pending inspection. Sample size + max allowed defects are derived
    from the AQL engine using the production quantity (when known)."""
    plan = aql_engine.sampling_plan(prod_qty, aql)
    ins = Inspection(
        tenant_id=tenant_id, order_ref=order_ref, item=item, stage=stage, aql=plan["aql"],
        result="Pending", defect_count=0,
        sku=sku, product=product, batch_lot=batch_lot,
        inspection_type=inspection_type or ("Final QC" if stage == "Final" else "In-line"),
        prod_qty=prod_qty, sample_size=plan["sampleSize"], max_defects=plan["maxDefects"],
        inspection_date=datetime.date.today(), inspector=inspector,
        parent_inspection_id=parent_inspection_id,
    )
    session.add(ins)
    session.flush()
    ins.inspection_no = f"QC-{7700 + ins.id}"
    session.flush()
    session.refresh(ins)
    return ins


def start_inspection(session: Session, *, public_id: str, tenant_id: UUID | None = None) -> Inspection | None:
    """Pending → In Progress (records the start time; seeds the default checklist)."""
    ins = get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    if ins.result == "Pending":
        ins.result = "In Progress"
        ins.started_at = datetime.datetime.utcnow()
        session.flush()
        seed_default_checks(session, tenant_id=tenant_id or ins.tenant_id, inspection_id=ins.id)
    return ins


def update_inspection_header(session: Session, *, public_id: str, inspector, batch_lot,
                             inspection_type, aql, sample_size=None) -> Inspection | None:
    """Edit header fields.
      - If the AQL level changes, sample size + max defects are recomputed from
        the AQL sampling plan (auto behaviour, unchanged).
      - If a manual sample size is supplied, it overrides the plan and the max
        allowed defects are recomputed to match that sample size + AQL.
    """
    ins = get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    ins.inspector = inspector
    ins.batch_lot = batch_lot
    ins.inspection_type = inspection_type
    aql_changed = bool(aql) and aql != ins.aql
    if aql:
        ins.aql = aql_engine._normalise_aql(aql)
    if aql_changed and not sample_size:
        # AQL changed, no manual override → recompute the standard plan.
        plan = aql_engine.sampling_plan(ins.prod_qty, ins.aql)
        ins.sample_size = plan["sampleSize"]
        ins.max_defects = plan["maxDefects"]
    if sample_size and int(sample_size) > 0:
        # Manual sample size → keep it, recompute the acceptance number for it.
        ins.sample_size = int(sample_size)
        ins.max_defects = aql_engine.acceptance_for_sample(int(sample_size), ins.aql)
    session.flush()
    return ins


def link_shipment(session: Session, *, ins: Inspection, shipment_no: str | None) -> None:
    if shipment_no and not ins.shipment_ref:
        ins.shipment_ref = shipment_no
        session.flush()


# ---------- Checklist ----------

# Default apparel QC checklist seeded onto a fresh inspection.
DEFAULT_CHECKS: list[tuple[str, str]] = [
    ("Stitching", "No loose/broken/skipped stitches"),
    ("Measurements", "Within spec tolerance"),
    ("Colour", "Matches approved sample / shade band"),
    ("Fabric / material", "Correct fabric, no flaws"),
    ("Labelling", "Correct labels, care & size present"),
    ("Packaging", "Correct polybag, carton & barcode"),
    ("Workmanship / surface", "No stains, marks or damage"),
]


def list_checks(session: Session, *, inspection_id: int) -> list[InspectionCheck]:
    return session.execute(
        select(InspectionCheck)
        .where(InspectionCheck.inspection_id == inspection_id,
               InspectionCheck.is_deleted == False)  # noqa: E712
        .order_by(InspectionCheck.seq, InspectionCheck.id)
    ).scalars().all()


def seed_default_checks(session: Session, *, tenant_id: UUID, inspection_id: int) -> None:
    """Seed the standard checklist onto an inspection that has none yet."""
    existing = session.execute(
        select(func.count()).select_from(InspectionCheck)
        .where(InspectionCheck.inspection_id == inspection_id,
               InspectionCheck.is_deleted == False)  # noqa: E712
    ).scalar_one()
    if existing:
        return
    for i, (crit, req) in enumerate(DEFAULT_CHECKS):
        session.add(InspectionCheck(tenant_id=tenant_id, inspection_id=inspection_id,
                                    seq=i, criterion=crit, requirement=req, result="Pending"))
    session.flush()


def add_check(session: Session, *, tenant_id: UUID, inspection_id: int, **f) -> InspectionCheck:
    seq = session.execute(
        select(func.coalesce(func.max(InspectionCheck.seq), -1))
        .where(InspectionCheck.inspection_id == inspection_id)
    ).scalar_one() + 1
    c = InspectionCheck(tenant_id=tenant_id, inspection_id=inspection_id, seq=seq, **f)
    session.add(c)
    session.flush()
    session.refresh(c)
    return c


def get_check(session: Session, *, public_id: str) -> InspectionCheck | None:
    return session.execute(
        select(InspectionCheck).where(InspectionCheck.public_id == public_id,
                                      InspectionCheck.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def update_check(session: Session, *, public_id: str, **f) -> InspectionCheck | None:
    c = get_check(session, public_id=public_id)
    if c is None:
        return None
    for k, v in f.items():
        setattr(c, k, v)
    session.flush()
    session.refresh(c)
    return c


def delete_check(session: Session, *, public_id: str) -> bool:
    c = get_check(session, public_id=public_id)
    if c is None:
        return False
    c.is_deleted = True
    session.flush()
    return True


# ---------- Inspection defects ----------

def defect_options(session: Session) -> list[dict]:
    """Defect Types catalog projected for the 'Add defect' dropdown."""
    rows = session.execute(
        select(DefectType.public_id, DefectType.id, DefectType.name,
               DefectType.category, DefectType.severity)
        .where(DefectType.is_deleted == False)  # noqa: E712
        .order_by(DefectType.name)
    ).all()
    return [{"publicId": str(r.public_id), "id": r.id, "name": r.name,
             "category": r.category, "severity": r.severity} for r in rows]


def _defect_type_by_public(session: Session, public_id: str) -> DefectType | None:
    return session.execute(
        select(DefectType).where(DefectType.public_id == public_id,
                                 DefectType.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def list_inspection_defects(session: Session, *, inspection_id: int) -> list[InspectionDefect]:
    return session.execute(
        select(InspectionDefect)
        .where(InspectionDefect.inspection_id == inspection_id,
               InspectionDefect.is_deleted == False)  # noqa: E712
        .order_by(InspectionDefect.id)
    ).scalars().all()


def recompute_defect_count(session: Session, *, ins: Inspection) -> None:
    total = session.execute(
        select(func.coalesce(func.sum(InspectionDefect.qty_affected), 0))
        .where(InspectionDefect.inspection_id == ins.id,
               InspectionDefect.is_deleted == False)  # noqa: E712
    ).scalar_one()
    ins.defect_count = int(total or 0)
    session.flush()


def add_defect(session: Session, *, tenant_id: UUID, ins: Inspection, defect_type_public: str | None,
               defect_name: str | None, qty_affected: int, location, description, corrective,
               image_doc_id: str | None = None) -> InspectionDefect:
    dt = _defect_type_by_public(session, defect_type_public) if defect_type_public else None
    d = InspectionDefect(
        tenant_id=tenant_id, inspection_id=ins.id,
        defect_type_id=dt.id if dt else None,
        defect_name=(dt.name if dt else defect_name) or "Defect",
        category=dt.category if dt else None,
        severity=dt.severity if dt else None,
        qty_affected=qty_affected, location=location,
        description=description, corrective=corrective, image_doc_id=image_doc_id,
    )
    session.add(d)
    session.flush()
    d.defect_no = f"DEF-{d.id:04d}"
    session.flush()
    recompute_defect_count(session, ins=ins)
    session.refresh(d)
    return d


def delete_inspection_defect(session: Session, *, public_id: str) -> Inspection | None:
    d = session.execute(
        select(InspectionDefect).where(InspectionDefect.public_id == public_id,
                                       InspectionDefect.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if d is None:
        return None
    d.is_deleted = True
    session.flush()
    ins = session.get(Inspection, d.inspection_id)
    if ins is not None:
        recompute_defect_count(session, ins=ins)
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


def promote_open_to_final(session: Session, *, order_ref: str | None) -> Inspection | None:
    """When production for an order finishes, advance its still-open inspection
    (Pending / In Progress) to the Final QC stage so the stage isn't stuck at an
    earlier value like 'Pre-Production'. Finalised inspections are left as-is."""
    if not order_ref:
        return None
    ins = session.execute(
        select(Inspection)
        .where(Inspection.order_ref == order_ref, Inspection.is_deleted == False,  # noqa: E712
               Inspection.result.in_(["Pending", "In Progress"]))
        .order_by(Inspection.id.desc())
    ).scalars().first()
    if ins is None:
        return None
    if ins.stage != "Final":
        ins.stage = "Final"
        ins.inspection_type = "Final QC"
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


def inspection_exists_for_item(session: Session, *, order_ref: str | None, item: str | None) -> bool:
    """Whether an inspection already exists for this order + item (per-item idempotency)."""
    if not order_ref:
        return False
    return session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.order_ref == order_ref, Inspection.item == item,
               Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one() > 0


def inspections_for_order(session: Session, *, order_ref: str | None) -> list[Inspection]:
    """Every inspection under an order (one per item) — for the grouped drill-down."""
    if not order_ref:
        return []
    return session.execute(
        select(Inspection).where(Inspection.order_ref == order_ref,
                                 Inspection.is_deleted == False)  # noqa: E712
        .order_by(Inspection.id)
    ).scalars().all()


def get_inspection(session: Session, *, public_id: str) -> Inspection | None:
    return session.execute(
        select(Inspection).where(Inspection.public_id == public_id,
                                 Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def set_disposition(session: Session, *, public_id: str, disposition: str,
                    notes: str | None, assigned_to: str | None, due_date) -> Inspection | None:
    """Record the disposition of a FAILED inspection."""
    ins = get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    ins.disposition = disposition
    ins.disposition_notes = notes
    ins.assigned_to = assigned_to
    ins.due_date = due_date
    session.flush()
    return ins


def create_reinspection(session: Session, *, tenant_id: UUID, parent: Inspection) -> Inspection:
    """A fresh Pending inspection linked to the failed original, carrying its
    context forward (its own id + result; history shares the order_ref)."""
    plan = aql_engine.sampling_plan(parent.prod_qty, parent.aql)
    ins = Inspection(
        tenant_id=tenant_id, order_ref=parent.order_ref, stage=parent.stage, aql=plan["aql"],
        result="Pending", defect_count=0, sku=parent.sku, product=parent.product,
        batch_lot=parent.batch_lot, inspection_type=parent.inspection_type,
        prod_qty=parent.prod_qty, sample_size=plan["sampleSize"], max_defects=plan["maxDefects"],
        inspection_date=datetime.date.today(), inspector=parent.inspector,
        parent_inspection_id=parent.id,
    )
    session.add(ins)
    session.flush()
    ins.inspection_no = f"QC-{7700 + ins.id}"
    session.flush()
    session.refresh(ins)
    return ins


def defect_insights(session: Session) -> dict:
    """Real quality statistics across all inspections — drives the (future-ready)
    Quality Insights panel. No AI/ML backend is used; these are live aggregates."""
    # Top defects by affected quantity.
    top_rows = session.execute(
        select(InspectionDefect.defect_name,
               func.coalesce(func.sum(InspectionDefect.qty_affected), 0).label("qty"))
        .where(InspectionDefect.is_deleted == False)  # noqa: E712
        .group_by(InspectionDefect.defect_name)
        .order_by(func.coalesce(func.sum(InspectionDefect.qty_affected), 0).desc())
    ).all()
    total_qty = sum(int(r.qty) for r in top_rows) or 0
    top = [{"name": r.defect_name or "—", "qty": int(r.qty),
            "pct": round(int(r.qty) / total_qty * 100, 1) if total_qty else 0.0}
           for r in top_rows[:5]]

    # Finalised pass/fail counts → fail rate → risk band.
    passed = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Pass")  # noqa: E712
    ).scalar_one()
    failed = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Fail")  # noqa: E712
    ).scalar_one()
    finalised = passed + failed
    fail_rate = round(failed / finalised * 100, 1) if finalised else 0.0
    risk = "HIGH" if fail_rate >= 20 else "MEDIUM" if fail_rate >= 8 else "LOW"
    return {"topDefects": top, "passed": passed, "failed": failed,
            "finalised": finalised, "failRate": fail_rate, "risk": risk}


def inspection_history(session: Session, *, order_ref: str | None) -> list[dict]:
    """All inspections for an order (original + re-inspections), newest first."""
    if not order_ref:
        return []
    stmt = (
        select(Inspection.public_id, Inspection.inspection_no, Inspection.inspection_date,
               Inspection.stage, Inspection.inspector, Inspection.result,
               Inspection.defect_count, Inspection.aql)
        .where(Inspection.order_ref == order_ref, Inspection.is_deleted == False)  # noqa: E712
        .order_by(Inspection.id.desc())
    )
    return [dict(r._mapping) for r in session.execute(stmt)]


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

"""One QC workflow for a full production order and for a production batch.

The production scope changes. Start, checklist, decision and hold resolution
do not. Inline inspections never release pieces. Final approval only records
quantities; shipments and warehouse receipts stay in their own modules.
"""
from __future__ import annotations

import json
import uuid
from urllib.parse import unquote

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import has_permission
from app.models.batches import BatchLine, BatchStage, ProductionBatch
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.quality import Inspection, InspectionCheck, QcCommand, QcLineBalance, QcReservation
from app.modules.quality import aql as aql_engine
from app.modules.quality import repository as repo


def task_key(scope: str, public_id: str, article: str) -> str:
    return f"{scope}:{public_id}:{article}"


def parse_task(key: str) -> tuple[str, str, str]:
    raw = unquote(key or "")
    scope, sep, rest = raw.partition(":")
    public_id, sep2, article = rest.partition(":")
    if scope not in ("order", "batch") or not sep or not sep2 or not public_id or not article.strip():
        raise HTTPException(404, "Inspection task not found")
    return scope, public_id, article.strip()


def task_key_for_inspection(session: Session, ins: Inspection) -> str | None:
    if ins.batch_id:
        batch = session.get(ProductionBatch, ins.batch_id)
        if batch is not None:
            return task_key("batch", str(batch.public_id), ins.item or ins.product or "Article")
    if ins.batch_line_id:
        line = session.get(BatchLine, ins.batch_line_id)
        batch = session.get(ProductionBatch, line.batch_id) if line else None
        source = session.get(PurchaseOrderLine, line.po_line_id) if line else None
        if batch is not None and source is not None:
            return task_key("batch", str(batch.public_id), source.name)
    work = session.scalar(select(ProductionOrder).where(
        ProductionOrder.tenant_id == ins.tenant_id, ProductionOrder.order_no == ins.order_ref,
        ProductionOrder.is_deleted.is_(False)))
    if work is None:
        return None
    return task_key("order", str(work.public_id), ins.item or ins.product or work.style)


def _tid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _request(value) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(422, "A request id is required so the decision can be retried safely.")


def _replay(session, tenant, request_id):
    row = session.scalar(select(QcCommand).where(
        QcCommand.tenant_id == tenant, QcCommand.request_id == _request(request_id)))
    return json.loads(row.result) if row else None


def _remember(session, tenant, request_id, payload):
    prior = _replay(session, tenant, request_id)
    if prior is not None:
        return prior
    command = QcCommand(tenant_id=tenant, request_id=_request(request_id), result=json.dumps(payload))
    try:
        with session.begin_nested():
            session.add(command)
            session.flush()
    except IntegrityError:
        session.expunge(command)
        found = _replay(session, tenant, request_id)
        if found is not None:
            return found
        raise
    return payload


def _status(*, awaiting, held, approved, rejected, open_result, legacy) -> str:
    if open_result == "In Progress":
        return "In progress"
    if held > 0 and approved == 0:
        return "On hold"
    if held > 0 or (approved > 0 and (awaiting > 0 or rejected > 0)):
        return "Partially cleared"
    if awaiting > 0:
        return "Pending"
    if approved > 0:
        return "Cleared"
    if rejected > 0:
        return "Rejected"
    if legacy:
        return "Cleared"
    return "Pending"


def _next_action(*, awaiting, held, open_result, in_production=False) -> str:
    """The one thing a user should do next on this article, for the queue."""
    if open_result == "In Progress":
        return "Continue"
    if awaiting > 0:
        return "Inspect"
    if held > 0:
        return "Resolve hold"
    if in_production:
        return "Check"
    return "Done"


def _po_for(session, work: ProductionOrder):
    if not work.purchase_order_id:
        return None
    return session.get(PurchaseOrder, work.purchase_order_id)


def _balance(session, tenant, line_id: int) -> QcLineBalance:
    row = session.scalar(select(QcLineBalance).where(
        QcLineBalance.tenant_id == tenant, QcLineBalance.production_line_id == line_id))
    if row is None:
        row = QcLineBalance(tenant_id=tenant, production_line_id=line_id, approved=0, held=0, rejected=0)
        session.add(row)
        session.flush()
    return row


def _reserved(session, tenant, *, production_line_id=None, batch_line_id=None, kind=None, exclude_inspection=None):
    query = select(QcReservation).where(QcReservation.tenant_id == tenant)
    if production_line_id is not None:
        query = query.where(QcReservation.production_line_id == production_line_id)
    if batch_line_id is not None:
        query = query.where(QcReservation.batch_line_id == batch_line_id)
    if kind:
        query = query.where(QcReservation.kind == kind)
    if exclude_inspection is not None:
        query = query.where(QcReservation.inspection_id != exclude_inspection)
    return sum(row.qty for row in session.scalars(query))


def _tna_done(session, line_id: int) -> bool:
    stages = session.scalars(select(ProductionStage).where(
        ProductionStage.line_id == line_id, ProductionStage.is_deleted.is_(False))).all()
    return bool(stages) and all(stage.status in ("Completed", "Received") for stage in stages)


def _batch_style_done(session, tenant, line: BatchLine) -> bool:
    from app.modules.batches.workflow import style_complete
    return style_complete(session, tenant, line)


def work_queue(session: Session, tenant_id) -> dict:
    tenant = _tid(tenant_id)
    orders = session.scalars(select(ProductionOrder).where(
        ProductionOrder.tenant_id == tenant, ProductionOrder.is_deleted.is_(False)).order_by(ProductionOrder.id.desc())).all()
    groups = []
    for work in orders:
        po = _po_for(session, work)
        if po is not None and (po.is_deleted or po.status in ("Cancelled", "Rejected", "Inactive")):
            continue
        batches = session.scalars(select(ProductionBatch).where(
            ProductionBatch.tenant_id == tenant, ProductionBatch.order_id == work.id,
            ProductionBatch.status != "Cancelled")).all()
        tasks = []
        if batches:
            for batch in batches:
                tasks.extend(_batch_tasks(session, tenant, work, po, batch))
        else:
            tasks.extend(_order_tasks(session, tenant, work, po))
        if tasks:
            groups.append({
                "orderId": str(work.public_id), "orderRef": work.order_no,
                "poRef": po.po_no if po else None, "supplier": po.supplier_name if po else None,
                "tasks": tasks,
            })
    return {"groups": groups}


def _latest_inspector(session, tenant, order_ref, article, batch_id=None):
    query = select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == order_ref, Inspection.item == article,
        Inspection.is_deleted.is_(False)).order_by(Inspection.id.desc())
    if batch_id is not None:
        query = query.where(Inspection.batch_id == batch_id)
    row = session.scalars(query).first()
    return row.inspector if row else None


def _open_result(session, tenant, order_ref, article, batch_id=None):
    query = select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == order_ref, Inspection.item == article,
        Inspection.is_deleted.is_(False), Inspection.result.in_(("Pending", "In Progress")))
    if batch_id is not None:
        query = query.where(Inspection.batch_id == batch_id)
    else:
        query = query.where(Inspection.batch_id.is_(None), Inspection.batch_line_id.is_(None))
    row = session.scalars(query.order_by(Inspection.id.desc())).first()
    return row.result if row else None


def _legacy_cleared(session, tenant, order_ref, article) -> bool:
    rows = session.scalars(select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == order_ref, Inspection.item == article,
        Inspection.is_deleted.is_(False), Inspection.stage == "Final",
        Inspection.batch_line_id.is_(None), Inspection.batch_id.is_(None)).order_by(Inspection.id)).all()
    latest = next((row for row in reversed(rows) if row.result != "Cancelled"), None)
    return bool(latest and latest.legacy_clearance and latest.result == "Pass" and latest.approved_qty is None)


def _order_tasks(session, tenant, work, po):
    lines = session.scalars(select(ProductionOrderLine).where(
        ProductionOrderLine.order_id == work.id, ProductionOrderLine.tenant_id == tenant,
        ProductionOrderLine.is_deleted.is_(False)).order_by(ProductionOrderLine.id)).all()
    names = list(dict.fromkeys(line.name for line in lines))
    tasks = []
    for name in names:
        group = [line for line in lines if line.name == name]
        if not any(_tna_done(session, line.id) or _line_started(session, line.id) for line in group):
            continue
        approved = held = rejected = awaiting = 0
        quantity_mode = False
        for line in group:
            bal = session.scalar(select(QcLineBalance).where(
                QcLineBalance.tenant_id == tenant, QcLineBalance.production_line_id == line.id))
            approved += bal.approved if bal else 0
            held += bal.held if bal else 0
            rejected += bal.rejected if bal else 0
            if bal and (bal.approved or bal.held or bal.rejected):
                quantity_mode = True
            produced = line.qty if (_tna_done(session, line.id) or (bal and (bal.approved or bal.held or bal.rejected))) else 0
            claimed = _reserved(session, tenant, production_line_id=line.id, kind="awaiting")
            awaiting += max(0, produced - (bal.approved if bal else 0) - (bal.held if bal else 0) - (bal.rejected if bal else 0) - claimed)
        legacy = _legacy_cleared(session, tenant, work.order_no, name) and not quantity_mode and approved == 0 and held == 0 and rejected == 0
        if legacy:
            awaiting = 0
        open_result = _open_result(session, tenant, work.order_no, name)
        in_production = not legacy and not all(_tna_done(session, line.id) for line in group)
        tasks.append({
            "nextAction": _next_action(awaiting=awaiting, held=held, open_result=open_result, in_production=in_production),
            "inProduction": in_production,
            "taskKey": task_key("order", str(work.public_id), name),
            "article": name, "scope": "Full order", "scopeKind": "order",
            "awaiting": awaiting, "held": held, "approved": approved, "rejected": rejected,
            "inspector": _latest_inspector(session, tenant, work.order_no, name),
            "status": _status(awaiting=awaiting, held=held, approved=approved, rejected=rejected,
                              open_result=open_result, legacy=legacy),
            "legacy": legacy, "quantityMode": quantity_mode or not legacy,
        })
    return tasks


def _line_started(session, line_id: int) -> bool:
    stages = session.scalars(select(ProductionStage).where(
        ProductionStage.line_id == line_id, ProductionStage.is_deleted.is_(False))).all()
    return any(stage.status != "Pending" for stage in stages)


def _batch_started(session, tenant, batch) -> bool:
    stages = session.scalars(select(BatchStage).where(
        BatchStage.tenant_id == tenant, BatchStage.batch_id == batch.id)).all()
    return any(stage.name != "Tech Pack" and stage.status != "Pending" for stage in stages)


def _batch_tasks(session, tenant, work, po, batch):
    lines = session.scalars(select(BatchLine).where(
        BatchLine.tenant_id == tenant, BatchLine.batch_id == batch.id)).all()
    grouped: dict[str, list[BatchLine]] = {}
    for line in lines:
        source = session.get(PurchaseOrderLine, line.po_line_id)
        grouped.setdefault(source.name if source else "Article", []).append(line)
    tasks = []
    started = _batch_started(session, tenant, batch)
    for name, group in grouped.items():
        if not started and not any(line.completed or line.held or line.approved or _batch_style_done(session, tenant, line) for line in group):
            continue
        approved = sum(line.approved for line in group)
        held = sum(line.held for line in group)
        rejected = sum(line.rejected for line in group)
        awaiting = 0
        for line in group:
            claimed = _reserved(session, tenant, batch_line_id=line.id, kind="awaiting")
            awaiting += max(0, line.completed - line.approved - line.held - line.rejected - claimed)
        open_result = _open_result(session, tenant, work.order_no, name, batch.id)
        in_production = not all(_batch_style_done(session, tenant, line) for line in group)
        tasks.append({
            "nextAction": _next_action(awaiting=awaiting, held=held, open_result=open_result, in_production=in_production),
            "inProduction": in_production,
            "taskKey": task_key("batch", str(batch.public_id), name),
            "article": name, "scope": batch.name, "scopeKind": "batch",
            "awaiting": awaiting, "held": held, "approved": approved, "rejected": rejected,
            "inspector": _latest_inspector(session, tenant, work.order_no, name, batch.id),
            "status": _status(awaiting=awaiting, held=held, approved=approved, rejected=rejected,
                              open_result=open_result, legacy=False),
            "legacy": False, "quantityMode": True,
        })
    return tasks


def _context(session, tenant, scope, public_id, article):
    try:
        scope_uuid = uuid.UUID(public_id)
    except ValueError:
        raise HTTPException(404, "Inspection task not found")
    if scope == "batch":
        batch = session.scalar(select(ProductionBatch).where(
            ProductionBatch.tenant_id == tenant, ProductionBatch.public_id == scope_uuid).with_for_update())
        if batch is None:
            raise HTTPException(404, "Production batch not found")
        work = session.scalar(select(ProductionOrder).where(
            ProductionOrder.id == batch.order_id, ProductionOrder.tenant_id == tenant).with_for_update())
    else:
        batch = None
        work = session.scalar(select(ProductionOrder).where(
            ProductionOrder.tenant_id == tenant, ProductionOrder.public_id == scope_uuid,
            ProductionOrder.is_deleted.is_(False)).with_for_update())
    if work is None:
        raise HTTPException(404, "Production order not found")
    po = _po_for(session, work)
    return work, po, batch


def _sources(session, tenant, work, batch, article):
    if batch is not None:
        rows = []
        for line in session.scalars(select(BatchLine).where(
                BatchLine.tenant_id == tenant, BatchLine.batch_id == batch.id).order_by(BatchLine.id)).all():
            source = session.get(PurchaseOrderLine, line.po_line_id)
            if source is None or source.name != article:
                continue
            awaiting_reserved = _reserved(session, tenant, batch_line_id=line.id, kind="awaiting")
            held_reserved = _reserved(session, tenant, batch_line_id=line.id, kind="held")
            awaiting = max(0, line.completed - line.approved - line.held - line.rejected - awaiting_reserved)
            rows.append({
                "id": line.id, "kind": "batch", "color": source.color, "size": source.size, "sku": source.sku,
                "planned": line.planned, "awaiting": awaiting, "held": max(0, line.held - held_reserved),
                "approved": line.approved, "rejected": line.rejected,
                "tnaComplete": _batch_style_done(session, tenant, line),
            })
        return rows
    rows = []
    for line in session.scalars(select(ProductionOrderLine).where(
            ProductionOrderLine.order_id == work.id, ProductionOrderLine.tenant_id == tenant,
            ProductionOrderLine.name == article, ProductionOrderLine.is_deleted.is_(False)).order_by(ProductionOrderLine.id)).all():
        bal = session.scalar(select(QcLineBalance).where(
            QcLineBalance.tenant_id == tenant, QcLineBalance.production_line_id == line.id))
        approved = bal.approved if bal else 0
        held = bal.held if bal else 0
        rejected = bal.rejected if bal else 0
        done = _tna_done(session, line.id)
        produced = line.qty if done or approved or held or rejected else 0
        awaiting_reserved = _reserved(session, tenant, production_line_id=line.id, kind="awaiting")
        held_reserved = _reserved(session, tenant, production_line_id=line.id, kind="held")
        # A production line covers every colour/size of its style; the PO lines behind it
        # give the colour and size split used to display the quantities.
        variants = [{"color": p.color, "size": p.size, "qty": int(p.qty or 0)} for p in session.scalars(
            select(PurchaseOrderLine).where(PurchaseOrderLine.tenant_id == tenant,
                                            PurchaseOrderLine.production_line_id == line.id)
            .order_by(PurchaseOrderLine.id)).all()]
        rows.append({
            "id": line.id, "kind": "order", "color": line.color, "size": line.size, "sku": line.sku,
            "variants": variants,
            "planned": int(line.qty or 0),
            "awaiting": max(0, produced - approved - held - rejected - awaiting_reserved),
            "held": max(0, held - held_reserved),
            "approved": approved, "rejected": rejected, "tnaComplete": done,
        })
    return rows


def get_task(session: Session, tenant_id, key: str, permissions: list[str]) -> dict:
    tenant = _tid(tenant_id)
    scope, public_id, article = parse_task(key)
    work, po, batch = _context(session, tenant, scope, public_id, article)
    sources = _sources(session, tenant, work, batch, article)
    if not sources:
        raise HTTPException(404, "Article not found on this production scope")
    awaiting = sum(row["awaiting"] for row in sources)
    held = sum(row["held"] for row in sources)
    approved = sum(row["approved"] for row in sources)
    rejected = sum(row["rejected"] for row in sources)
    batch_id = batch.id if batch else None
    legacy = scope == "order" and _legacy_cleared(session, tenant, work.order_no, article) and approved == held == rejected == 0
    if legacy:
        awaiting = 0
    open_row = _open_inspection(session, tenant, work.order_no, article, batch_id)
    in_production = not legacy and not all(row["tnaComplete"] for row in sources)
    planned_qty = sum(row.get("planned", 0) for row in sources)
    plan_qty = awaiting or held or (planned_qty if in_production else 0)
    aql_level = repo.tenant_aql(session, tenant)
    plan = aql_engine.sampling_plan(plan_qty, aql_level)
    return {
        "taskKey": task_key(scope, public_id, article),
        "orderRef": work.order_no, "orderId": str(work.public_id),
        "poRef": po.po_no if po else None, "poId": str(po.public_id) if po else None,
        "supplier": po.supplier_name if po else None,
        "article": article, "scope": batch.name if batch else "Full order",
        "scopeKind": scope,
        "awaiting": awaiting, "held": held, "approved": approved, "rejected": rejected,
        "inProduction": in_production, "plannedQty": planned_qty,
        "status": _status(awaiting=awaiting, held=held, approved=approved, rejected=rejected,
                          open_result=open_row.result if open_row else None, legacy=legacy),
        "legacy": legacy,
        "legacyNote": "Cleared by an earlier inspection that did not record release quantities. Those pieces are not released again." if legacy else None,
        "aql": aql_level,
        "suggestedSampleSize": plan["sampleSize"], "suggestedMaxDefects": plan["maxDefects"],
        "plan":"Configured single-sampling plan in quality/aql.py (Level II tables stored in the application).",
        "sources": sources,
        "reworkOpen": _rework_blocking(session, tenant, work, batch, article),
        "canReleaseException": has_permission(permissions, "quality.release_exception"),
        "inspection": _inspection_payload(session, open_row) if open_row else None,
        "history": _history(session, tenant, work.order_no, article, batch_id),
        "links": {
            "production": f"/production/porders/{work.public_id}",
            "purchaseOrder": f"/procurement/pos/{po.public_id}" if po else None,
            "shipments": "/shipments/shipments",
            "inventory": "/inventory/stock",
        },
    }


def _open_inspection(session, tenant, order_ref, article, batch_id):
    query = select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == order_ref, Inspection.item == article,
        Inspection.is_deleted.is_(False), Inspection.result.in_(("Pending", "In Progress")))
    if batch_id is not None:
        query = query.where(Inspection.batch_id == batch_id)
    else:
        query = query.where(Inspection.batch_id.is_(None), Inspection.batch_line_id.is_(None))
    return session.scalars(query.order_by(Inspection.id.desc())).first()


def _inspection_payload(session, ins: Inspection | None):
    if ins is None:
        return None
    checks = repo.list_checks(session, inspection_id=ins.id)
    defects = repo.list_inspection_defects(session, inspection_id=ins.id)
    reservations = session.scalars(select(QcReservation).where(QcReservation.inspection_id == ins.id)).all()
    accepted = _acceptance(ins, checks)
    return {
        "publicId": str(ins.public_id), "inspectionNo": ins.inspection_no, "result": ins.result,
        "stage": ins.stage, "samplingMode": ins.sampling_mode, "sampleSize": ins.sample_size,
        "lotQty": ins.prod_qty, "maxDefects": ins.max_defects, "defectCount": ins.defect_count,
        "aql": ins.aql, "inspector": ins.inspector, "purpose": reservations[0].kind if reservations else None,
        "accepted": accepted["accepted"], "reasons": accepted["reasons"],
        "checks": [{"publicId": str(c.public_id), "criterion": c.criterion, "requirement": c.requirement,
                    "actual": c.actual, "result": c.result, "notes": c.notes,
                    "targetValue": float(c.target_value) if c.target_value is not None else None,
                    "tolerance": float(c.tolerance) if c.tolerance is not None else None} for c in checks],
        "defects": [{"publicId": str(d.public_id), "name": d.defect_name, "severity": d.severity,
                     "qty": d.qty_affected, "notes": d.description, "imageDocId": d.image_doc_id} for d in defects],
        "reservations": [{"sourceId": row.batch_line_id or row.production_line_id, "qty": row.qty, "kind": row.kind}
                         for row in reservations],
    }


def _history(session, tenant, order_ref, article, batch_id):
    query = select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == order_ref, Inspection.item == article,
        Inspection.is_deleted.is_(False)).order_by(Inspection.id.desc())
    if batch_id is not None:
        query = query.where((Inspection.batch_id == batch_id) | (Inspection.batch_line_id.is_not(None)))
    rows = []
    for ins in session.scalars(query).all():
        if batch_id is not None and ins.batch_id not in (None, batch_id):
            continue
        rows.append({
            "publicId": str(ins.public_id), "inspectionNo": ins.inspection_no, "stage": ins.stage,
            "result": ins.result, "legacy": bool(ins.legacy_clearance),
            "approved": ins.approved_qty, "held": ins.held_qty, "rejected": ins.rejected_qty,
            "decision": ins.release_decision, "inspector": ins.inspector,
        })
    return rows


def _acceptance(ins: Inspection, checks: list[InspectionCheck]) -> dict:
    blocking = []
    quality = []
    if not checks:
        blocking.append("Add at least one checklist item.")
    if any(check.result == "Pending" for check in checks):
        blocking.append("Resolve every checklist item before the decision.")
    if any(check.result == "Fail" for check in checks):
        quality.append("A checklist item failed.")
    limit = ins.max_defects if ins.max_defects is not None else aql_engine.sampling_plan(ins.prod_qty, ins.aql)["maxDefects"]
    if (ins.defect_count or 0) > (limit or 0):
        quality.append(f"Defects ({ins.defect_count}) exceed the configured acceptance number ({limit}).")
    return {"accepted": not blocking and not quality, "blocking": blocking, "reasons": blocking + quality or ["Within the configured acceptance rules."], "maxDefects": limit}


def _rework_blocking(session, tenant, work, batch, article) -> bool:
    latest = session.scalars(select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == work.order_no, Inspection.item == article,
        Inspection.is_deleted.is_(False), Inspection.result.not_in(("Pending", "In Progress", "Cancelled"))
    ).order_by(Inspection.id.desc())).first()
    if latest is None or latest.disposition != "Rework":
        return False
    if batch is not None:
        return not all(_batch_style_done(session, tenant, line) for line in _batch_lines(session, tenant, batch, article))
    lines = session.scalars(select(ProductionOrderLine).where(
        ProductionOrderLine.order_id == work.id, ProductionOrderLine.tenant_id == tenant,
        ProductionOrderLine.name == article, ProductionOrderLine.is_deleted.is_(False))).all()
    return not lines or not all(_tna_done(session, line.id) for line in lines)


def _batch_lines(session, tenant, batch, article):
    rows = []
    for line in session.scalars(select(BatchLine).where(BatchLine.tenant_id == tenant, BatchLine.batch_id == batch.id)).all():
        source = session.get(PurchaseOrderLine, line.po_line_id)
        if source and source.name == article:
            rows.append(line)
    return rows


def start_task(session, *, tenant_id, actor, permissions, key, payload):
    tenant = _tid(tenant_id)
    prior = _replay(session, tenant, payload.requestId)
    if prior:
        return prior
    scope, public_id, article = parse_task(key)
    work, po, batch = _context(session, tenant, scope, public_id, article)
    existing = _open_inspection(session, tenant, work.order_no, article, batch.id if batch else None)
    already_reserved = bool(existing and session.scalar(
        select(QcReservation.id).where(QcReservation.inspection_id == existing.id).limit(1)))
    if existing and (existing.stage != "Final" or already_reserved):
        return _remember(session, tenant, payload.requestId, {"inspectionId": str(existing.public_id), "reused": True})
    if existing and payload.stage == "Inline":
        raise HTTPException(409, "Finish the open final inspection before starting an inline inspection.")
    purpose = payload.purpose
    stage = "In-line" if payload.stage == "Inline" else "Final"
    if stage == "Final" and purpose == "held" and _rework_blocking(session, tenant, work, batch, article):
        raise HTTPException(409, "Complete the reopened production TNA before starting reinspection.")
    if stage == "Final" and scope == "order" and _legacy_cleared(session, tenant, work.order_no, article):
        raise HTTPException(409, "This article was cleared by a legacy inspection without quantities and cannot be released again.")
    sources = _sources(session, tenant, work, batch, article)
    pool = "held" if purpose == "held" else "awaiting"
    if stage == "Final":
        if purpose == "held" and not all(row["tnaComplete"] for row in sources if row["held"]):
            raise HTTPException(409, "Complete this article's production TNA before final reinspection.")
        if purpose == "awaiting" and not any(row["tnaComplete"] and row["awaiting"] for row in sources):
            raise HTTPException(409, "Final QC is available after the article's production TNA is complete and pieces are awaiting inspection.")
    chosen = {item.sourceId: item.qty for item in payload.lines} if payload.lines else None
    if stage == "Final" and chosen is not None:
        for row in sources:
            qty = chosen.get(row["id"], 0)
            if qty < 0 or qty > row[pool]:
                raise HTTPException(422, "Selected quantity exceeds the pieces still eligible on a colour or size.")
            row[pool] = qty
    lot = sum(row[pool] for row in sources) if stage == "Final" else max(
        1, sum(row["awaiting"] + row["held"] + row["approved"] for row in sources), sum(row.get("planned", 0) for row in sources))
    if stage == "Final" and lot <= 0:
        raise HTTPException(409, "No eligible quantity is left for this inspection.")
    sample = lot if payload.samplingMode == "full" else int(payload.sampleSize or 0)
    if sample <= 0 or sample > max(lot, 1):
        raise HTTPException(422, "Sample size must be between 1 and the lot quantity covered by this inspection.")
    reused = existing is not None
    if reused:
        ins = existing
    else:
        ins = repo.create_inspection(
            session, tenant_id=tenant, order_ref=work.order_no, item=article, stage=stage,
            product=article, batch_lot=(batch.name[:60] if batch else "Full order"),
            inspection_type="Inline" if stage == "In-line" else ("Reinspection" if purpose == "held" else "Final"),
            prod_qty=lot if stage == "Final" else None, inspector=payload.inspector or actor)
    ins.batch_id = batch.id if batch else None
    ins.sampling_mode = payload.samplingMode
    ins.sample_size = sample
    ins.max_defects = aql_engine.acceptance_for_sample(sample, ins.aql)
    ins.prod_qty = lot if stage == "Final" else ins.prod_qty
    ins.legacy_clearance = False
    if payload.inspector:
        ins.inspector = payload.inspector
    if stage == "Final":
        for row in sources:
            qty = row[pool]
            if qty <= 0:
                continue
            session.add(QcReservation(
                tenant_id=tenant, inspection_id=ins.id, qty=qty, kind=pool,
                production_line_id=row["id"] if row["kind"] == "order" else None,
                batch_line_id=row["id"] if row["kind"] == "batch" else None))
        if purpose == "held":
            ins.parent_inspection_id = ins.parent_inspection_id or _rework_parent_id(session, tenant, work.order_no, article)
    session.flush()
    if ins.result == "Pending":
        repo.start_inspection(session, public_id=str(ins.public_id), tenant_id=tenant)
    return _remember(session, tenant, payload.requestId, {"inspectionId": str(ins.public_id), "reused": reused})


def failed_midchecks(session: Session, tenant_id=None, order_ref: str | None = None) -> list[dict]:
    """Articles whose latest inspection is a failed mid-production check.

    A later inspection on the same article (a pass, or a final inspection)
    replaces the failure, so the alert clears once the problem is rechecked.
    """
    query = select(Inspection).where(Inspection.is_deleted.is_(False), Inspection.result != "Cancelled")
    if tenant_id is not None:
        query = query.where(Inspection.tenant_id == _tid(tenant_id))
    if order_ref:
        query = query.where(Inspection.order_ref == order_ref)
    latest: dict[tuple, Inspection] = {}
    for row in session.scalars(query.order_by(Inspection.id.desc())):
        latest.setdefault((row.order_ref, row.item, row.batch_id), row)
    return [{"orderRef": row.order_ref, "article": row.item, "scope": row.batch_lot or "Full order",
             "inspectionNo": row.inspection_no}
            for row in latest.values() if row.stage == "In-line" and row.result == "Fail"]


def _rework_parent_id(session, tenant, order_ref, article):
    row = session.scalars(select(Inspection).where(
        Inspection.tenant_id == tenant, Inspection.order_ref == order_ref, Inspection.item == article,
        Inspection.disposition == "Rework", Inspection.is_deleted.is_(False)).order_by(Inspection.id.desc())).first()
    return row.id if row else None


def decide_task(session, *, tenant_id, permissions, key, payload):
    tenant = _tid(tenant_id)
    prior = _replay(session, tenant, payload.requestId)
    if prior:
        return prior
    scope, public_id, article = parse_task(key)
    work, po, batch = _context(session, tenant, scope, public_id, article)
    ins = _open_inspection(session, tenant, work.order_no, article, batch.id if batch else None)
    if ins is None or ins.result != "In Progress":
        raise HTTPException(409, "Start the inspection before recording its decision.")
    if ins.stage == "Final" and _rework_blocking(session, tenant, work, batch, article):
        raise HTTPException(409, "Complete the reopened production TNA before completing reinspection.")
    checks = repo.list_checks(session, inspection_id=ins.id)
    outcome = _acceptance(ins, checks)
    if outcome["blocking"]:
        raise HTTPException(409, " ".join(outcome["blocking"]))
    if ins.stage != "Final":
        ins.result = "Pass" if outcome["accepted"] else "Fail"
        ins.approved_qty = ins.held_qty = ins.rejected_qty = 0
        ins.release_decision = "inline"
        ins.finalized_at = _now()
        session.flush()
        return _remember(session, tenant, payload.requestId, {"result": ins.result, "released": False, "reasons": outcome["reasons"]})
    reservations = session.scalars(select(QcReservation).where(QcReservation.inspection_id == ins.id)).all()
    if not reservations:
        raise HTTPException(409, "This inspection has no reserved quantity to decide.")
    if not outcome["accepted"] and payload.decision == "approve_all":
        _require_exception(permissions, payload.exceptionReason)
    allocations = _allocations(payload, reservations, outcome, permissions)
    _apply(session, tenant, ins, allocations)
    ins.approved_qty = sum(row["approved"] for row in allocations)
    ins.held_qty = sum(row["held"] for row in allocations)
    ins.rejected_qty = sum(row["rejected"] for row in allocations)
    ins.release_decision = payload.decision
    ins.exception_reason = (payload.exceptionReason or "").strip() or None
    ins.result = "Pass" if ins.held_qty == 0 and ins.rejected_qty == 0 and outcome["accepted"] else "Fail"
    if ins.exception_reason and ins.approved_qty and not outcome["accepted"] and ins.held_qty == 0 and ins.rejected_qty == 0:
        ins.result = "Pass"
    ins.finalized_at = _now()
    for row in reservations:
        session.delete(row)
    session.flush()
    # Released goods appear under Shipments > Ready to ship. A shipment is only created
    # when someone prepares it, never automatically.
    return _remember(session, tenant, payload.requestId, {
        "result": ins.result, "released": True, "approved": ins.approved_qty,
        "held": ins.held_qty, "rejected": ins.rejected_qty, "reasons": outcome["reasons"],
    })


def _require_exception(permissions, reason):
    if not has_permission(permissions, "quality.release_exception"):
        raise HTTPException(403, "Failed acceptance rules block approval. An administrator must record an exception reason.")
    if not (reason or "").strip():
        raise HTTPException(422, "Record the reason for approving pieces that failed the acceptance rules.")


def _allocations(payload, reservations, outcome, permissions):
    reserved = { (row.batch_line_id or row.production_line_id): row for row in reservations }
    if payload.decision == "approve_all":
        return [{"sourceId": key, "kind": row.kind, "batch": row.batch_line_id is not None,
                 "approved": row.qty, "held": 0, "rejected": 0} for key, row in reserved.items()]
    if payload.decision == "hold_all":
        return [{"sourceId": key, "kind": row.kind, "batch": row.batch_line_id is not None,
                 "approved": 0, "held": row.qty, "rejected": 0} for key, row in reserved.items()]
    if payload.decision != "split":
        raise HTTPException(422, "Choose approve all, hold all, or split quantities.")
    seen = set()
    rows = []
    for item in payload.allocations:
        holder = reserved.get(item.sourceId)
        if holder is None:
            raise HTTPException(422, "A split line is not part of this inspection's quantity.")
        if item.sourceId in seen:
            raise HTTPException(422, "Each colour and size can only be decided once in a split.")
        if item.approved + item.held + item.rejected != holder.qty:
            raise HTTPException(422, "Approved, held and rejected must equal the reserved quantity on every colour and size.")
        if item.approved and not outcome["accepted"]:
            _require_exception(permissions, payload.exceptionReason)
        seen.add(item.sourceId)
        rows.append({"sourceId": item.sourceId, "kind": holder.kind, "batch": holder.batch_line_id is not None,
                     "approved": item.approved, "held": item.held, "rejected": item.rejected})
    if seen != set(reserved):
        raise HTTPException(422, "Enter a decision for every colour and size in this inspection.")
    return rows


def _apply(session, tenant, ins: Inspection, allocations):
    for row in allocations:
        if row["batch"]:
            line = session.scalar(select(BatchLine).where(
                BatchLine.id == row["sourceId"], BatchLine.tenant_id == tenant).with_for_update())
            if line is None:
                raise HTTPException(409, "Batch quantity line was not found")
            take = row["approved"] + row["held"] + row["rejected"]
            if row["kind"] == "held":
                other = _reserved(session, tenant, batch_line_id=line.id, kind="held", exclude_inspection=ins.id)
                if line.held - other < take:
                    raise HTTPException(409, "Held quantity changed. Reload the inspection before deciding.")
                line.held -= take
            else:
                other = _reserved(session, tenant, batch_line_id=line.id, kind="awaiting", exclude_inspection=ins.id)
                free = line.completed - line.approved - line.held - line.rejected - other
                if free < take:
                    raise HTTPException(409, "Those pieces were already decided. Reload the inspection.")
            line.approved += row["approved"]
            line.held += row["held"]
            line.rejected += row["rejected"]
            if line.approved + line.held + line.rejected > line.completed:
                raise HTTPException(409, "Decision exceeds produced quantity.")
        else:
            bal = _balance(session, tenant, row["sourceId"])
            line = session.get(ProductionOrderLine, row["sourceId"])
            if line is None or line.tenant_id != tenant:
                raise HTTPException(409, "Production quantity line was not found")
            take = row["approved"] + row["held"] + row["rejected"]
            if row["kind"] == "held":
                other = _reserved(session, tenant, production_line_id=line.id, kind="held", exclude_inspection=ins.id)
                if bal.held - other < take:
                    raise HTTPException(409, "Held quantity changed. Reload the inspection before deciding.")
                bal.held -= take
            else:
                other = _reserved(session, tenant, production_line_id=line.id, kind="awaiting", exclude_inspection=ins.id)
                free = line.qty - bal.approved - bal.held - bal.rejected - other
                if free < take:
                    raise HTTPException(409, "Those pieces were already decided. Reload the inspection.")
            bal.approved += row["approved"]
            bal.held += row["held"]
            bal.rejected += row["rejected"]
    session.flush()


def _reserved_qty(session, inspection_id, batch_line_id=None, production_line_id=None):
    query = select(QcReservation).where(QcReservation.inspection_id == inspection_id)
    if batch_line_id is not None:
        query = query.where(QcReservation.batch_line_id == batch_line_id)
    if production_line_id is not None:
        query = query.where(QcReservation.production_line_id == production_line_id)
    return sum(row.qty for row in session.scalars(query))


def resolve_hold(session, *, tenant_id, actor, key, payload):
    tenant = _tid(tenant_id)
    prior = _replay(session, tenant, payload.requestId)
    if prior:
        return prior
    scope, public_id, article = parse_task(key)
    work, po, batch = _context(session, tenant, scope, public_id, article)
    if _open_inspection(session, tenant, work.order_no, article, batch.id if batch else None):
        raise HTTPException(409, "Finish or keep the open inspection before resolving the hold.")
    selected = {item.sourceId: item.qty for item in payload.lines} if payload.lines else None
    sources = _sources(session, tenant, work, batch, article)
    moved = []
    if payload.action == "reject":
        if not (payload.reason or "").strip():
            raise HTTPException(422, "A reason is required to reject held pieces.")
        for row in sources:
            qty = row["held"] if selected is None else selected.get(row["id"], 0)
            if qty <= 0:
                continue
            if qty > row["held"]:
                raise HTTPException(422, "Reject quantity exceeds the held quantity for a colour or size.")
            _move_held_to_rejected(session, tenant, row, qty)
            moved.append({"sourceId": row["id"], "qty": qty})
        if not moved:
            raise HTTPException(409, "There is no held quantity to reject.")
        ins = repo.create_inspection(
            session, tenant_id=tenant, order_ref=work.order_no, item=article, stage="Final",
            product=article, batch_lot=(batch.name[:60] if batch else "Full order"),
            inspection_type="Reject hold", prod_qty=sum(item["qty"] for item in moved), inspector=actor)
        ins.batch_id = batch.id if batch else None
        ins.result = "Fail"
        ins.disposition = "Rejected"
        ins.disposition_notes = payload.reason.strip()
        ins.rejected_qty = sum(item["qty"] for item in moved)
        ins.approved_qty = ins.held_qty = 0
        ins.release_decision = "reject_hold"
        ins.finalized_at = _now()
        ins.legacy_clearance = False
        session.flush()
        return _remember(session, tenant, payload.requestId, {"action": "reject", "inspectionId": str(ins.public_id), "lines": moved})
    if payload.action == "rework":
        _reopen_tna(session, tenant, work, batch, article)
        latest = session.scalars(select(Inspection).where(
            Inspection.tenant_id == tenant, Inspection.order_ref == work.order_no, Inspection.item == article,
            Inspection.is_deleted.is_(False)).order_by(Inspection.id.desc())).first()
        if latest:
            latest.disposition = "Rework"
            latest.disposition_notes = (payload.reason or "").strip() or latest.disposition_notes
        session.flush()
        return _remember(session, tenant, payload.requestId, {"action": "rework", "blockedUntilTna": True})
    if payload.action != "reinspect":
        raise HTTPException(422, "Choose reinspect, rework, or reject.")
    return _remember(session, tenant, payload.requestId, {"action": "reinspect", "next": "start", "purpose": "held"})


def _move_held_to_rejected(session, tenant, row, qty):
    if row["kind"] == "batch":
        line = session.scalar(select(BatchLine).where(BatchLine.id == row["id"], BatchLine.tenant_id == tenant).with_for_update())
        if line is None or line.held < qty:
            raise HTTPException(409, "Held quantity changed. Reload and try again.")
        line.held -= qty
        line.rejected += qty
    else:
        bal = _balance(session, tenant, row["id"])
        if bal.held < qty:
            raise HTTPException(409, "Held quantity changed. Reload and try again.")
        bal.held -= qty
        bal.rejected += qty
    session.flush()


def _reopen_tna(session, tenant, work, batch, article):
    import datetime
    if batch is not None:
        line_ids = set()
        for line in _batch_lines(session, tenant, batch, article):
            source = session.get(PurchaseOrderLine, line.po_line_id)
            if source and source.production_line_id:
                line_ids.add(source.production_line_id)
        stages = session.scalars(select(BatchStage).where(
            BatchStage.tenant_id == tenant, BatchStage.batch_id == batch.id,
            BatchStage.production_line_id.in_(line_ids or {-1}))).all()
        if not stages:
            raise HTTPException(409, "This article has no production TNA to reopen.")
        for stage in stages:
            stage.status = "Pending"
            stage.actual_end_on = None
        return
    lines = session.scalars(select(ProductionOrderLine).where(
        ProductionOrderLine.order_id == work.id, ProductionOrderLine.tenant_id == tenant,
        ProductionOrderLine.name == article, ProductionOrderLine.is_deleted.is_(False))).all()
    if not lines:
        raise HTTPException(409, "Production item not found")
    from app.modules.production.service import STAGES
    for line in lines:
        old = session.scalars(select(ProductionStage).where(
            ProductionStage.line_id == line.id, ProductionStage.is_deleted.is_(False)).order_by(ProductionStage.seq)).all()
        specs = [(stage.name, stage.duration_days, stage.worker) for stage in old] or [(name, 1, None) for name in STAGES]
        for stage in old:
            stage.is_deleted = True
        cursor = datetime.date.today()
        for seq, (name, days, worker) in enumerate(specs, 1):
            end = cursor + datetime.timedelta(days=days or 1)
            session.add(ProductionStage(
                tenant_id=tenant, order_id=work.id, line_id=line.id, seq=seq, name=name,
                duration_days=days or 1, worker=worker, status="Pending", start_on=cursor, end_on=end))
            cursor = end
    session.flush()


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def _refresh_ordinary_shipment(session, work: ProductionOrder):
    po = _po_for(session, work)
    if po is None:
        return
    from app.modules.shipments.supplier_inbound import ensure
    ensure(session, po)


def supplier_release(session: Session, po: PurchaseOrder) -> dict:
    """Quantity-aware gate for ordinary supplier shipments. Legacy passes stay as recorded."""
    work = session.scalar(select(ProductionOrder).where(
        ProductionOrder.purchase_order_id == po.id, ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.is_deleted.is_(False)))
    if work is None:
        return {"ready": False, "lines": []}
    if session.scalar(select(ProductionBatch.id).where(
            ProductionBatch.order_id == work.id, ProductionBatch.tenant_id == po.tenant_id).limit(1)):
        return {"ready": False, "lines": []}
    po_lines = session.scalars(select(PurchaseOrderLine).where(
        PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.tenant_id == po.tenant_id).order_by(PurchaseOrderLine.id)).all()
    prod_lines = session.scalars(select(ProductionOrderLine).where(
        ProductionOrderLine.order_id == work.id, ProductionOrderLine.tenant_id == po.tenant_id,
        ProductionOrderLine.is_deleted.is_(False))).all()
    by_article: dict[str, list] = {}
    for line in prod_lines:
        by_article.setdefault(line.name, []).append(line)
    if not by_article:
        return {"ready": False, "lines": []}
    articles_ready = True
    any_approved = False
    line_qty = {}
    for name, group in by_article.items():
        approved = held = rejected = awaiting = 0
        quantity = False
        for line in group:
            bal = session.scalar(select(QcLineBalance).where(
                QcLineBalance.tenant_id == po.tenant_id, QcLineBalance.production_line_id == line.id))
            if bal and (bal.approved or bal.held or bal.rejected):
                quantity = True
            approved += bal.approved if bal else 0
            held += bal.held if bal else 0
            rejected += bal.rejected if bal else 0
            produced = line.qty if _tna_done(session, line.id) or (bal and (bal.approved or bal.held or bal.rejected)) else 0
            awaiting += max(0, produced - (bal.approved if bal else 0) - (bal.held if bal else 0) - (bal.rejected if bal else 0))
            line_qty[line.id] = bal.approved if bal else 0
        legacy = _legacy_cleared(session, po.tenant_id, work.order_no, name) and not quantity
        if legacy:
            if not _all_tna(session, group):
                articles_ready = False
            any_approved = True
            for line in group:
                line_qty[line.id] = line.qty
            continue
        if awaiting or held or not _all_tna(session, group):
            articles_ready = False
        if approved:
            any_approved = True
        if not approved and not rejected:
            articles_ready = False
    shipment_lines = []
    for po_line in po_lines:
        qty = line_qty.get(po_line.production_line_id, 0) if po_line.production_line_id else 0
        if qty > 0:
            shipment_lines.append({"poLineId": po_line.id, "qty": min(po_line.qty, qty), "sku": po_line.sku,
                                   "description": " · ".join(v for v in (po_line.name, po_line.color, po_line.size) if v)[:200]})
    return {"ready": articles_ready and any_approved and bool(shipment_lines), "lines": shipment_lines}


def _all_tna(session, lines) -> bool:
    return bool(lines) and all(_tna_done(session, line.id) for line in lines)

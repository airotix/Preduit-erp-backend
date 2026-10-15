"""Quality business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.production import ProductionOrder, ProductionOrderLine
from app.models.quality import Inspection
from app.models.batches import ProductionBatch, BatchLine
from app.modules.quality import aql as aql_engine
from app.modules.quality import repository as repo
from app.modules.quality.dto import DefectTypeCreate, DefectTypeUpdate, InspectionCreate
from app.presenters.screen import list_config, text_cell

_RESULT_TONE = {"Pass": "green", "Fail": "red", "Pending": "amber",
                "In Progress": "navy", "Cancelled": "neutral"}
_SEV_TONE = {"Critical": "red", "Major": "red", "Minor": "amber"}


def _batch_managed_order(session, tenant_id, order_ref):
    if not order_ref:
        return False
    return bool(session.scalar(select(ProductionBatch.id).join(ProductionOrder,
        ProductionOrder.id == ProductionBatch.order_id).where(
        ProductionOrder.order_no == order_ref, ProductionOrder.tenant_id == _tid(tenant_id),
        ProductionBatch.tenant_id == _tid(tenant_id)).limit(1)))


def order_items(session: Session, order_ref: str | None) -> list[dict]:
    """Distinct items (production lines) for an order + their total quantity —
    powers the Item selector on the New inspection form."""
    if not order_ref:
        return []
    po = session.execute(
        select(ProductionOrder).where(ProductionOrder.order_no == order_ref,
                                      ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if po is None:
        return []
    rows = session.execute(
        select(ProductionOrderLine.name, ProductionOrderLine.qty)
        .where(ProductionOrderLine.order_id == po.id,
               ProductionOrderLine.is_deleted == False)  # noqa: E712
    ).all()
    agg: dict[str, int] = {}
    for name, qty in rows:
        agg[name] = agg.get(name, 0) + int(qty or 0)
    return [{"item": n, "qty": q} for n, q in agg.items()]


def _item_qty(session: Session, order_ref: str | None, item: str | None) -> int | None:
    """Total quantity for one item across the order's production lines."""
    for it in order_items(session, order_ref):
        if it["item"] == item:
            return it["qty"]
    return None


def _po_autofill(session: Session, order_ref: str | None) -> dict:
    """Pull product/sku/qty from the matching production order (read-only) so we
    don't ask the inspector to re-key data the ERP already has."""
    if not order_ref:
        return {}
    po = session.execute(
        select(ProductionOrder).where(ProductionOrder.order_no == order_ref,
                                      ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if po is None:
        return {}
    sku = session.execute(
        select(ProductionOrderLine.sku).where(ProductionOrderLine.order_id == po.id,
                                               ProductionOrderLine.sku.isnot(None)).limit(1)
    ).scalar_one_or_none()
    return {"product": po.style, "prod_qty": po.qty, "sku": sku}


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


# ---------- Inspections ----------

def _group_status(results: list[str], stages: list[str]) -> str:
    """Roll a set of item results into one order-level label for the list."""
    if any(r == "Fail" for r in results):
        return "Attention"
    if any(r == "In Progress" for r in results):
        return "In Progress"
    if any(r == "Pending" for r in results):
        return "Pending"
    if results and all(r == "Pass" for r in results):
        return "Cleared" if all(s == "Final" for s in stages) else "Passed"
    return results[0] if results else "—"


_GROUP_TONE = {"Attention": "red", "In Progress": "navy", "Pending": "amber",
               "Passed": "green", "Cleared": "green"}


def inspections_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    # Pull all inspections, then group into ONE row per order (each order's items
    # open together as tabs in the drill-down).
    rows, _ = repo.list_inspections(session, limit=1000, offset=0)
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        batch = session.get(ProductionBatch, session.get(BatchLine, r["batch_line_id"]).batch_id) if r.get("batch_line_id") else None
        key = f"batch::{batch.id}" if batch else r["order_ref"] or f"__{r['public_id']}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    grid, ids, records = [], [], []
    for key in order:
        g = groups[key]
        first_line = session.get(BatchLine, g[0]["batch_line_id"]) if g[0].get("batch_line_id") else None
        batch = session.get(ProductionBatch, first_line.batch_id) if first_line else None
        order_ref = f"{g[0]['order_ref']} · {batch.name}" if batch else g[0]["order_ref"] or "—"
        items = [x["item"] or x["product"] or "Item" for x in g]
        uniq_items = list(dict.fromkeys(items))
        prod = uniq_items[0] + (f" +{len(uniq_items) - 1} more" if len(uniq_items) > 1 else "")
        inspectors = list(dict.fromkeys([x["inspector"] for x in g if x["inspector"]]))
        defects = sum(int(x["defect_count"] or 0) for x in g)
        if batch:
            from app.modules.batches import workflow as batch_workflow
            batch_lines = session.scalars(select(BatchLine).where(BatchLine.batch_id == batch.id,
                BatchLine.tenant_id == batch.tenant_id)).all()
            label = batch_workflow.states(session, batch.tenant_id, batch, batch_lines)["qualityStatus"]
            tone = "red" if "hold" in label.lower() else "green" if label == "QC complete" else "amber"
        else:
            label = _group_status([x["result"] for x in g], [x["stage"] for x in g])
            tone = _GROUP_TONE.get(label, "neutral")
        grid.append([
            text_cell(order_ref, avatar=True, sub=f"{len(g)} item{'s' if len(g) != 1 else ''}"),
            order_ref,
            prod,
            text_cell(inspectors[0] + (f" +{len(inspectors) - 1}" if len(inspectors) > 1 else "") if inspectors else "—"),
            text_cell(str(defects), align="center", mono=True),
            text_cell(label, badge=tone),
        ])
        ids.append(str(g[0]["public_id"]))   # opens the grouped drill-down
        records.append({"status": label})

    return list_config(
        columns=[{"label": "Order"}, {"label": "Order ref"}, {"label": "Items"},
                 {"label": "Inspector"}, {"label": "Defects", "align": "center"},
                 {"label": "Result"}],
        rows=grid, total=len(grid),
        ids=ids, records=records,
        search="Search inspections…", action="New inspection", filters=["Stage", "Result"],
    )


def create_inspection(session, *, tenant_id, payload: InspectionCreate):
    """Create a Pending inspection, auto-pulling product/SKU/quantity from the
    production order so the inspector doesn't re-enter existing ERP data."""
    from fastapi import HTTPException
    if _batch_managed_order(session, tenant_id, payload.order):
        raise HTTPException(409, "Record inspection against the production batch in Quality")
    auto = _po_autofill(session, payload.order)
    item = (payload.item or "").strip() or None
    # When an item is chosen, scope product + quantity to that item (production
    # line); otherwise fall back to the whole-order autofill.
    if item:
        product = payload.product or item
        prod_qty = _item_qty(session, payload.order, item)
    else:
        product = payload.product or auto.get("product")
        prod_qty = auto.get("prod_qty")
    return repo.create_inspection(
        session, tenant_id=_tid(tenant_id), order_ref=payload.order,
        stage=payload.stage, aql=payload.aql,
        sku=payload.sku or auto.get("sku"),
        product=product, batch_lot=payload.batchLot,
        inspection_type=payload.inspectionType,
        prod_qty=prod_qty, inspector=payload.inspector, item=item,
    )


def update_inspection(session, *, public_id, payload):
    existing = repo.get_inspection(session, public_id=public_id)
    if existing is not None and existing.batch_line_id:
        from fastapi import HTTPException
        raise HTTPException(409, "Batch inspection quantities are immutable; record a new QC action for changes")
    ss = None
    if payload.sampleSize not in (None, ""):
        try:
            ss = int(str(payload.sampleSize).strip())
        except ValueError:
            ss = None
    return repo.update_inspection_header(
        session, public_id=public_id, inspector=payload.inspector,
        batch_lot=payload.batchLot, inspection_type=payload.inspectionType,
        aql=payload.aql, sample_size=ss)


def start_inspection(session, *, public_id, tenant_id=None):
    current = repo.get_inspection(session, public_id=public_id)
    if current is not None and current.result == "Cancelled":
        from fastapi import HTTPException
        raise HTTPException(409, "A cancelled inspection cannot be restarted.")
    """Pending → In Progress. Guard: only a Pending inspection can be started."""
    ins = repo.get_inspection(session, public_id=public_id)
    if ins and inspection_waits_for_rework(session, ins):
        from fastapi import HTTPException
        raise HTTPException(409, "Complete the reopened production TNA before starting reinspection.")
    return repo.start_inspection(session, public_id=public_id,
                                 tenant_id=_tid(tenant_id) if tenant_id else None)


# ---------- Checklist ----------

def _eval_check(target, tolerance, actual, result):
    """When a numeric target + tolerance are set and the actual is numeric,
    derive Pass/Fail automatically; otherwise keep the supplied result."""
    if target is not None and tolerance is not None and actual not in (None, ""):
        try:
            val = float(str(actual).strip())
        except ValueError:
            return result
        return "Pass" if abs(val - float(target)) <= float(tolerance) else "Fail"
    return result


def add_check(session, *, tenant_id, public_id, payload):
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    result = _eval_check(payload.targetValue, payload.tolerance, payload.actual, payload.result)
    return repo.add_check(session, tenant_id=_tid(tenant_id), inspection_id=ins.id,
                          criterion=payload.criterion, requirement=payload.requirement,
                          target_value=payload.targetValue, tolerance=payload.tolerance,
                          actual=payload.actual, result=result, notes=payload.notes)


def update_check(session, *, check_public_id, payload):
    result = _eval_check(payload.targetValue, payload.tolerance, payload.actual, payload.result)
    return repo.update_check(session, public_id=check_public_id,
                             criterion=payload.criterion, requirement=payload.requirement,
                             target_value=payload.targetValue, tolerance=payload.tolerance,
                             actual=payload.actual, result=result, notes=payload.notes)


def delete_check(session, *, check_public_id):
    return repo.delete_check(session, public_id=check_public_id)


# ---------- Defects ----------

def defect_options(session):
    return repo.defect_options(session)


def add_defect(session, *, tenant_id, public_id, payload):
    from fastapi import HTTPException
    ins = session.scalar(select(Inspection).where(Inspection.public_id == UUID(str(public_id)),
        Inspection.tenant_id == _tid(tenant_id), Inspection.is_deleted.is_(False)).with_for_update())
    if ins is None:
        return None
    if ins.result != "In Progress":
        raise HTTPException(409, "Start the inspection before adding defects.")
    defect_type_id = payload.defectTypeId
    if payload.newDefect:
        from sqlalchemy import func
        from app.models.quality import DefectType
        if defect_type_id:
            raise HTTPException(422, "Select an existing defect or create a new one.")
        name, category = payload.newDefect.name.strip(), payload.newDefect.category.strip()
        if not name or not category:
            raise HTTPException(422, "Defect name and category are required.")
        existing = session.scalar(select(DefectType).where(DefectType.tenant_id == _tid(tenant_id),
            DefectType.is_deleted.is_(False), func.lower(func.trim(DefectType.name)) == name.lower()).limit(1))
        defect_type = existing or repo.create_defect(session, tenant_id=_tid(tenant_id),
            name=name, category=category, severity=payload.newDefect.severity)
        defect_type_id = str(defect_type.public_id)
    if not defect_type_id and not (payload.defectName or "").strip():
        raise HTTPException(422, "Select a defect type or enter a defect name.")
    return repo.add_defect(session, tenant_id=_tid(tenant_id), ins=ins,
                           defect_type_public=defect_type_id, defect_name=(payload.defectName or "").strip(),
                           qty_affected=payload.qtyAffected, location=payload.location,
                           description=payload.description, corrective=payload.corrective,
                           image_doc_id=payload.imageDocId)


def delete_defect(session, *, defect_public_id):
    return repo.delete_inspection_defect(session, public_id=defect_public_id)


# ---------- Disposition + re-inspection ----------

def set_disposition(session, *, public_id, payload):
    """Record disposition for a failed inspection. Guard: only a FAILED
    inspection can be dispositioned."""
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    if ins.batch_line_id:
        from fastapi import HTTPException
        raise HTTPException(409, "Batch dispositions are recorded with their QC quantities")
    if ins.result != "Fail":
        return {"error": "not_failed"}
    child = session.scalar(select(Inspection).where(Inspection.parent_inspection_id == ins.id,
        Inspection.tenant_id == ins.tenant_id, Inspection.is_deleted.is_(False)).limit(1))
    if child and payload.disposition != ins.disposition:
        from fastapi import HTTPException
        raise HTTPException(409, "Recovery is already underway; keep its selected disposition.")
    import datetime as _dt
    due = None
    if payload.dueDate:
        try:
            due = _dt.date.fromisoformat(payload.dueDate[:10])
        except ValueError:
            due = None
    ins = repo.set_disposition(session, public_id=public_id, disposition=payload.disposition,
                               notes=payload.notes, assigned_to=payload.assignedTo, due_date=due)
    return {"inspection": ins}


def create_reinspection(session, *, tenant_id, public_id):
    return reopen_failed(session, tenant_id=tenant_id, public_id=public_id, mode="inspection")


def production_for_inspection(session, ins):
    return session.scalar(select(ProductionOrder).where(ProductionOrder.tenant_id == ins.tenant_id,
        ProductionOrder.order_no == ins.order_ref, ProductionOrder.is_deleted.is_(False)))


def inspection_waits_for_rework(session, ins):
    if not ins.parent_inspection_id:
        return False
    parent = session.get(Inspection, ins.parent_inspection_id)
    if parent is None or parent.disposition != "Rework":
        return False
    work = production_for_inspection(session, ins)
    if work is None:
        return False
    lines = session.scalars(select(ProductionOrderLine).where(ProductionOrderLine.order_id == work.id,
        ProductionOrderLine.tenant_id == ins.tenant_id, ProductionOrderLine.is_deleted.is_(False))).all()
    from app.modules.production.service import _line_is_complete
    targets = [line for line in lines if not ins.item or line.name == ins.item]
    return not targets or not all(_line_is_complete(session, line.id) for line in targets)


def reopen_failed(session, *, tenant_id, public_id, mode, notes=None, assigned_to=None, due_date=None):
    from fastapi import HTTPException
    from app.models.quality import Inspection
    from app.models.procurement import PurchaseOrder
    from app.models.production import ProductionStage
    from app.modules.production import repository as production
    import datetime
    tid = _tid(tenant_id)
    parent = session.scalar(select(Inspection).where(Inspection.public_id == UUID(str(public_id)),
        Inspection.tenant_id == tid, Inspection.is_deleted.is_(False)).with_for_update())
    if parent is None:
        raise HTTPException(404, "Inspection not found")
    if parent.batch_line_id:
        raise HTTPException(409, "Record a batch reinspection from its Quality batch workspace")
    if parent.result != "Fail":
        raise HTTPException(409, "Only a failed inspection can be reopened.")
    parsed_due = None
    if due_date:
        try:
            parsed_due = datetime.date.fromisoformat(due_date)
        except ValueError:
            raise HTTPException(422, "Enter a valid corrective-action due date.")
    child = session.scalar(select(Inspection).where(Inspection.parent_inspection_id == parent.id,
        Inspection.tenant_id == tid, Inspection.is_deleted.is_(False)).order_by(Inspection.id).limit(1))
    if child:
        if (parent.disposition == "Rework") != (mode == "production"):
            raise HTTPException(409, "This failure already has a recovery action. Open its new inspection.")
        return {"inspection": child, "production": production_for_inspection(session, parent)}
    work = production_for_inspection(session, parent)
    if work:
        work = session.scalar(select(ProductionOrder).where(ProductionOrder.id == work.id,
            ProductionOrder.tenant_id == tid).with_for_update())
        po = session.get(PurchaseOrder, work.purchase_order_id) if work.purchase_order_id else None
        if po and po.inventory_received:
            raise HTTPException(409, "This PO has already entered inventory; reconcile received stock before reopening it.")
    if mode == "production":
        if work is None:
            raise HTTPException(409, "This inspection has no production TNA to reopen.")
        lines = [line for line in production.list_lines(session, work.id) if not parent.item or line.name == parent.item]
        if not lines:
            raise HTTPException(409, "Production item not found")
        from app.modules.production.service import STAGES
        for line in lines:
            old = production.list_line_stages(session, line.id)
            specs = [(s.name, s.duration_days, s.worker) for s in old] or [(name, 1, None) for name in STAGES]
            for stage in old:
                stage.is_deleted = True
            cursor = datetime.date.today()
            for seq, (name, days, worker) in enumerate(specs, 1):
                end = cursor + datetime.timedelta(days=days)
                session.add(ProductionStage(tenant_id=tid, order_id=work.id, line_id=line.id,
                    seq=seq, name=name, duration_days=days, worker=worker, status="Pending", start_on=cursor, end_on=end))
                cursor = end
        session.flush()
        production._recompute_order(session, work.id)
    parent.disposition = "Rework" if mode == "production" else "Re-inspection"
    if notes is not None:
        parent.disposition_notes = notes.strip() or None
    if assigned_to is not None:
        parent.assigned_to = assigned_to.strip() or None
    if due_date is not None:
        parent.due_date = parsed_due
    child = repo.create_reinspection(session, tenant_id=tid, parent=parent)
    session.flush()
    return {"inspection": child, "production": work}


def ai_insights(session):
    return repo.defect_insights(session)


# ---------- Completion (auto result) ----------

def complete_inspection(session, *, public_id):
    """Evaluate checklist + AQL and set Pass/Fail. Guards:
      - inspection must be In Progress
      - at least one checklist item
      - no checklist item left Pending
    Returns {error} on a guard failure, else {inspection, result}.
    """
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    if ins.result != "In Progress":
        return {"error": "not_in_progress"}
    if inspection_waits_for_rework(session, ins):
        from fastapi import HTTPException
        raise HTTPException(409, "Complete the reopened production TNA before completing reinspection.")
    checks = repo.list_checks(session, inspection_id=ins.id)
    if not checks:
        return {"error": "no_checks"}
    if any(c.result == "Pending" for c in checks):
        return {"error": "pending_checks"}

    # Result = Fail if any checklist item failed OR the AQL limit is exceeded.
    # The limit is the inspection's max_defects (honours a manual sample-size
    # override); fall back to the lot-based plan for legacy rows.
    checklist_fail = any(c.result == "Fail" for c in checks)
    max_defects = ins.max_defects
    if max_defects is None:
        max_defects = aql_engine.evaluate(ins.prod_qty, ins.aql, ins.defect_count)["maxDefects"]
    aql_exceeded = ins.defect_count > max_defects
    result = "Fail" if (checklist_fail or aql_exceeded) else "Pass"

    import datetime as _dt
    ins.result = result
    ins.finalized_at = _dt.datetime.utcnow()
    session.flush()
    return {"inspection": ins, "result": result,
            "reason": "Checklist failure" if checklist_fail else
                      ("AQL exceeded" if aql_exceeded else "Within acceptance criteria")}


def set_result(session, *, public_id, status, tenant_id=None):
    current = repo.get_inspection(session, public_id=public_id)
    if current is not None and status == "Pass" and current.result == "Pending":
        from fastapi import HTTPException
        raise HTTPException(409, "Start and complete this inspection before recording a pass.")
    if current is not None and current.batch_line_id:
        from fastapi import HTTPException
        raise HTTPException(409, "Batch inspection results are recorded with their QC quantities")
    if current is not None and current.result == "Cancelled" and status != "Cancelled":
        from fastapi import HTTPException
        raise HTTPException(409, "A cancelled inspection cannot be reopened.")
    if current and status == "Pass" and inspection_waits_for_rework(session, current):
        from fastapi import HTTPException
        raise HTTPException(409, "Complete the reopened production TNA before passing reinspection.")
    ins = repo.set_result(session, public_id=public_id, result=status)
    return ins


def pass_and_ship(session, *, tenant_id, public_id, carrier, destination):
    """Mark an inspection Passed and create its shipment with the given carrier
    and destination. Idempotent — one shipment per order; the created (or
    existing) shipment reference is linked back onto the inspection.

    Guard: cannot ship a Pending inspection — it must be started/evaluated first.
    """
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is not None and ins.result == "Cancelled":
        from fastapi import HTTPException
        raise HTTPException(409, "A cancelled inspection cannot ship.")
    if ins is None:
        return None
    if ins.batch_line_id or _batch_managed_order(session, tenant_id, ins.order_ref):
        from fastapi import HTTPException
        raise HTTPException(409, "Manage batch shipments in Shipments after QC quantities are recorded")
    if ins.result == "Pending":
        # Can't create a shipment straight from a never-started inspection.
        return {"error": "not_started", "inspection": ins}
    if inspection_waits_for_rework(session, ins):
        from fastapi import HTTPException
        raise HTTPException(409, "Complete the reopened production TNA before passing reinspection.")
    from app.modules.shipments import repository as ship_repo
    ins = repo.set_result(session, public_id=public_id, result="Pass")

    # Sales shortage production replenishes inventory; it never ships alone.
    from sqlalchemy import select
    from app.models.production import ProductionOrder
    from app.models.procurement import PurchaseOrder
    from app.modules.sales import fulfillment
    source_po = session.scalar(select(PurchaseOrder).join(ProductionOrder,
        ProductionOrder.purchase_order_id == PurchaseOrder.id).where(
        ProductionOrder.order_no == ins.order_ref, ProductionOrder.tenant_id == _tid(tenant_id),
        PurchaseOrder.tenant_id == _tid(tenant_id)))
    if source_po is not None:
        return {"inspection": ins, "shipment_no": None, "awaitingWarehouseReceipt": True}

    # Order-level gate: a shipment is only created once EVERY item's inspection
    # on this order is a FINAL-stage Pass. A passed Pre-Production / In-line item
    # (or a still-open sibling) must not trigger a shipment.
    siblings = repo.inspections_for_order(session, order_ref=ins.order_ref) or [ins]
    all_cleared = all(s.result == "Pass" and s.stage == "Final" for s in siblings)
    if not all_cleared:
        return {"error": "not_all_cleared", "inspection": ins, "shipment_no": None}

    existing = ship_repo.shipment_for_order(session, order_ref=ins.order_ref)
    if existing is None:
        existing = ship_repo.create_shipment(session, tenant_id=_tid(tenant_id), order_ref=ins.order_ref,
                                             carrier=carrier or "Pending", destination=destination or "—")
    # Link the (created or pre-existing) shipment back onto the inspection.
    repo.link_shipment(session, ins=ins, shipment_no=existing.shipment_no)
    return {"inspection": ins, "shipment_no": existing.shipment_no}


_PROGRESS_STEPS = ["Created", "Sampling", "Inspection", "Defect Review", "Decision", "Completed"]
_STATUS_STEP = {"Pending": "Sampling", "In Progress": "Inspection",
                "Fail": "Decision", "Cancelled": "Decision", "Pass": "Completed"}


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def size_breakdown(session, ins):
    """The inspected item's actual production quantities, never current stock."""
    from math import gcd
    from functools import reduce
    from app.models.procurement import PurchaseOrderLine
    from app.models.sales import SalesOrderLine
    from app.modules.sales.service import _size_rank
    if ins.batch_line_id:
        batch_line = session.get(BatchLine, ins.batch_line_id)
        source_line = session.get(PurchaseOrderLine, batch_line.po_line_id)
        size = source_line.size or "—"
        inspected = ins.prod_qty or 0
        return {"sizes": [size], "rows": [{"article": source_line.name,
            "color": source_line.color or "—", "quantities": [inspected], "total": inspected}],
            "totals": [inspected], "total": inspected, "ratio": [1], "source": "Batch"}
    work = production_for_inspection(session, ins)
    if work is None:
        return {"sizes": [], "rows": [], "totals": [], "total": 0, "ratio": [], "source": "Unavailable"}
    if work.purchase_order_id:
        source = "Purchase order"
        lines = session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == work.purchase_order_id,
            PurchaseOrderLine.tenant_id == ins.tenant_id)).all()
    elif work.sales_order_id:
        source = "Sales order"
        lines = session.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == work.sales_order_id,
            SalesOrderLine.tenant_id == ins.tenant_id)).all()
    else:
        source = "Production"
        lines = session.scalars(select(ProductionOrderLine).where(ProductionOrderLine.order_id == work.id,
            ProductionOrderLine.tenant_id == ins.tenant_id, ProductionOrderLine.is_deleted.is_(False))).all()
    groups, size_names = {}, set()
    for line in lines:
        if ins.item and line.name != ins.item:
            continue
        color, size = (line.color or "Unspecified").strip(), (line.size or "Unspecified").strip()
        key = (line.name, color)
        cells = groups.setdefault(key, {})
        cells[size] = cells.get(size, 0) + int(line.qty or 0)
        size_names.add(size)
    sizes = sorted(size_names, key=lambda size: (_size_rank(size), size))
    rows = [{"article": name, "color": color, "quantities": [cells.get(size, 0) for size in sizes],
             "total": sum(cells.values())} for (name, color), cells in groups.items()]
    totals = [sum(row["quantities"][i] for row in rows) for i in range(len(sizes))]
    divisor = reduce(gcd, totals, 0) or 1
    return {"sizes": sizes, "rows": rows, "totals": totals, "total": sum(totals),
            "ratio": [qty // divisor for qty in totals], "source": source}


def _build_workspace(session: Session, ins, *, focus_id: str) -> dict:
    """The full workspace block for a single inspection (one item)."""
    # AQL evaluation. Sample size + max defects come from the inspection record
    # (which honours any manual sample-size override); the lot plan is only a
    # fallback for legacy rows that never had them computed.
    ev = aql_engine.evaluate(ins.prod_qty, ins.aql, ins.defect_count)
    sampled = ins.sample_size or ev["sampleSize"]
    max_defects = ins.max_defects if ins.max_defects is not None else ev["maxDefects"]
    accepted = ins.defect_count <= max_defects
    defect_rate = round((ins.defect_count / sampled) * 100, 1) if sampled else 0.0

    if ins.result in ("Pass", "Fail"):
        evaluation = "PASSED" if ins.result == "Pass" else "FAILED — AQL exceeded"
    elif ins.result == "Cancelled":
        evaluation = "CANCELLED"
    else:
        evaluation = "In review" if ins.result == "In Progress" else "Awaiting start"

    current_step = _STATUS_STEP.get(ins.result, "Sampling")
    done_before = _PROGRESS_STEPS.index(current_step)
    progress = [
        {"key": s, "label": s, "done": i < done_before, "current": i == done_before}
        for i, s in enumerate(_PROGRESS_STEPS)
    ]
    if ins.result == "Pass":   # terminal → all complete
        for p in progress:
            p["done"], p["current"] = True, False

    checks = [
        {"publicId": str(c.public_id), "criterion": c.criterion, "requirement": c.requirement or "",
         "targetValue": float(c.target_value) if c.target_value is not None else None,
         "tolerance": float(c.tolerance) if c.tolerance is not None else None,
         "actual": c.actual or "", "result": c.result, "notes": c.notes or ""}
        for c in repo.list_checks(session, inspection_id=ins.id)
    ]
    defects = [
        {"publicId": str(d.public_id), "defectNo": d.defect_no or "",
         "name": d.defect_name or "—", "category": d.category or "—",
         "severity": d.severity or "—", "qtyAffected": d.qty_affected,
         "location": d.location or "", "description": d.description or "",
         "corrective": d.corrective or "", "imageDocId": d.image_doc_id or None}
        for d in repo.list_inspection_defects(session, inspection_id=ins.id)
    ]

    # Inspection history: siblings on the same order + the parent/child chain.
    historical_rows = repo.inspection_history(session, order_ref=ins.order_ref)
    if ins.batch_line_id:
        batch_id = session.get(BatchLine, ins.batch_line_id).batch_id
        batch_line_ids = [line.id for line in session.scalars(select(BatchLine).where(
            BatchLine.batch_id == batch_id, BatchLine.tenant_id == ins.tenant_id)).all()]
        historical_rows = [dict(public_id=row.public_id, inspection_no=row.inspection_no,
            inspection_date=row.inspection_date, stage=row.stage, inspector=row.inspector,
            result=row.result, defect_count=row.defect_count, aql=row.aql)
            for row in session.scalars(select(Inspection).where(
                Inspection.batch_line_id.in_(batch_line_ids), Inspection.tenant_id == ins.tenant_id,
                Inspection.is_deleted.is_(False)).order_by(Inspection.id.desc())).all()]
    history = [
        {"inspectionNo": h["inspection_no"], "date": _iso(h["inspection_date"]),
         "stage": h["stage"], "inspector": h["inspector"] or "—",
         "result": h["result"], "defects": h["defect_count"], "aql": h["aql"],
         "publicId": str(h["public_id"]), "current": str(h["public_id"]) == focus_id}
        for h in historical_rows
    ]

    return {
            "publicId": str(ins.public_id),
            "batchQuantities": {"assessed": ins.prod_qty or 0, "sampleSize": ins.sample_size or 0,
                "approved": ins.approved_qty or 0, "held": ins.held_qty or 0,
                "rejected": ins.rejected_qty or 0, "findings": ins.disposition_notes or "",
                "kind": ins.inspection_type or "Final"} if ins.batch_line_id else None,
            "sizeBreakdown": size_breakdown(session, ins),
            "item": ins.item or ins.product or "Item",
            "header": {
                "inspectionNo": ins.inspection_no or "—",
                "order": ins.order_ref or "—",
                "product": ins.product or "—",
                "sku": ins.sku or "—",
                "batchLot": ins.batch_lot or "—",
                "stage": ins.stage or "—",
                "inspectionType": ins.inspection_type or "—",
                "inspector": ins.inspector or "—",
                "aql": ins.aql or "—",
                "date": _iso(ins.inspection_date),
                "prodQty": ins.prod_qty,
                "result": ins.result,
            },
            "progress": progress,
            "aql": {
                "aql": ev["aql"], "lotQty": ev["lotQty"], "codeLetter": ev["codeLetter"],
                "sampleSize": sampled, "maxDefects": max_defects,
                "actualDefects": ins.defect_count, "accepted": accepted,
                "result": "Pass" if accepted else "Fail",
            },
            "summary": {
                "sampled": sampled, "defects": ins.defect_count,
                "defectRate": defect_rate,
                "maxDefects": max_defects,
                "evaluation": evaluation,
            },
            "shipmentRef": ins.shipment_ref,
            "canStart": not ins.batch_line_id and ins.result == "Pending" and not inspection_waits_for_rework(session, ins),
            "waitingForTNA": inspection_waits_for_rework(session, ins),
            "canReopenProduction": not ins.batch_line_id and production_for_inspection(session, ins) is not None,
            "canDecide": not ins.batch_line_id and ins.result == "In Progress",
            "canDispose": not ins.batch_line_id and ins.result == "Fail",
            "disposition": ins.disposition,
            "dispositionNotes": ins.disposition_notes or "",
            "assignedTo": ins.assigned_to or "",
            "dueDate": _iso(ins.due_date),
            "dispositionOptions": ["Rework", "Hold", "Scrap", "Return to Vendor", "Re-inspection"],
            "editForm": {
                "inspector": ins.inspector or "", "batchLot": ins.batch_lot or "",
                "inspectionType": ins.inspection_type or "", "aql": ins.aql or "2.5",
                "sampleSize": str(sampled or ""),
            },
            "aqlOptions": list(aql_engine.SUPPORTED_AQL),
            "typeOptions": ["First Article", "In-line", "Final QC", "Pre-Shipment"],
            "checks": checks,
            "defects": defects,
            "history": history,
    }


def _order_status(items: list[dict]) -> tuple[str, str]:
    """Roll a group of item-inspection results into one order-level status."""
    results = [it["header"]["result"] for it in items]
    if any(r == "Fail" for r in results):
        return "Attention", "red"
    if any(r == "In Progress" for r in results):
        return "In Progress", "navy"
    if any(r == "Pending" for r in results):
        return "Pending", "amber"
    if results and all(r == "Pass" for r in results):
        return "Passed", "green"
    return (results[0] if results else "—"), "neutral"


def inspection_detail(session: Session, *, public_id: str) -> dict | None:
    """Per-ORDER inspection page: one workspace tab per item (production line),
    all grouped under the order the clicked inspection belongs to."""
    focus = repo.get_inspection(session, public_id=public_id)
    if focus is None:
        return None
    batch = session.get(ProductionBatch, session.get(BatchLine, focus.batch_line_id).batch_id) if focus.batch_line_id else None
    if focus.batch_line_id:
        source_line = session.get(BatchLine, focus.batch_line_id)
        related_ids = [line.id for line in session.scalars(select(BatchLine).where(
            BatchLine.batch_id == source_line.batch_id, BatchLine.tenant_id == focus.tenant_id)).all()]
        group = session.scalars(select(Inspection).where(Inspection.batch_line_id.in_(related_ids),
            Inspection.tenant_id == focus.tenant_id, Inspection.is_deleted.is_(False)).order_by(Inspection.id)).all()
    else:
        group = repo.inspections_for_order(session, order_ref=focus.order_ref) or [focus]
    items = [_build_workspace(session, ins, focus_id=public_id) for ins in group]
    covered = {it["item"] for it in items}

    # Every ITEM of the order gets a tab. Items whose production isn't finished
    # yet (no inspection) show a "production not completed" placeholder — the
    # order's inspection group is complete only once all of them are inspected.
    order_names = [] if focus.batch_line_id else [oi["item"] for oi in order_items(session, focus.order_ref)]
    placeholders = [
        {"publicId": f"pending::{n}", "item": n, "placeholder": True,
         "header": {"inspectionNo": "—", "order": focus.order_ref or "—", "product": n,
                    "sku": "—", "batchLot": "—", "stage": "—", "inspectionType": "—",
                    "inspector": "—", "aql": "—", "date": None, "prodQty": None,
                    "result": "In production"}}
        for n in order_names if n not in covered
    ]
    all_items = items + placeholders

    # Unique tab labels (append the QC number when two share a name).
    label_counts: dict[str, int] = {}
    for it in all_items:
        label_counts[it["item"]] = label_counts.get(it["item"], 0) + 1
    seen: dict[str, int] = {}
    for it in all_items:
        base = it["item"]
        if label_counts[base] > 1 and not it.get("placeholder"):
            it["tabLabel"] = f"{base} · {it['header']['inspectionNo']}"
        else:
            seen[base] = seen.get(base, 0) + 1
            it["tabLabel"] = base if seen[base] == 1 else f"{base} ({seen[base]})"

    # Historical failures remain visible, but only each item's latest run
    # contributes to current order status and clearance.
    latest = {}
    for it in items:
        if it["header"]["result"] != "Cancelled":
            latest[(it["item"], it["header"]["stage"])] = it
    current_items = list(latest.values())
    # Show current runs as working tabs. Direct links retain historical details.
    all_items = current_items + placeholders
    focused = next((it for it in items if it["publicId"] == str(public_id)), None)
    if focused and focused not in current_items:
        focused["historical"] = True
        focused["tabLabel"] = f"History · {focused['header']['inspectionNo']}"
        all_items.insert(0, focused)
    counts = {}
    for it in current_items:
        counts[it["item"]] = counts.get(it["item"], 0) + 1
    for it in current_items:
        it["tabLabel"] = it["item"] if counts[it["item"]] == 1 else f"{it['item']} · {it['header']['stage']}"
    status_label, status_tone = _order_status(current_items + placeholders)
    # "Cleared" for shipment = a FINAL-stage Pass inspection (not a placeholder,
    # not Pre-Production/In-line). Every order item must be cleared to ship.
    def _cleared(it: dict) -> bool:
        return (not it.get("placeholder")
                and it["header"]["result"] == "Pass" and it["header"]["stage"] == "Final")
    if order_names:
        cleared_names = {it["item"] for it in current_items if _cleared(it)}
        total = len(set(order_names))
        passed = len(set(order_names) & cleared_names)
        all_passed = not placeholders and set(order_names).issubset(cleared_names)
    else:
        total = len(current_items)
        passed = sum(1 for it in current_items if _cleared(it))
        all_passed = bool(current_items) and passed == total
    ship_ref = next((it["shipmentRef"] for it in items if it.get("shipmentRef")), None)
    from app.models.procurement import PurchaseOrder
    from app.models.production import ProductionOrder
    source_po = session.scalar(select(PurchaseOrder).join(ProductionOrder,
        ProductionOrder.purchase_order_id == PurchaseOrder.id).where(
        ProductionOrder.order_no == focus.order_ref, ProductionOrder.tenant_id == focus.tenant_id,
        PurchaseOrder.tenant_id == focus.tenant_id))
    ship_target = next((it["publicId"] for it in current_items if _cleared(it)),
                       items[0]["publicId"] if items else None)
    return {
        "variant": "inspection",
        "batchManaged": bool(focus.batch_line_id),
        "ref": focus.order_ref or "—",
        "title": f"Inspection · {focus.order_ref or '—'}{f' / {batch.name}' if batch else ''}",
        "batchId": str(batch.public_id) if batch else None,
        "statusLabel": status_label,
        "statusTone": status_tone,
        "meta": ([{"k": "Batch", "v": batch.name},
                  {"k": "Assessed", "v": str(focus.prod_qty or 0)},
                  {"k": "Approved", "v": str(focus.approved_qty or 0)},
                  {"k": "Hold", "v": str(focus.held_qty or 0)},
                  {"k": "Rejected", "v": str(focus.rejected_qty or 0)}] if batch else []) + [
            {"k": "Order", "v": focus.order_ref or "—"},
            {"k": "Items", "v": str(len({it["item"] for it in current_items + placeholders}))},
            {"k": "Passed", "v": f"{passed}/{total}"},
            {"k": "Stage", "v": focus.stage or "—"},
        ],
        "tabs": [it["tabLabel"] for it in all_items],
        "inspection": {
            "items": all_items,
            # Shipment is order-level: unlocked only when EVERY item is a
            # Final-stage pass (idempotent /pass links the shipment back).
            "shipment": {
                "replenishmentPO": {"publicId": str(source_po.public_id), "poNo": source_po.po_no,
                                    "received": source_po.inventory_received} if source_po else None,
                "allPassed": all_passed,
                "passedCount": passed,
                "total": total,
                "shipmentRef": ship_ref,
                "inspectionId": ship_target,
            },
        },
    }


# ---------- Defect types ----------

def defects_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_defects(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], strong=True),
            r["category"] or "—",
            text_cell(r["severity"] or "—", badge=_SEV_TONE.get(r["severity"], "neutral")),
            text_cell(f"{r['frequency']}%", align="right", mono=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Defect"}, {"label": "Category"}, {"label": "Severity"},
                 {"label": "Frequency", "align": "right"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "category": r["category"], "severity": r["severity"]}
                 for r in rows],
        search="Search defects…", action="New defect type", filters=["Category", "Severity"],
    )


def _defect_fields(p) -> dict:
    return {"name": p.name, "category": p.category, "severity": p.severity}


def create_defect(session, *, tenant_id, payload: DefectTypeCreate):
    return repo.create_defect(session, tenant_id=_tid(tenant_id), **_defect_fields(payload))


def update_defect(session, *, public_id, payload: DefectTypeUpdate):
    return repo.update_defect(session, public_id=public_id, **_defect_fields(payload))

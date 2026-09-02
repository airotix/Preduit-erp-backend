"""Quality business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.production import ProductionOrder, ProductionOrderLine
from app.modules.quality import aql as aql_engine
from app.modules.quality import repository as repo
from app.modules.quality.dto import DefectTypeCreate, DefectTypeUpdate, InspectionCreate
from app.presenters.screen import list_config, text_cell

_RESULT_TONE = {"Pass": "green", "Fail": "red", "Pending": "amber",
                "In Progress": "navy", "Cancelled": "neutral"}
_SEV_TONE = {"Critical": "red", "Major": "red", "Minor": "amber"}


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
        key = r["order_ref"] or f"__{r['public_id']}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    grid, ids, records = [], [], []
    for key in order:
        g = groups[key]
        order_ref = g[0]["order_ref"] or "—"
        items = [x["item"] or x["product"] or "Item" for x in g]
        uniq_items = list(dict.fromkeys(items))
        prod = uniq_items[0] + (f" +{len(uniq_items) - 1} more" if len(uniq_items) > 1 else "")
        inspectors = list(dict.fromkeys([x["inspector"] for x in g if x["inspector"]]))
        defects = sum(int(x["defect_count"] or 0) for x in g)
        label = _group_status([x["result"] for x in g], [x["stage"] for x in g])
        grid.append([
            text_cell(order_ref, avatar=True, sub=f"{len(g)} item{'s' if len(g) != 1 else ''}"),
            order_ref,
            prod,
            text_cell(inspectors[0] + (f" +{len(inspectors) - 1}" if len(inspectors) > 1 else "") if inspectors else "—"),
            text_cell(str(defects), align="center", mono=True),
            text_cell(label, badge=_GROUP_TONE.get(label, "neutral")),
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
    """Pending → In Progress. Guard: only a Pending inspection can be started."""
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
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    return repo.add_defect(session, tenant_id=_tid(tenant_id), ins=ins,
                           defect_type_public=payload.defectTypeId, defect_name=payload.defectName,
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
    if ins.result != "Fail":
        return {"error": "not_failed"}
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
    """Create a new Pending inspection linked to a failed original."""
    parent = repo.get_inspection(session, public_id=public_id)
    if parent is None:
        return None
    if parent.result != "Fail":
        return {"error": "not_failed"}
    child = repo.create_reinspection(session, tenant_id=_tid(tenant_id), parent=parent)
    # Mark the original's disposition as Re-inspection if not already dispositioned.
    if not parent.disposition:
        repo.set_disposition(session, public_id=public_id, disposition="Re-inspection",
                             notes=parent.disposition_notes, assigned_to=parent.assigned_to,
                             due_date=parent.due_date)
    return {"inspection": child}


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


def set_result(session, *, public_id, status):
    return repo.set_result(session, public_id=public_id, result=status)


def pass_and_ship(session, *, tenant_id, public_id, carrier, destination):
    """Mark an inspection Passed and create its shipment with the given carrier
    and destination. Idempotent — one shipment per order; the created (or
    existing) shipment reference is linked back onto the inspection.

    Guard: cannot ship a Pending inspection — it must be started/evaluated first.
    """
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    if ins.result == "Pending":
        # Can't create a shipment straight from a never-started inspection.
        return {"error": "not_started", "inspection": ins}
    from app.modules.shipments import repository as ship_repo
    ins = repo.set_result(session, public_id=public_id, result="Pass")

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
    history = [
        {"inspectionNo": h["inspection_no"], "date": _iso(h["inspection_date"]),
         "stage": h["stage"], "inspector": h["inspector"] or "—",
         "result": h["result"], "defects": h["defect_count"], "aql": h["aql"],
         "publicId": str(h["public_id"]), "current": str(h["public_id"]) == focus_id}
        for h in repo.inspection_history(session, order_ref=ins.order_ref)
    ]

    return {
            "publicId": str(ins.public_id),
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
            "canStart": ins.result == "Pending",
            "canDecide": ins.result == "In Progress",
            "canDispose": ins.result == "Fail",
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
    group = repo.inspections_for_order(session, order_ref=focus.order_ref) or [focus]
    items = [_build_workspace(session, ins, focus_id=public_id) for ins in group]
    covered = {it["item"] for it in items}

    # Every ITEM of the order gets a tab. Items whose production isn't finished
    # yet (no inspection) show a "production not completed" placeholder — the
    # order's inspection group is complete only once all of them are inspected.
    order_names = [oi["item"] for oi in order_items(session, focus.order_ref)]
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

    status_label, status_tone = _order_status(all_items)
    # "Cleared" for shipment = a FINAL-stage Pass inspection (not a placeholder,
    # not Pre-Production/In-line). Every order item must be cleared to ship.
    def _cleared(it: dict) -> bool:
        return (not it.get("placeholder")
                and it["header"]["result"] == "Pass" and it["header"]["stage"] == "Final")
    if order_names:
        cleared_names = {it["item"] for it in items if _cleared(it)}
        total = len(set(order_names))
        passed = len(set(order_names) & cleared_names)
        all_passed = not placeholders and set(order_names).issubset(cleared_names)
    else:
        total = len(items)
        passed = sum(1 for it in items if _cleared(it))
        all_passed = bool(items) and passed == total
    ship_ref = next((it["shipmentRef"] for it in items if it.get("shipmentRef")), None)
    ship_target = next((it["publicId"] for it in items if _cleared(it)),
                       items[0]["publicId"] if items else None)
    return {
        "variant": "inspection",
        "ref": focus.order_ref or "—",
        "title": f"Inspection · {focus.order_ref or '—'}",
        "statusLabel": status_label,
        "statusTone": status_tone,
        "meta": [
            {"k": "Order", "v": focus.order_ref or "—"},
            {"k": "Items", "v": str(len(all_items))},
            {"k": "Passed", "v": f"{passed}/{total}"},
            {"k": "Stage", "v": focus.stage or "—"},
        ],
        "tabs": [it["tabLabel"] for it in all_items],
        "inspection": {
            "items": all_items,
            # Shipment is order-level: unlocked only when EVERY item is a
            # Final-stage pass (idempotent /pass links the shipment back).
            "shipment": {
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

"""Quality business logic → frontend ScreenConfig."""
import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.quality import repository as repo
from app.modules.quality.dto import DefectTypeCreate, DefectTypeUpdate, InspectionCreate
from app.presenters.screen import list_config, text_cell

_RESULT_TONE = {"Pass": "green", "Fail": "red", "Pending": "amber"}
_SEV_TONE = {"Major": "red", "Minor": "amber"}


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


# ---------- Inspections ----------

def inspections_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_inspections(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(f"{r['inspection_no']} · {r['stage']}", avatar=True, sub=f"AQL {r['aql']}"),
            r["order_ref"] or "—",
            text_cell(r["stage"], badge="navy"),
            text_cell(str(r["defect_count"]), align="center", mono=True),
            text_cell(r["aql"] or "—", align="center", mono=True),
            text_cell(r["result"], badge=_RESULT_TONE.get(r["result"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Inspection"}, {"label": "Order"}, {"label": "Stage"},
                 {"label": "Defects", "align": "center"}, {"label": "AQL", "align": "center"},
                 {"label": "Result"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["result"]} for r in rows],
        search="Search inspections…", action="New inspection", filters=["Stage", "Result"],
    )


def create_inspection(session, *, tenant_id, payload: InspectionCreate):
    return repo.create_inspection(session, tenant_id=_tid(tenant_id), order_ref=payload.order,
                                  stage=payload.stage, aql=payload.aql)


def set_result(session, *, public_id, status):
    return repo.set_result(session, public_id=public_id, result=status)


def inspection_detail(session: Session, *, public_id: str) -> dict | None:
    ins = repo.get_inspection(session, public_id=public_id)
    if ins is None:
        return None
    result_done = ins.result != "Pending"
    timeline = [
        {"icon": "clipboard-list", "tone": "navy", "title": "Inspection opened",
         "time": ins.inspection_no or "", "done": True},
        {"icon": "search", "tone": "amber", "title": f"Sampling · AQL {ins.aql}",
         "time": ins.stage, "done": True},
        {"icon": "alert-triangle", "tone": "amber" if ins.defect_count else "neutral",
         "title": f"{ins.defect_count} defect(s) logged", "time": "", "done": ins.defect_count > 0},
        {"icon": "check-circle-2" if ins.result == "Pass" else "x-circle",
         "tone": _RESULT_TONE.get(ins.result, "neutral"),
         "title": f"Result · {ins.result}", "time": ins.inspector or "", "done": result_done},
    ]
    return {
        "variant": "generic",
        "ref": ins.inspection_no or "—",
        "title": f"{ins.stage} inspection · {ins.order_ref or '—'}",
        "statusLabel": ins.result,
        "statusTone": _RESULT_TONE.get(ins.result, "neutral"),
        "meta": [
            {"k": "Order", "v": ins.order_ref or "—"},
            {"k": "AQL", "v": ins.aql or "—"},
            {"k": "Defects", "v": str(ins.defect_count)},
            {"k": "Inspector", "v": ins.inspector or "—"},
        ],
        "tabs": ["Checklist", "Defects", "Photos"],
        "generic": {"timeline": timeline},
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

"""Quality HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_module, require_permission
from app.modules.quality import service
from app.modules.quality import workflow
from app.modules.quality.dto import (
    CheckIn, DecideTask, DefectIn, DefectTypeCreate, DefectTypeUpdate, QualitySettingsIn, ResolveTask,
    StartTask, ReopenIn,
)

router = APIRouter(prefix="/quality", tags=["quality"])

read = require_module("quality", "read")
write = require_module("quality", "write")


@router.get("/settings")
def quality_settings(principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.get_settings(db, tenant_id=principal.tenant_id, permissions=principal.permissions)


@router.put("/settings")
def update_quality_settings(payload: QualitySettingsIn,
                            principal: Principal = Depends(require_permission("admin.settings")),
                            db: Session = Depends(tenant_db)):
    return service.update_settings(db, tenant_id=principal.tenant_id, payload=payload, actor=principal.email)


@router.get("/work-queue")
def work_queue(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return workflow.work_queue(db, _.tenant_id)


@router.get("/tasks")
def quality_task(task: str = Query(...), principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return workflow.get_task(db, principal.tenant_id, task, principal.permissions)


@router.post("/tasks/start")
def start_task(payload: StartTask, principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return workflow.start_task(db, tenant_id=principal.tenant_id, actor=principal.email,
                               permissions=principal.permissions, key=payload.taskKey, payload=payload)


@router.post("/tasks/decide")
def decide_task(payload: DecideTask, principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return workflow.decide_task(db, tenant_id=principal.tenant_id, permissions=principal.permissions,
                                key=payload.taskKey, payload=payload)


@router.post("/tasks/resolve")
def resolve_task(payload: ResolveTask, principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return workflow.resolve_hold(db, tenant_id=principal.tenant_id, actor=principal.email,
                                 key=payload.taskKey, payload=payload)


@router.get("/inspections/screen")
def inspections_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                       _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.inspections_screen(db, limit=limit, offset=offset)


@router.get("/defect-options")
def defect_options(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return {"options": service.defect_options(db)}


@router.post("/inspections/{public_id}/checks", status_code=status.HTTP_201_CREATED)
def add_check(public_id: str, payload: CheckIn, principal: Principal = Depends(write),
              db: Session = Depends(tenant_db)):
    c = service.add_check(db, tenant_id=principal.tenant_id, public_id=public_id, payload=payload)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return {"public_id": str(c.public_id), "result": c.result}


@router.put("/checks/{check_id}")
def update_check(check_id: str, payload: CheckIn, principal: Principal = Depends(write),
                 db: Session = Depends(tenant_db)):
    c = service.update_check(db, check_public_id=check_id, payload=payload)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist item not found")
    return {"public_id": str(c.public_id), "result": c.result}


@router.delete("/checks/{check_id}")
def delete_check(check_id: str, principal: Principal = Depends(write),
                 db: Session = Depends(tenant_db)):
    if not service.delete_check(db, check_public_id=check_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checklist item not found")
    return {"ok": True}


@router.post("/inspections/{public_id}/defects", status_code=status.HTTP_201_CREATED)
def add_defect(public_id: str, payload: DefectIn, principal: Principal = Depends(write),
               db: Session = Depends(tenant_db)):
    d = service.add_defect(db, tenant_id=principal.tenant_id, public_id=public_id, payload=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return {"public_id": str(d.public_id), "defect_no": d.defect_no}


@router.delete("/defects/log/{defect_id}")
def delete_defect(defect_id: str, principal: Principal = Depends(write),
                  db: Session = Depends(tenant_db)):
    ins = service.delete_defect(db, defect_public_id=defect_id)
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Defect not found")
    return {"ok": True}


@router.get("/inspections/{public_id}/detail")
def inspection_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.inspection_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return d


@router.post("/inspections/{public_id}/reopen", status_code=status.HTTP_201_CREATED)
def reopen_failed(public_id: str, payload: ReopenIn, principal: Principal = Depends(write),
                  db: Session = Depends(tenant_db)):
    if payload.mode == "production":
        require_module("production", "write")(principal)
    out = service.reopen_failed(db, tenant_id=principal.tenant_id, public_id=public_id, mode=payload.mode,
        notes=payload.notes, assigned_to=payload.assignedTo, due_date=payload.dueDate)
    ins, work = out["inspection"], out["production"]
    return {"publicId": str(ins.public_id), "inspectionNo": ins.inspection_no,
            "productionId": str(work.public_id) if work else None}


@router.get("/insights")
def quality_insights(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.ai_insights(db)


@router.get("/defects/screen")
def defects_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                   _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.defects_screen(db, limit=limit, offset=offset)


@router.post("/defects", status_code=status.HTTP_201_CREATED)
def create_defect(payload: DefectTypeCreate, principal: Principal = Depends(write),
                  db: Session = Depends(tenant_db)):
    d = service.create_defect(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(d.public_id), "name": d.name}


@router.put("/defects/{public_id}")
def update_defect(public_id: str, payload: DefectTypeUpdate,
                  principal: Principal = Depends(write),
                  db: Session = Depends(tenant_db)):
    d = service.update_defect(db, public_id=public_id, payload=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Defect type not found")
    return {"public_id": str(d.public_id), "name": d.name}

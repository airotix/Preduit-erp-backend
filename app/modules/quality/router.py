"""Quality HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.quality import service
from app.modules.quality.dto import (
    CheckIn, DefectIn, DefectTypeCreate, DefectTypeUpdate, DispositionIn,
    InspectionCreate, InspectionHeaderUpdate, PassShipIn, StatusUpdate,
)

router = APIRouter(prefix="/quality", tags=["quality"])

read = require_module("quality", "read")
write = require_module("quality", "write")


@router.get("/inspections/screen")
def inspections_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                       _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.inspections_screen(db, limit=limit, offset=offset)


@router.post("/inspections", status_code=status.HTTP_201_CREATED)
def create_inspection(payload: InspectionCreate, principal: Principal = Depends(write),
                      db: Session = Depends(tenant_db)):
    ins = service.create_inspection(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(ins.public_id), "inspection_no": ins.inspection_no}


@router.post("/inspections/{public_id}/status")
def inspection_result(public_id: str, payload: StatusUpdate,
                      principal: Principal = Depends(write),
                      db: Session = Depends(tenant_db)):
    ins = service.set_result(db, public_id=public_id, status=payload.status,
                             tenant_id=principal.tenant_id)
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return {"public_id": str(ins.public_id), "result": ins.result}


@router.put("/inspections/{public_id}")
def update_inspection(public_id: str, payload: InspectionHeaderUpdate,
                      principal: Principal = Depends(write),
                      db: Session = Depends(tenant_db)):
    ins = service.update_inspection(db, public_id=public_id, payload=payload)
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return {"public_id": str(ins.public_id)}


@router.post("/inspections/{public_id}/start")
def start_inspection(public_id: str, principal: Principal = Depends(write),
                     db: Session = Depends(tenant_db)):
    ins = service.start_inspection(db, public_id=public_id, tenant_id=principal.tenant_id)
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return {"public_id": str(ins.public_id), "result": ins.result}


@router.get("/defect-options")
def defect_options(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return {"options": service.defect_options(db)}


@router.get("/order-items")
def order_items(order: str = Query(...), _: Principal = Depends(read),
                db: Session = Depends(tenant_db)):
    """Items (production lines) for an order — feeds the New inspection Item picker."""
    return {"items": service.order_items(db, order)}


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


@router.post("/inspections/{public_id}/complete")
def complete_inspection(public_id: str, principal: Principal = Depends(write),
                        db: Session = Depends(tenant_db)):
    out = service.complete_inspection(db, public_id=public_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    err = out.get("error")
    if err == "not_in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an in-progress inspection can be completed.")
    if err == "no_checks":
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one checklist item before completing.")
    if err == "pending_checks":
        raise HTTPException(status.HTTP_409_CONFLICT, "Resolve every checklist item (no Pending) before completing.")
    ins = out["inspection"]
    return {"public_id": str(ins.public_id), "result": out["result"], "reason": out["reason"]}


@router.post("/inspections/{public_id}/pass")
def pass_and_ship(public_id: str, payload: PassShipIn,
                  principal: Principal = Depends(write),
                  db: Session = Depends(tenant_db)):
    out = service.pass_and_ship(db, tenant_id=principal.tenant_id, public_id=public_id,
                                carrier=payload.carrier, destination=payload.destination)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    if out.get("error") == "not_started":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Start the inspection before passing it and creating a shipment.")
    if out.get("error") == "not_all_cleared":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "All items must have a Final-stage pass before the order can be shipped.")
    ins = out["inspection"]
    return {"public_id": str(ins.public_id), "result": ins.result,
            "shipment_no": out.get("shipment_no")}


@router.get("/inspections/{public_id}/detail")
def inspection_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.inspection_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return d


@router.post("/inspections/{public_id}/disposition")
def set_disposition(public_id: str, payload: DispositionIn, principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    out = service.set_disposition(db, public_id=public_id, payload=payload)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    if out.get("error") == "not_failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a failed inspection can be dispositioned.")
    ins = out["inspection"]
    return {"public_id": str(ins.public_id), "disposition": ins.disposition}


@router.post("/inspections/{public_id}/reinspect", status_code=status.HTTP_201_CREATED)
def create_reinspection(public_id: str, principal: Principal = Depends(write),
                        db: Session = Depends(tenant_db)):
    out = service.create_reinspection(db, tenant_id=principal.tenant_id, public_id=public_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    if out.get("error") == "not_failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a failed inspection can be re-inspected.")
    ins = out["inspection"]
    return {"public_id": str(ins.public_id), "inspection_no": ins.inspection_no}


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

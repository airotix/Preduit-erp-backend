"""Quality HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.quality import service
from app.modules.quality.dto import (
    DefectTypeCreate, DefectTypeUpdate, InspectionCreate, StatusUpdate,
)

router = APIRouter(prefix="/quality", tags=["quality"])


@router.get("/inspections/screen")
def inspections_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                       db: Session = Depends(tenant_db)):
    return service.inspections_screen(db, limit=limit, offset=offset)


@router.post("/inspections", status_code=status.HTTP_201_CREATED)
def create_inspection(payload: InspectionCreate, principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    ins = service.create_inspection(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(ins.public_id), "inspection_no": ins.inspection_no}


@router.post("/inspections/{public_id}/status")
def inspection_result(public_id: str, payload: StatusUpdate,
                      principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    ins = service.set_result(db, public_id=public_id, status=payload.status)
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return {"public_id": str(ins.public_id), "result": ins.result}


@router.get("/inspections/{public_id}/detail")
def inspection_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.inspection_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return d


@router.get("/defects/screen")
def defects_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                   db: Session = Depends(tenant_db)):
    return service.defects_screen(db, limit=limit, offset=offset)


@router.post("/defects", status_code=status.HTTP_201_CREATED)
def create_defect(payload: DefectTypeCreate, principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    d = service.create_defect(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(d.public_id), "name": d.name}


@router.put("/defects/{public_id}")
def update_defect(public_id: str, payload: DefectTypeUpdate,
                  principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    d = service.update_defect(db, public_id=public_id, payload=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Defect type not found")
    return {"public_id": str(d.public_id), "name": d.name}

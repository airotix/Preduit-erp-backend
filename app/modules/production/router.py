"""Production HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.production import service
from app.modules.production.dto import (
    BomCreate, BomUpdate, ProductionOrderCreate, ShipOrderIn, StageAssignIn,
    StageExtendIn, StageNotesIn, StartProductionIn, StatusUpdate,
)

router = APIRouter(prefix="/production", tags=["production"])


@router.get("/porders/screen")
def porders_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                   db: Session = Depends(tenant_db)):
    return service.porders_screen(db, limit=limit, offset=offset)


@router.post("/porders", status_code=status.HTTP_201_CREATED)
def create_porder(payload: ProductionOrderCreate, principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    po = service.create_porder(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(po.public_id), "order_no": po.order_no}


@router.post("/porders/{public_id}/status")
def porder_stage(public_id: str, payload: StatusUpdate,
                 principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    po = service.set_stage(db, public_id=public_id, status=payload.status)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Production order not found")
    return {"public_id": str(po.public_id), "stage": po.stage}


@router.get("/porders/{public_id}/detail")
def porder_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.porder_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Production order not found")
    return d


@router.post("/porders/{public_id}/start")
def start_production(public_id: str, payload: StartProductionIn,
                     principal: Principal = Depends(require_tenant),
                     db: Session = Depends(tenant_db)):
    po = service.start_production(db, tenant_id=principal.tenant_id, public_id=public_id,
                                  stages=[s.model_dump() for s in payload.stages],
                                  line_id=payload.line_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Production order not found")
    return {"public_id": str(po.public_id), "stage": po.stage}


@router.post("/porders/{public_id}/inspect")
def send_for_inspection(public_id: str, principal: Principal = Depends(require_tenant),
                        db: Session = Depends(tenant_db)):
    po = service.send_for_inspection(db, tenant_id=principal.tenant_id, public_id=public_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Production order not found")
    return {"public_id": str(po.public_id)}


@router.post("/porders/{public_id}/ship")
def ship_order(public_id: str, payload: ShipOrderIn,
               principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    s = service.ship_order(db, tenant_id=principal.tenant_id, public_id=public_id,
                           carrier=payload.carrier, eta=payload.eta, destination=payload.destination)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Production order not found")
    return {"public_id": str(s.public_id), "shipment_no": s.shipment_no}


def _stage_result(s):
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stage not found")
    return {"public_id": str(s.public_id), "status": s.status}


@router.post("/stages/{public_id}/start")
def stage_start(public_id: str, principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    return _stage_result(service.stage_action(db, action="start", public_id=public_id))


@router.post("/stages/{public_id}/complete")
def stage_complete(public_id: str, principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    return _stage_result(service.stage_action(db, action="complete", public_id=public_id))


@router.post("/stages/{public_id}/resolve")
def stage_resolve(public_id: str, principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    return _stage_result(service.stage_action(db, action="resolve", public_id=public_id))


@router.post("/stages/{public_id}/extend")
def stage_extend(public_id: str, payload: StageExtendIn,
                 principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    return _stage_result(service.stage_action(db, action="extend", public_id=public_id, days=payload.days))


@router.post("/stages/{public_id}/assign")
def stage_assign(public_id: str, payload: StageAssignIn,
                 principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    return _stage_result(service.stage_action(db, action="assign", public_id=public_id, worker=payload.worker))


@router.post("/stages/{public_id}/notes")
def stage_notes(public_id: str, payload: StageNotesIn,
                principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    return _stage_result(service.stage_action(db, action="notes", public_id=public_id, notes=payload.notes))


@router.get("/pboard/screen")
def pboard_screen(db: Session = Depends(tenant_db)):
    return service.board_screen(db)


@router.get("/bom/{public_id}/detail")
def bom_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.bom_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BOM line not found")
    return d


@router.get("/bom/screen")
def bom_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(tenant_db)):
    return service.bom_screen(db, limit=limit, offset=offset)


@router.post("/bom", status_code=status.HTTP_201_CREATED)
def create_bom(payload: BomCreate, principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    b = service.create_bom(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(b.public_id), "component": b.component}


@router.put("/bom/{public_id}")
def update_bom(public_id: str, payload: BomUpdate,
               principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    b = service.update_bom(db, public_id=public_id, payload=payload)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BOM line not found")
    return {"public_id": str(b.public_id), "component": b.component}

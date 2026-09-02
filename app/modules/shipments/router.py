"""Shipments HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.shipments import service
from app.modules.shipments.dto import (
    CarrierCreate, CarrierUpdate, ShipmentCreate, ShipmentUpdate, StatusUpdate,
)

router = APIRouter(prefix="/shipments", tags=["shipments"])

read = require_module("shipments", "read")
write = require_module("shipments", "write")


@router.get("/shipments/screen")
def shipments_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.shipments_screen(db, limit=limit, offset=offset)


@router.post("/shipments", status_code=status.HTTP_201_CREATED)
def create_shipment(payload: ShipmentCreate, principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    s = service.create_shipment(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(s.public_id), "shipment_no": s.shipment_no}


@router.post("/shipments/{public_id}/status")
def shipment_status(public_id: str, payload: StatusUpdate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    s = service.set_status(db, public_id=public_id, status=payload.status)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    return {"public_id": str(s.public_id), "status": s.status}


@router.put("/shipments/{public_id}")
def update_shipment(public_id: str, payload: ShipmentUpdate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    s = service.update_shipment(db, public_id=public_id, payload=payload)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    return {"public_id": str(s.public_id), "status": s.status}


@router.get("/shipments/{public_id}/detail")
def shipment_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.shipment_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    return d


@router.get("/carriers/search")
def carriers_search(q: str = Query(""), _: Principal = Depends(read),
                    db: Session = Depends(tenant_db)):
    """Carrier name suggestions for shipment carrier pickers."""
    return {"carriers": service.search_carriers(db, q=q)}


@router.get("/carriers/screen")
def carriers_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                    _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.carriers_screen(db, limit=limit, offset=offset)


@router.post("/carriers", status_code=status.HTTP_201_CREATED)
def create_carrier(payload: CarrierCreate, principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    c = service.create_carrier(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(c.public_id), "name": c.name}


@router.put("/carriers/{public_id}")
def update_carrier(public_id: str, payload: CarrierUpdate,
                   principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    c = service.update_carrier(db, public_id=public_id, payload=payload)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carrier not found")
    return {"public_id": str(c.public_id), "name": c.name}

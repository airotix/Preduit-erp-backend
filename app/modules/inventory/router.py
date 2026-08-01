"""Inventory HTTP routes."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.inventory import service
from app.modules.inventory.dto import (
    LocationCreate, LocationUpdate, MatrixUpdate, ReorderAlertCreate, StatusUpdate,
    StockReceiptCreate, TransferCreate,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/stock/screen")
def stock_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(tenant_db)):
    return service.stock_screen(db, limit=limit, offset=offset)


@router.post("/stock", status_code=status.HTTP_201_CREATED)
def create_stock(payload: StockReceiptCreate,
                 principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    s = service.create_stock_receipt(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(s.public_id), "on_hand": s.on_hand}


@router.get("/stock/{public_id}/detail")
def stock_article_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.stock_article_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    return d


@router.put("/stock/{public_id}/matrix")
def save_stock_matrix(public_id: str, payload: MatrixUpdate,
                      principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    d = service.save_article_matrix(db, tenant_id=principal.tenant_id,
                                    public_id=public_id, payload=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    return d


@router.get("/locations/screen")
def locations_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     db: Session = Depends(tenant_db)):
    return service.locations_screen(db, limit=limit, offset=offset)


@router.post("/locations", status_code=status.HTTP_201_CREATED)
def create_location(payload: LocationCreate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    loc = service.create_location(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(loc.public_id), "name": loc.name}


@router.put("/locations/{public_id}")
def update_location(public_id: str, payload: LocationUpdate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    loc = service.update_location(db, public_id=public_id, payload=payload)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
    return {"public_id": str(loc.public_id), "name": loc.name}


@router.get("/transfers/screen")
def transfers_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     db: Session = Depends(tenant_db)):
    return service.transfers_screen(db, limit=limit, offset=offset)


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
def create_transfer(payload: TransferCreate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    t = service.create_transfer(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(t.public_id), "transfer_no": t.transfer_no}


@router.post("/transfers/{public_id}/status")
def transfer_status(public_id: str, payload: StatusUpdate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    t = service.set_transfer_status(db, public_id=public_id, status=payload.status)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer not found")
    return {"public_id": str(t.public_id), "status": t.status}


@router.get("/alerts/screen")
def alerts_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                  db: Session = Depends(tenant_db)):
    return service.alerts_screen(db, limit=limit, offset=offset)


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
def create_alert(payload: ReorderAlertCreate,
                 principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    a = service.create_alert(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(a.public_id), "sku": a.sku}

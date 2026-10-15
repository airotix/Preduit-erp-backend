"""Inventory HTTP routes."""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.inventory import service
from app.modules.inventory.dto import (
    LocationCreate, LocationUpdate, MatrixUpdate, ReorderAlertCreate, StatusUpdate,
    StockAdjustmentCreate, StockReceiptCreate, TransferCreate, WarehouseReceiptCreate,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])

read = require_module("inventory", "read")
write = require_module("inventory", "write")


@router.get("/movements")
def movement_history(productId: UUID | None = None, requestId: UUID | None = None,
                     variantId: UUID | None = None, locationId: UUID | None = None,
                     kind: str | None = None, search: str = Query("", max_length=200),
                     fromAt: datetime | None = None, toAt: datetime | None = None,
                     limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                     principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    from app.modules.inventory import ledger
    return ledger.history(db, tenant_id=principal.tenant_id, product_id=productId, request_id=requestId,
        variant_id=variantId, location_id=locationId, kind=kind, search=search,
        from_at=fromAt, to_at=toAt, limit=limit, offset=offset)


@router.get("/receiving")
def receiving_queue(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                    principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    from app.modules.inventory import receiving
    return receiving.queue(db, tenant_id=principal.tenant_id, limit=limit, offset=offset)


@router.post("/receiving/{public_id}", status_code=status.HTTP_201_CREATED)
def receive_purchase_order(public_id: UUID, payload: WarehouseReceiptCreate,
                           principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    from app.modules.inventory import receiving
    grn = receiving.post(db, tenant_id=principal.tenant_id, public_id=public_id,
        payload=payload, actor=principal.email)
    return {"public_id": str(grn.public_id), "grn_no": grn.grn_no}


@router.get("/stock/screen")
def stock_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.stock_screen(db, limit=limit, offset=offset)


@router.get("/stock/search")
def stock_search(q: str = Query(""), _: Principal = Depends(read),
                 db: Session = Depends(tenant_db)):
    """Type-ahead over in-stock products for the transfer / receipt item pickers."""
    return service.search_stock_products(db, q=q, limit=50)


@router.post("/stock", status_code=status.HTTP_201_CREATED)
def create_stock(payload: StockReceiptCreate,
                 principal: Principal = Depends(write),
                 db: Session = Depends(tenant_db)):
    return service.create_stock_receipt(db, tenant_id=principal.tenant_id, payload=payload,
                                        actor=principal.email)


@router.post("/stock/adjustments", status_code=status.HTTP_201_CREATED)
def adjust_stock(payload: StockAdjustmentCreate,
                 principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    raise HTTPException(409, "Create and submit a stocktake session; count corrections require review before posting")


@router.get("/stock/{public_id}/balances")
def stock_balances(public_id: str, principal: Principal = Depends(read),
                   db: Session = Depends(tenant_db)):
    return service.stock_balances(db, tenant_id=principal.tenant_id, public_id=public_id)


@router.get("/stock/{public_id}/entries")
def stock_entries(public_id: str, principal: Principal = Depends(read),
                  db: Session = Depends(tenant_db)):
    return service.stock_entries(db, tenant_id=principal.tenant_id, public_id=public_id)


@router.get("/stock/{public_id}/detail")
def stock_article_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.stock_article_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    return d


@router.put("/stock/{public_id}/matrix")
def save_stock_matrix(public_id: str, payload: MatrixUpdate,
                      principal: Principal = Depends(write),
                      db: Session = Depends(tenant_db)):
    d = service.save_article_matrix(db, tenant_id=principal.tenant_id,
                                    public_id=public_id, payload=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    return d


@router.get("/locations/screen")
def locations_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.locations_screen(db, limit=limit, offset=offset)


@router.post("/locations", status_code=status.HTTP_201_CREATED)
def create_location(payload: LocationCreate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    loc = service.create_location(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(loc.public_id), "name": loc.name}


@router.put("/locations/{public_id}")
def update_location(public_id: str, payload: LocationUpdate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    loc = service.update_location(db, public_id=public_id, payload=payload)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
    return {"public_id": str(loc.public_id), "name": loc.name}


@router.get("/transfers/screen")
def transfers_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.transfers_screen(db, limit=limit, offset=offset)


@router.get("/locations/options")
def location_options(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Location names for the New transfer From/To selects."""
    return service.location_options(db)


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
def create_transfer(payload: TransferCreate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    t = service.create_transfer(db, tenant_id=principal.tenant_id, payload=payload, actor=principal.email)
    return {"public_id": str(t.public_id), "transfer_no": t.transfer_no}


@router.post("/transfers/{public_id}/status")
def transfer_status(public_id: str, payload: StatusUpdate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    t = service.set_transfer_status(db, public_id=public_id, status=payload.status)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer not found")
    return {"public_id": str(t.public_id), "status": t.status}


@router.get("/alerts/screen")
def alerts_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                  _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.alerts_screen(db, limit=limit, offset=offset)


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
def create_alert(payload: ReorderAlertCreate,
                 principal: Principal = Depends(write),
                 db: Session = Depends(tenant_db)):
    raise HTTPException(409, "Configure a warehouse replenishment rule; alerts are calculated from live stock")

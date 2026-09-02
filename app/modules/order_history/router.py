"""Order History HTTP routes — read-only cross-module drill-down for orders
that have shipped (or are far enough along their own fulfillment status)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.order_history import service

router = APIRouter(prefix="/order-history", tags=["order-history"])

read = require_module("orderhistory", "read")


@router.get("/orders")
def orders_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                  _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.orders_screen(db, limit=limit, offset=offset)


@router.get("/orders/{public_id}/detail")
def order_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.order_history_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return d

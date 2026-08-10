"""Sales HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.sales import service
from app.modules.sales.dto import (
    CustomerCreate, CustomerUpdate, InvoiceCreate, InvoiceSettleIn, OrderCreate,
    ReturnCreate, StatusUpdate,
)

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/customers/screen")
def customers_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.customers_screen(db, limit=limit, offset=offset)


@router.post("/customers", status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    c = service.create_customer(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(c.public_id), "name": c.name}


@router.put("/customers/{public_id}")
def update_customer(public_id: str, payload: CustomerUpdate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    c = service.update_customer(db, public_id=public_id, payload=payload)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return {"public_id": str(c.public_id), "name": c.name}


@router.get("/orders/screen")
def orders_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.orders_screen(db, limit=limit, offset=offset)


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    res = service.create_order(db, tenant_id=principal.tenant_id, payload=payload)
    o = res["order"]
    return {
        "public_id": str(o.public_id), "order_no": o.order_no,
        "total": res["total"], "customer": res["customer"],
        "invoice_public_id": res["invoice_public_id"], "invoice_no": res["invoice_no"],
    }


@router.post("/invoices/{public_id}/settle")
def settle_invoice(public_id: str, payload: InvoiceSettleIn,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    inv = service.record_invoice_payment(
        db, tenant_id=principal.tenant_id, public_id=public_id,
        amount_paid=payload.amountPaid, paid=payload.paid)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return {"public_id": str(inv.public_id), "status": inv.status}


@router.get("/customers/{public_id}/detail")
def customer_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.customer_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return d


@router.get("/orders/{public_id}/detail")
def order_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.order_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return d


@router.get("/invoices/{public_id}/detail")
def invoice_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.invoice_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d


@router.post("/orders/{public_id}/status")
def order_status(public_id: str, payload: StatusUpdate,
                 principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    o = service.set_order_status(db, public_id=public_id, status=payload.status)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return {"public_id": str(o.public_id), "status": o.status}


@router.post("/invoices/{public_id}/status")
def invoice_status(public_id: str, payload: StatusUpdate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    inv = service.set_invoice_status(db, public_id=public_id, status=payload.status)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return {"public_id": str(inv.public_id), "status": inv.status}


@router.post("/returns/{public_id}/status")
def return_status(public_id: str, payload: StatusUpdate,
                  principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    r = service.set_return_status(db, public_id=public_id, status=payload.status)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Return not found")
    return {"public_id": str(r.public_id), "status": r.status}


@router.get("/board/screen")
def board_screen(db: Session = Depends(tenant_db)):
    return service.board_screen(db)


@router.get("/invoices/screen")
def invoices_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.invoices_screen(db, limit=limit, offset=offset)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    inv = service.create_invoice(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(inv.public_id), "invoice_no": inv.invoice_no}


@router.get("/returns/screen")
def returns_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.returns_screen(db, limit=limit, offset=offset)


@router.post("/returns", status_code=status.HTTP_201_CREATED)
def create_return(
    payload: ReturnCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    ret = service.create_return(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(ret.public_id), "rma_no": ret.rma_no}


# ---- Commercial / retail invoices generated from a sales order ----
# Kept under /invoice-docs so they don't collide with the AR /invoices routes.
@router.get("/invoice-docs/draft")
def sales_invoice_draft(
    order: str = Query(...),
    type: str = Query("Retail"),
    db: Session = Depends(tenant_db),
):
    d = service.build_sales_invoice_draft(db, order_no=order, invoice_type=type)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No sales order found for '{order}'.")
    return d


@router.get("/invoice-docs")
def sales_invoices_list(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.list_sales_invoice_docs(db, limit=limit, offset=offset)


@router.post("/invoice-docs", status_code=status.HTTP_201_CREATED)
def sales_invoice_create(
    payload: dict,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    return service.create_sales_invoice_doc(db, tenant_id=principal.tenant_id, data=payload)


@router.get("/invoice-docs/{public_id}")
def sales_invoice_get(public_id: str, db: Session = Depends(tenant_db)):
    d = service.get_sales_invoice_doc(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d


@router.put("/invoice-docs/{public_id}")
def sales_invoice_update(
    public_id: str, payload: dict,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    d = service.update_sales_invoice_doc(db, public_id=public_id, data=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d

"""Procurement HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.procurement import service
from app.modules.procurement.dto import (
    GoodsReceiptCreate, PurchaseOrderCreate, StatusUpdate, SupplierCreate, SupplierUpdate,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])


@router.get("/pos/screen")
def pos_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(tenant_db)):
    return service.pos_screen(db, limit=limit, offset=offset)


@router.post("/pos", status_code=status.HTTP_201_CREATED)
def create_po(payload: PurchaseOrderCreate,
              principal: Principal = Depends(require_tenant),
              db: Session = Depends(tenant_db)):
    po = service.create_po(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(po.public_id), "po_no": po.po_no}


@router.get("/pos/{public_id}/detail")
def po_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.po_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO not found")
    return d


@router.post("/pos/{public_id}/status")
def po_status(public_id: str, payload: StatusUpdate,
              principal: Principal = Depends(require_tenant),
              db: Session = Depends(tenant_db)):
    po = service.set_po_status(db, public_id=public_id, status=payload.status,
                               tenant_id=principal.tenant_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO not found")
    return {"public_id": str(po.public_id), "status": po.status}


@router.post("/receipts/{public_id}/status")
def receipt_status(public_id: str, payload: StatusUpdate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    grn = service.set_receipt_status(db, public_id=public_id, status=payload.status)
    if grn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return {"public_id": str(grn.public_id), "status": grn.status}


@router.get("/approvals/screen")
def approvals_screen(db: Session = Depends(tenant_db)):
    return service.approvals_board(db)


@router.get("/receipts/screen")
def receipts_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                    db: Session = Depends(tenant_db)):
    return service.receipts_screen(db, limit=limit, offset=offset)


@router.post("/receipts", status_code=status.HTTP_201_CREATED)
def create_receipt(payload: GoodsReceiptCreate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    grn = service.create_receipt(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(grn.public_id), "grn_no": grn.grn_no}


@router.get("/suppliers/screen")
def suppliers_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     db: Session = Depends(tenant_db)):
    return service.suppliers_screen(db, limit=limit, offset=offset)


@router.get("/suppliers/search")
def suppliers_search(q: str = Query(""), db: Session = Depends(tenant_db)):
    """Type-ahead over supplier names for the New PO form."""
    return service.search_suppliers(db, q=q, limit=10)


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
def create_supplier(payload: SupplierCreate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    s = service.create_supplier(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(s.public_id), "name": s.name}


@router.put("/suppliers/{public_id}")
def update_supplier(public_id: str, payload: SupplierUpdate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    s = service.update_supplier(db, public_id=public_id, payload=payload)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    return {"public_id": str(s.public_id), "name": s.name}


@router.get("/scorecard/screen")
def scorecard_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     db: Session = Depends(tenant_db)):
    return service.scorecard_screen(db, limit=limit, offset=offset)


@router.get("/suppliers/{public_id}/detail")
def supplier_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.supplier_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    return d


@router.get("/receipts/{public_id}/detail")
def receipt_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.receipt_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return d


# ---- Commercial invoices generated from a PO ----
@router.get("/invoices/draft")
def invoice_draft(po: str = Query(...), db: Session = Depends(tenant_db)):
    d = service.build_invoice_draft(db, po_no=po)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No purchase order found for '{po}'.")
    return d


@router.get("/invoices")
def invoices_list(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                  db: Session = Depends(tenant_db)):
    return service.list_invoices(db, limit=limit, offset=offset)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def invoice_create(payload: dict, principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    return service.create_invoice(db, tenant_id=principal.tenant_id, data=payload)


@router.get("/invoices/{public_id}")
def invoice_get(public_id: str, db: Session = Depends(tenant_db)):
    d = service.get_invoice(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d


@router.put("/invoices/{public_id}")
def invoice_update(public_id: str, payload: dict, principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    d = service.update_invoice(db, public_id=public_id, data=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d

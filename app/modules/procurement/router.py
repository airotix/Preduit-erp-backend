"""Procurement HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.procurement import service
from app.modules.procurement.dto import (
    GoodsReceiptCreate, PurchaseOrderCreate, StatusUpdate, SupplierCreate, SupplierUpdate, POExpectedUpdate, ResolveImpact, GroupedPurchaseOrderCreate,
)

router = APIRouter(prefix="/procurement", tags=["procurement"])

read = require_module("procurement", "read")
write = require_module("procurement", "write")

# Stock movements and money resolution remain separate permissioned actions.
from app.modules.procurement import returns as supplier_returns
from app.modules.inventory import workflows as return_workflows
from uuid import UUID
from app.modules.procurement import performance


@router.get("/supplier-performance")
def supplier_performance(supplier: UUID | None = Query(None),
                         p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return performance.dashboard(db, UUID(str(p.tenant_id)), supplier)


@router.post("/supplier-followups", status_code=201)
def create_supplier_followup(payload: performance.FollowupIn,
                             p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, performance.create_followup,
        UUID(str(p.tenant_id)), payload, return_workflows.actor(p))


@router.put("/supplier-followups/{public_id}")
def update_supplier_followup(public_id: UUID, payload: performance.FollowupUpdate,
                             p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, performance.update_followup,
        UUID(str(p.tenant_id)), public_id, payload, return_workflows.actor(p))


@router.get("/supplier-returns/options")
def supplier_return_options(p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return supplier_returns.options(db, UUID(str(p.tenant_id)))


@router.get("/supplier-returns")
def supplier_return_list(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                         p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return supplier_returns.listing(db, UUID(str(p.tenant_id)), limit, offset)


@router.post("/supplier-returns", status_code=201)
def supplier_return_create(payload: supplier_returns.ReturnIn, p: Principal = Depends(write),
                           _: Principal = Depends(require_module("inventory", "write")),
                           db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, supplier_returns.create, UUID(str(p.tenant_id)), payload, return_workflows.actor(p))


@router.post("/supplier-returns/{public_id}/credit")
def supplier_return_credit(public_id: UUID, payload: supplier_returns.CreditIn,
                           p: Principal = Depends(require_module("finance", "write")),
                           _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, supplier_returns.action, UUID(str(p.tenant_id)), public_id,
        payload, return_workflows.actor(p), "Supplier credit")


@router.post("/supplier-returns/{public_id}/refund")
def supplier_return_refund(public_id: UUID, payload: supplier_returns.RefundIn,
                           p: Principal = Depends(require_module("finance", "write")),
                           _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, supplier_returns.action, UUID(str(p.tenant_id)), public_id,
        payload, return_workflows.actor(p), "Supplier refund")


@router.post("/supplier-returns/{public_id}/replacement")
def supplier_return_replacement(public_id: UUID, payload: supplier_returns.ReceiveIn,
                                p: Principal = Depends(write),
                                _: Principal = Depends(require_module("inventory", "write")),
                                db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, supplier_returns.action, UUID(str(p.tenant_id)), public_id,
        payload, return_workflows.actor(p), "Receive supplier replacement")


@router.post("/supplier-returns/{public_id}/resolution")
def supplier_return_resolution(public_id: UUID, payload: supplier_returns.ResolutionIn,
                               p: Principal = Depends(require_module("finance", "write")),
                               _: Principal = Depends(read),
                               db: Session = Depends(tenant_db)):
    return return_workflows.atomic(db, supplier_returns.action, UUID(str(p.tenant_id)), public_id,
        payload, return_workflows.actor(p), "Change supplier resolution")

@router.get("/sourcing")
def sourcing(articles: list[str] = Query(default=[]), p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    from uuid import UUID
    from app.modules.catalog import supplier_pricing
    return supplier_pricing.sourcing(db, UUID(str(p.tenant_id)), articles)


@router.get("/pos/screen")
def pos_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
               _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.pos_screen(db, limit=limit, offset=offset)


@router.post("/pos", status_code=status.HTTP_201_CREATED)
def create_po(payload: PurchaseOrderCreate,
              principal: Principal = Depends(write),
              db: Session = Depends(tenant_db)):
    po = service.create_po(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(po.public_id), "po_no": po.po_no}


@router.post("/pos/grouped", status_code=status.HTTP_201_CREATED)
def create_supplier_pos(payload: GroupedPurchaseOrderCreate,
                        principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    from app.modules.procurement import grouped_orders
    return return_workflows.atomic(db, grouped_orders.create, UUID(str(principal.tenant_id)),
        payload, return_workflows.actor(principal))


@router.get("/pos/{public_id}/detail")
def po_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.po_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO not found")
    return d


@router.post("/pos/{public_id}/status")
def po_status(public_id: str, payload: StatusUpdate,
              principal: Principal = Depends(write),
              db: Session = Depends(tenant_db)):
    po = service.set_po_status(db, public_id=public_id, status=payload.status, reason=payload.reason,
                               tenant_id=principal.tenant_id, review_token=payload.reviewToken,
                               acknowledged=payload.acknowledged, actor_id=principal.user_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO not found")
    return {"public_id": str(po.public_id), "status": po.status}


@router.get("/pos/{public_id}/impact")
def po_impact(public_id: str, status: str | None = Query(None, max_length=24),
              expected: str | None = Query(None, max_length=40),
              principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    from app.modules.procurement import changes
    po = changes.get_po(db, public_id, principal.tenant_id)
    return changes.review(db, po, status=status, expected=expected, permissions=principal.permissions)


@router.post("/pos/{public_id}/impact/resolve")
def resolve_po_impact(public_id: str, payload: ResolveImpact,
                      principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    from app.modules.procurement import changes
    po = changes.get_po(db, public_id, principal.tenant_id, lock=True)
    changes.resolve_issue(db, po, key=payload.key, reason=payload.reason,
                          actor=principal.email, permissions=principal.permissions)
    return changes.review(db, po, permissions=principal.permissions)


@router.put("/pos/{public_id}/expected")
def po_expected(public_id: str, payload: POExpectedUpdate,
                principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    from app.modules.procurement import changes
    po = changes.get_po(db, public_id, principal.tenant_id, lock=True)
    changes.confirm(db, po, expected=payload.expected, reason=payload.reason,
        review_token=payload.reviewToken, acknowledged=payload.acknowledged, actor_id=principal.user_id)
    return {"public_id": str(po.public_id), "expected": po.expected}


@router.post("/receipts/{public_id}/status")
def receipt_status(public_id: str, payload: StatusUpdate,
                   principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    grn = service.set_receipt_status(db, public_id=public_id, status=payload.status)
    if grn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return {"public_id": str(grn.public_id), "status": grn.status}


@router.get("/approvals/screen")
def approvals_screen(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.approvals_board(db)


@router.get("/receipts/screen")
def receipts_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                    _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.receipts_screen(db, limit=limit, offset=offset)


@router.post("/receipts", status_code=status.HTTP_201_CREATED)
def create_receipt(payload: GoodsReceiptCreate,
                   principal: Principal = Depends(write),
                   _: Principal = Depends(require_module("inventory", "write")),
                   db: Session = Depends(tenant_db)):
    grn = service.create_receipt(db, tenant_id=principal.tenant_id, payload=payload, actor=principal.email)
    return {"public_id": str(grn.public_id), "grn_no": grn.grn_no}


@router.get("/suppliers/screen")
def suppliers_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.suppliers_screen(db, limit=limit, offset=offset)


@router.get("/suppliers/search")
def suppliers_search(q: str = Query(""), _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Type-ahead over supplier names for the New PO form."""
    return service.search_suppliers(db, q=q, limit=10)


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
def create_supplier(payload: SupplierCreate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    s = service.create_supplier(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(s.public_id), "name": s.name}


@router.put("/suppliers/{public_id}")
def update_supplier(public_id: str, payload: SupplierUpdate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    s = service.update_supplier(db, public_id=public_id, payload=payload)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    return {"public_id": str(s.public_id), "name": s.name}


@router.get("/scorecard/screen")
def scorecard_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                     _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.scorecard_screen(db, limit=limit, offset=offset)


@router.get("/suppliers/{public_id}/detail")
def supplier_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.supplier_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    return d


@router.get("/receipts/{public_id}/detail")
def receipt_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.receipt_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return d


# ---- Commercial invoices generated from a PO ----
@router.get("/invoices/draft")
def invoice_draft(po: str = Query(...), principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.build_invoice_draft(db, po_no=po, actor_id=principal.user_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No purchase order found for '{po}'.")
    return d


@router.get("/invoices")
def invoices_list(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                  _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return service.list_invoices(db, limit=limit, offset=offset)


@router.post("/invoices/generate-missing")
def generate_missing_invoices(principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return service.generate_missing_invoices(db, tenant_id=principal.tenant_id)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def invoice_create(payload: dict, principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    return service.create_invoice(db, tenant_id=principal.tenant_id, data=payload, actor_id=principal.user_id)


@router.get("/invoices/{public_id}")
def invoice_get(public_id: str, principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    d = service.get_invoice(db, public_id=public_id, actor_id=principal.user_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d


@router.put("/invoices/{public_id}")
def invoice_update(public_id: str, payload: dict, principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    d = service.update_invoice(db, public_id=public_id, data=payload)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return d

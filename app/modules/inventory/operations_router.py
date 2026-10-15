from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.deps import tenant_db
from app.core.security import Principal, require_module, has_permission
from app.modules.inventory import replenishment as rules, workflows as flow
from app.modules.inventory.operations_dto import Action, CountCreate, RuleInput, ReturnCreate

router = APIRouter(tags=["warehouse workflows"])
read = require_module("inventory", "read")
write = require_module("inventory", "write")
sales_read = require_module("sales", "read")
sales_write = require_module("sales", "write")
quality_read = require_module("quality", "read")
quality_write = require_module("quality", "write")

def tid(p):
    return UUID(str(p.tenant_id))

@router.get("/inventory/operations/options")
def options(p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return rules.options(db, tid(p))

@router.get("/inventory/replenishment")
def replenishment(p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return rules.plan(db, tid(p))

@router.post("/inventory/replenishment/rules")
def rule(payload: RuleInput, p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return flow.atomic(db, rules.save_rule, tid(p), payload)

@router.post("/inventory/replenishment/{pid}/purchase")
def purchase(pid: UUID, payload: Action, p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    if not has_permission(p.permissions, "procurement.write"):
        raise HTTPException(403, "Procurement write access is required to create a PO")
    return flow.atomic(db, rules.create_po, tid(p), pid, payload)

@router.post("/inventory/replenishment/incoming/{pid}/assign")
def assign(pid: UUID, payload: Action, p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    if not has_permission(p.permissions, "procurement.write"):
        raise HTTPException(403, "Procurement write access is required to assign a PO")
    return flow.atomic(db, rules.assign, tid(p), pid, payload)

@router.get("/inventory/stocktakes")
def counts(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
           p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return flow.counts(db, tid(p), limit, offset)

@router.post("/inventory/stocktakes")
def create_count(payload: CountCreate, p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return flow.atomic(db, flow.new_count, tid(p), payload, p)

@router.get("/inventory/stocktakes/{pid}")
def count(pid: UUID, p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return flow.count_detail(db, tid(p), pid)

@router.post("/inventory/stocktakes/{pid}/{action}")
def count_action(pid: UUID, action: str, payload: Action, p: Principal = Depends(write), db: Session = Depends(tenant_db)):
    return flow.atomic(db, flow.count_action, tid(p), pid, action, payload, p)

@router.get("/sales/return-workflow/options")
def return_options(search: str = Query("", max_length=200), p: Principal = Depends(sales_read), db: Session = Depends(tenant_db)):
    return flow.return_options(db, tid(p), search=search)

@router.get("/sales/return-workflow")
def sales_returns(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                  p: Principal = Depends(sales_read), db: Session = Depends(tenant_db)):
    return flow.returns(db, tid(p), limit, offset)

@router.get("/inventory/returns")
def returns(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
            p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return flow.returns(db, tid(p), limit, offset)

@router.post("/sales/return-workflow")
def new_return(payload: ReturnCreate, p: Principal = Depends(sales_write), db: Session = Depends(tenant_db)):
    return flow.atomic(db, flow.new_return, tid(p), payload, p)

@router.get("/sales/return-workflow/{pid}")
def sales_return(pid: UUID, p: Principal = Depends(sales_read), db: Session = Depends(tenant_db)):
    return flow.return_detail(db, tid(p), pid)

@router.get("/inventory/returns/{pid}")
def inventory_return(pid: UUID, p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    return flow.return_detail(db, tid(p), pid)

@router.get("/quality/returns")
def quality_returns(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                    p: Principal = Depends(quality_read), db: Session = Depends(tenant_db)):
    return flow.returns(db, tid(p), limit, offset)

@router.get("/quality/returns/{pid}")
def quality_return(pid: UUID, p: Principal = Depends(quality_read), db: Session = Depends(tenant_db)):
    return flow.return_detail(db, tid(p), pid)

@router.post("/quality/returns/{pid}/{action}")
def quality_return_action(pid: UUID, action: str, payload: Action, p: Principal = Depends(quality_write), db: Session = Depends(tenant_db)):
    if action not in ("assess", "repair"):
        raise HTTPException(403, "Only return QC and repair inspection belong in Quality")
    return flow.atomic(db, flow.return_action, tid(p), pid, action, payload, p)

@router.post("/inventory/returns/{pid}/{action}")
def return_action(pid: UUID, action: str, payload: Action, p: Principal = Depends(read), db: Session = Depends(tenant_db)):
    permission = {"receive": "inventory.write", "restock": "inventory.write",
        "cancel": "sales.write", "close": "sales.write"}.get(action)
    if permission is None:
        raise HTTPException(404, "Unknown return action")
    if not has_permission(p.permissions, permission):
        raise HTTPException(403, f"{permission} access is required for this action")
    return flow.atomic(db, flow.return_action, tid(p), pid, action, payload, p)

@router.post("/sales/return-workflow/{pid}/{action}")
def close_return(pid: UUID, action: str, payload: Action, p: Principal = Depends(sales_write), db: Session = Depends(tenant_db)):
    if action not in ("cancel", "close"):
        raise HTTPException(403, "Receive and process goods in Inventory")
    return flow.atomic(db, flow.return_action, tid(p), pid, action, payload, p)

"""Admin HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.admin import service
from app.modules.admin.dto import (
    ApprovalRuleCreate, ApprovalRuleUpdate, RoleCreate, RoleUpdate,
    UserCreate, UserUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------- Users ----------

@router.get("/users/screen")
def users_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(tenant_db)):
    return service.users_screen(db, limit=limit, offset=offset)


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    u = service.create_user(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(u.public_id), "name": u.display_name}


@router.put("/users/{public_id}")
def update_user(public_id: str, payload: UserUpdate,
                principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    u = service.update_user(db, public_id=public_id, payload=payload)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return {"public_id": str(u.public_id), "name": u.display_name}


# ---------- Roles ----------

@router.get("/roles/screen")
def roles_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(tenant_db)):
    return service.roles_screen(db, limit=limit, offset=offset)


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate, principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    r = service.create_role(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(r.public_id), "name": r.name}


@router.put("/roles/{public_id}")
def update_role(public_id: str, payload: RoleUpdate,
                principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    r = service.update_role(db, public_id=public_id, payload=payload)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return {"public_id": str(r.public_id), "name": r.name}


# ---------- Approval rules ----------

@router.get("/approvalrules/screen")
def rules_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(tenant_db)):
    return service.rules_screen(db, limit=limit, offset=offset)


@router.post("/approvalrules", status_code=status.HTTP_201_CREATED)
def create_rule(payload: ApprovalRuleCreate, principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    r = service.create_rule(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(r.public_id), "name": r.name}


@router.put("/approvalrules/{public_id}")
def update_rule(public_id: str, payload: ApprovalRuleUpdate,
                principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    r = service.update_rule(db, public_id=public_id, payload=payload)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval rule not found")
    return {"public_id": str(r.public_id), "name": r.name}


# ---------- Read-only screens ----------

@router.get("/doclibrary/screen")
def doclibrary_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                      db: Session = Depends(tenant_db)):
    return service.doclibrary_screen(db, limit=limit, offset=offset)


@router.get("/audit/screen")
def audit_screen(limit: int = Query(100, le=500), db: Session = Depends(tenant_db)):
    return service.audit_screen(db, limit=limit)

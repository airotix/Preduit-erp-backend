"""Self-serve onboarding routes (plan §6).

Available to any authenticated principal (they may not have a tenant yet).
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import Principal, get_principal
from app.modules.onboarding import service
from app.modules.onboarding.dto import CreateOrgRequest, CreateOrgResponse

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/organization", response_model=CreateOrgResponse, status_code=status.HTTP_201_CREATED)
def create_organization(req: CreateOrgRequest, principal: Principal = Depends(get_principal)):
    if principal.tenant_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Principal already belongs to an organization")
    try:
        return service.create_organization(principal, req)
    except ValueError as exc:
        if str(exc) == "slug_taken":
            raise HTTPException(status.HTTP_409_CONFLICT, "That workspace URL is already taken") from exc
        raise

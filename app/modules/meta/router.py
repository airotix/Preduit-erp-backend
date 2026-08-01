"""Lightweight 'current context' endpoint (business name for the shell, etc.)."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import tenant_db

router = APIRouter(tags=["meta"])


@router.get("/me")
def me(db: Session = Depends(tenant_db)):
    """The signed-in user's organization (from onboarding) for the app shell."""
    row = db.execute(text(
        "SELECT name, slug, base_currency_code FROM dbo.tenants "
        "WHERE id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)"
    )).mappings().first()
    if not row:
        return {"businessName": None, "slug": None, "baseCurrency": None}
    return {"businessName": row["name"], "slug": row["slug"], "baseCurrency": row["base_currency_code"]}

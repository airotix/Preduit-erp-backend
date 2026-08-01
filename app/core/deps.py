"""Composed FastAPI dependencies: authenticated principal + RLS-scoped session."""
from typing import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import Principal, require_tenant


def tenant_db(principal: Principal = Depends(require_tenant)) -> Iterator[Session]:
    """RLS-scoped DB session for the caller's tenant."""
    yield from get_db(principal.tenant_id)

"""Dashboard KPI overrides endpoint (read-only, tenant-scoped)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.modules.dashboards import service

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


@router.get("/{key}")
def dashboard_overrides(key: str, db: Session = Depends(tenant_db)):
    """Real KPI values keyed by label; {} when the dashboard has no live source."""
    return service.overrides(db, key)

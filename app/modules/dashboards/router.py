"""Dashboard KPI overrides endpoint (read-only, tenant-scoped)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.dashboards import service

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

# Every role that exists today already carries at least dashboard.read, so this
# is mostly a consistency check (matches the Roles matrix) rather than an
# active gate. Note: `key` can address a specific module's dashboard (e.g.
# "proddash"), which this doesn't cross-check against that module's own
# permission — a known, low-stakes gap since it only ever returns KPI numbers.
read = require_module("dashboard", "read")


@router.get("/{key}")
def dashboard_overrides(key: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Real KPI values keyed by label; {} when the dashboard has no live source."""
    return service.overrides(db, key)

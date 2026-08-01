"""AI Insights HTTP routes — the ERP-owned facade over the forecasting engine.

The browser never calls the engine directly; it calls these endpoints, which
serve from the tenant's ``ai_snapshot`` store (populated by conversing with the
engine) and forward writes back to the engine.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.ai import service
from app.modules.ai.dto import RecommendationOverride, RevertPayload, SeasonConfig

router = APIRouter(prefix="/ai", tags=["ai"])


# ---- Setup: season config (ERP-owned) + pipeline triggers (proxied) ----
@router.get("/season-config")
def get_season_config(principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    return service.get_season_config(db)


@router.post("/season-config")
def save_season_config(payload: SeasonConfig, principal: Principal = Depends(require_tenant),
                       db: Session = Depends(tenant_db)):
    return service.save_season_config(db, principal.tenant_id, payload.model_dump())


@router.get("/ingest/status")
def ingest_status(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.ingest_status(db, principal.tenant_id)


@router.post("/ingest/sync")
def ingest_sync(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.run_ingest(db, principal.tenant_id)


@router.get("/forecast/job")
def forecast_job(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.forecast_job(db, principal.tenant_id)


@router.post("/forecast/refresh")
def forecast_refresh(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.refresh_forecast(db, principal.tenant_id)


@router.get("/forecast/backtest-status")
def backtest_status(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.backtest_status(db, principal.tenant_id)


@router.post("/forecast/backtest")
def run_backtest(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.run_backtest(db, principal.tenant_id)


# ---- Reads ----
@router.get("/dashboard")
def dashboard(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.dashboard(db, principal.tenant_id)


@router.get("/products")
def products(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.products(db, principal.tenant_id)


@router.get("/projections")
def projections(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.projections(db, principal.tenant_id)


@router.get("/projections/{reference}/detail")
def projection_detail(reference: str, principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    return service.projection_detail(db, principal.tenant_id, reference)


@router.get("/budget")
def budget(scenario: str = Query("base"), principal: Principal = Depends(require_tenant),
           db: Session = Depends(tenant_db)):
    return service.budget(db, principal.tenant_id, scenario)


@router.get("/recommendations")
def recommendations(scenario: str = Query("base"), principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    return service.recommendations(db, principal.tenant_id, scenario)


@router.get("/validation")
def validation(scenario: str = Query("base"), principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    return service.validation(db, principal.tenant_id, scenario)


@router.get("/customers")
def customers(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.customers(db, principal.tenant_id)


@router.get("/customer-detail")
def customer_detail(customer: str = Query(...), principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    return service.customer_detail(db, principal.tenant_id, customer)


@router.get("/audit")
def audit(limit: int = Query(30, le=200), principal: Principal = Depends(require_tenant),
          db: Session = Depends(tenant_db)):
    return service.audit(db, principal.tenant_id, limit)


@router.get("/forecast/accuracy")
def forecast_accuracy(principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    return service.accuracy(db, principal.tenant_id)


# ---- Writes (forwarded to the engine) ----
@router.put("/recommendations/{reference}/{couleur}")
def update_recommendation(reference: str, couleur: str, payload: RecommendationOverride,
                          principal: Principal = Depends(require_tenant),
                          db: Session = Depends(tenant_db)):
    return service.update_recommendation(db, reference, couleur, payload.model_dump())


@router.post("/recommendations/{reference}/{couleur}/lock")
def lock_recommendation(reference: str, couleur: str,
                        principal: Principal = Depends(require_tenant),
                        db: Session = Depends(tenant_db)):
    return service.set_lock(db, reference, couleur, locked=True)


@router.post("/recommendations/{reference}/{couleur}/unlock")
def unlock_recommendation(reference: str, couleur: str,
                          principal: Principal = Depends(require_tenant),
                          db: Session = Depends(tenant_db)):
    return service.set_lock(db, reference, couleur, locked=False)


@router.post("/overrides/revert")
def revert_override(payload: RevertPayload, principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    return service.revert_override(db, payload.reference, payload.couleur, payload.taille)


@router.post("/overrides/revert-all")
def revert_all(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.revert_all_overrides(db)


# ---- Sync ----
@router.post("/sync")
def sync(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.sync_all(db, principal.tenant_id)


@router.get("/sync-state")
def get_sync_state(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return service.sync_state(db)

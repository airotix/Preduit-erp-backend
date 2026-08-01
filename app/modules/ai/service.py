"""AI Insights service.

The ERP backend is the only thing that talks to the external Forcaster
forecasting engine. It fetches the engine's responses over HTTP, materialises
them into ``ai_snapshot`` rows (per tenant, via RLS) and serves the AI Insights
screens from that store. Writes (overrides, locks, reverts) are forwarded to the
engine and the affected snapshots are invalidated so the next read re-syncs.
"""
import json
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.ai import repository as repo

settings = get_settings()

SCENARIOS = ("conservative", "base", "optimistic")

# Empty-but-valid payloads so the AI screens render (as empty states) while the
# engine is offline / not yet configured — instead of erroring or crashing.
_EMPTY = {
    "dashboard": {
        "kpis": [], "financialSummary": {
            "budgetUsed": 0, "budgetTotal": 0, "margin": 0, "targetMargin": 0,
            "coverage": 0, "totalPurchaseCost": 0, "expectedTurnover": 0,
        },
        "confidenceDistribution": {}, "lastPipelineRun": None, "totalSKUs": 0,
    },
    "products": [],
    "projections": [],
    "projection_detail": {
        "skuId": "", "skuName": "", "category": "", "productInfo": {},
        "stockSummary": {"forecastedQuantity": 0, "supplierOrder": 0, "stock": 0, "total": 0},
        "colorVariants": [], "sizeColumns": [], "confidence": "medium",
    },
    "budget": {"summary": {
        "totalPurchaseCost": 0, "seasonBudget": 0, "expectedTurnover": 0,
        "grossMargin": 0, "grossMarginPercent": 0,
    }, "categories": []},
    "recommendations": [],
    "validation": {"scenarioMetrics": {}, "overrides": []},
    "customers": {"customers": [], "regions": [], "latestSeason": None},
    "customer_detail": {"customer": "", "purchaseHistory": [], "seasons": []},
    "audit": [],
    "accuracy": None,
}


def _empty(kind: str):
    return _EMPTY.get(kind)


# --------------------------------------------------------------------------- #
# Engine HTTP client
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    h = {"Accept": "application/json"}
    if settings.ai_engine_token:
        h["Authorization"] = f"Bearer {settings.ai_engine_token}"
    return h


ALL_COLLECTION_KINDS = ["dashboard", "products", "projections", "recommendations",
                        "validation", "customers", "audit", "accuracy", "budget",
                        "projection_detail", "customer_detail"]


def _engine_get(path: str, params: dict | None = None):
    url = f"{settings.ai_engine_url}{path}"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, params={k: v for k, v in (params or {}).items() if v not in (None, "")},
                           headers=_headers())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Forecasting engine unavailable: {exc}") from exc


def _engine_send(method: str, path: str, body: dict | None = None):
    if not settings.ai_engine_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "AI forecasting engine is offline — writes are disabled.")
    url = f"{settings.ai_engine_url}{path}"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.request(method, url, json=body, headers=_headers())
            r.raise_for_status()
            return r.json() if r.content else {"status": "ok"}
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Forecasting engine write failed: {exc}") from exc


# --------------------------------------------------------------------------- #
# Cache helpers
# --------------------------------------------------------------------------- #
def _cached(session: Session, tenant_id: UUID, kind: str, scope: str, path: str,
            params: dict | None = None, *, force: bool = False):
    """Return the stored payload for (kind, scope); fetch + cache from the engine on
    a miss. Degrades to an empty payload (never raises) when the engine is disabled
    or unreachable and nothing is cached yet — so the AI tabs keep working."""
    if not force:
        row = repo.get_snapshot(session, kind=kind, scope=scope)
        if row is not None:
            return json.loads(row.data)
    if not settings.ai_engine_enabled:
        return _empty(kind)
    try:
        payload = _engine_get(path, params)
    except HTTPException:
        return _empty(kind)
    repo.upsert_snapshot(session, tenant_id=tenant_id, kind=kind, scope=scope,
                         data=json.dumps(payload))
    return payload


# --------------------------------------------------------------------------- #
# Reads (used by the router)
# --------------------------------------------------------------------------- #
def dashboard(session, tenant_id):
    return _cached(session, tenant_id, "dashboard", "", "/dashboard")


def products(session, tenant_id):
    return _cached(session, tenant_id, "products", "", "/products")


def projections(session, tenant_id):
    return _cached(session, tenant_id, "projections", "", "/projections")


def projection_detail(session, tenant_id, reference):
    return _cached(session, tenant_id, "projection_detail", reference,
                   f"/projections/{reference}/detail")


def budget(session, tenant_id, scenario):
    return _cached(session, tenant_id, "budget", scenario, "/budget", {"scenario": scenario})


def recommendations(session, tenant_id, scenario):
    return _cached(session, tenant_id, "recommendations", scenario,
                   "/recommendations", {"scenario": scenario})


def validation(session, tenant_id, scenario):
    return _cached(session, tenant_id, "validation", scenario, "/validation", {"scenario": scenario})


def customers(session, tenant_id):
    return _cached(session, tenant_id, "customers", "", "/customers")


def customer_detail(session, tenant_id, customer):
    return _cached(session, tenant_id, "customer_detail", customer,
                   "/customer-detail", {"customer": customer})


def audit(session, tenant_id, limit=30):
    return _cached(session, tenant_id, "audit", "", "/audit", {"limit": limit})


def accuracy(session, tenant_id):
    return _cached(session, tenant_id, "accuracy", "", "/forecast/accuracy")


# --------------------------------------------------------------------------- #
# Writes (forward to engine, then invalidate affected snapshots)
# --------------------------------------------------------------------------- #
def _invalidate(session, kinds):
    repo.delete_snapshots(session, kinds=kinds)


def update_recommendation(session, reference, couleur, payload: dict):
    res = _engine_send("PUT", f"/recommendations/{reference}/{couleur}", payload)
    _invalidate(session, ["recommendations", "validation", "projections", "audit"])
    return res


def set_lock(session, reference, couleur, locked: bool):
    verb = "lock" if locked else "unlock"
    res = _engine_send("POST", f"/recommendations/{reference}/{couleur}/{verb}")
    _invalidate(session, ["recommendations", "validation", "audit"])
    return res


def revert_override(session, reference, couleur, taille):
    res = _engine_send("POST", "/overrides/revert",
                       {"reference": reference, "couleur": couleur, "taille": taille})
    _invalidate(session, ["recommendations", "validation", "audit"])
    return res


def revert_all_overrides(session):
    res = _engine_send("POST", "/overrides/revert-all")
    _invalidate(session, ["recommendations", "validation", "audit"])
    return res


# --------------------------------------------------------------------------- #
# Full sync + status
# --------------------------------------------------------------------------- #
def sync_all(session, tenant_id: UUID) -> dict:
    """Refresh every collection snapshot from the engine. Detail/customer-detail
    stay lazy (fetched on drill-down)."""
    if not settings.ai_engine_enabled:
        repo.upsert_sync_state(session, tenant_id=tenant_id, status="offline",
                               message="AI engine is disabled (AI_ENGINE_ENABLED=false).",
                               synced=False)
        return {"status": "offline", "snapshots": 0}
    fetched = 0
    try:
        for kind, path in (("dashboard", "/dashboard"), ("products", "/products"),
                           ("projections", "/projections"), ("customers", "/customers"),
                           ("accuracy", "/forecast/accuracy")):
            repo.upsert_snapshot(session, tenant_id=tenant_id, kind=kind, scope="",
                                 data=json.dumps(_engine_get(path)))
            fetched += 1
        repo.upsert_snapshot(session, tenant_id=tenant_id, kind="audit", scope="",
                             data=json.dumps(_engine_get("/audit", {"limit": 30})))
        fetched += 1
        for scenario in SCENARIOS:
            for kind, path in (("budget", "/budget"), ("recommendations", "/recommendations"),
                               ("validation", "/validation")):
                repo.upsert_snapshot(session, tenant_id=tenant_id, kind=kind, scope=scenario,
                                     data=json.dumps(_engine_get(path, {"scenario": scenario})))
                fetched += 1
        repo.upsert_sync_state(session, tenant_id=tenant_id, status="ok", message=None, synced=True)
        return {"status": "ok", "snapshots": fetched}
    except HTTPException as exc:
        repo.upsert_sync_state(session, tenant_id=tenant_id, status="error",
                               message=str(exc.detail), synced=False)
        raise


# --------------------------------------------------------------------------- #
# Setup tab: season config (ERP-owned) + pipeline triggers (proxied to engine)
# --------------------------------------------------------------------------- #
def _safe_get(path: str, default, params: dict | None = None):
    """Engine GET that degrades to a default instead of raising (status probes)."""
    if not settings.ai_engine_enabled:
        return default
    try:
        return _engine_get(path, params)
    except HTTPException:
        return default


def get_season_config(session) -> dict:
    row = repo.get_snapshot(session, kind="season_config", scope="")
    return json.loads(row.data) if row else {}


def save_season_config(session, tenant_id: UUID, payload: dict) -> dict:
    """Season config is ERP-owned — persisted locally so Setup works even when the
    engine is offline. When the engine is live it's also pushed to /season-config."""
    repo.upsert_snapshot(session, tenant_id=tenant_id, kind="season_config", scope="",
                         data=json.dumps(payload))
    if settings.ai_engine_enabled:
        try:
            with httpx.Client(timeout=30.0) as client:
                client.post(f"{settings.ai_engine_url}/season-config", json=payload,
                            headers=_headers())
        except httpx.HTTPError:
            pass  # local save already succeeded; engine push is best-effort
    return payload


def ingest_status(session, tenant_id) -> dict:
    return _safe_get("/ingest/erp/status", {
        "enabled": False, "configured": False, "hasHistoryQuery": False,
        "baseUrl": None, "enseigne": None, "mode": "sample", "provider": "engine",
    })


def run_ingest(session, tenant_id) -> dict:
    res = _engine_send("POST", "/ingest/erp", {})
    _invalidate(session, ALL_COLLECTION_KINDS)
    return res


def forecast_job(session, tenant_id) -> dict:
    return _safe_get("/forecast/job", {
        "jobId": None, "status": "idle", "lastRun": None,
        "errorMessage": None, "startedAt": None, "finishedAt": None,
    })


def refresh_forecast(session, tenant_id) -> dict:
    res = _engine_send("POST", "/forecast/refresh", {})
    _invalidate(session, ALL_COLLECTION_KINDS)
    return res


def backtest_status(session, tenant_id) -> dict:
    return _safe_get("/forecast/backtest/status", {"runId": None, "status": "idle"})


def run_backtest(session, tenant_id) -> dict:
    return _engine_send("POST", "/forecast/backtest", {})


def sync_state(session) -> dict:
    row = repo.get_sync_state(session)
    if row is None:
        return {"status": "idle", "lastSyncedAt": None, "message": None}
    return {
        "status": row.status,
        "lastSyncedAt": row.last_synced_at.isoformat() + "Z" if row.last_synced_at else None,
        "message": row.message,
    }

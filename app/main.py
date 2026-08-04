"""Preduit ERP backend — FastAPI application entrypoint."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.modules.admin.router import router as admin_router
from app.modules.ai.router import router as ai_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.dashboards.router import router as dashboards_router
from app.modules.documents.router import router as documents_router
from app.modules.finance.router import router as finance_router
from app.modules.inventory.router import router as inventory_router
from app.modules.meta.router import router as meta_router
from app.modules.onboarding.router import router as onboarding_router
from app.modules.procurement.router import router as procurement_router
from app.modules.production.router import router as production_router
from app.modules.quality.router import router as quality_router
from app.modules.sales.router import router as sales_router
from app.modules.shipments.router import router as shipments_router

settings = get_settings()

app = FastAPI(
    title="Preduit ERP API",
    version="0.1.0",
    description="Multi-tenant apparel ERP backend (Phase 0 foundation).",
    docs_url="/docs",
    openapi_url="/api/v1/openapi.json",
)

# Fail fast if the app-issued JWT secret was never overridden outside dev — a
# default secret means anyone can forge tokens.
if settings.env != "dev" and settings.jwt_secret_is_default:
    raise RuntimeError(
        "JWT_SECRET is still the built-in dev default. Set a strong JWT_SECRET "
        "(env / Key Vault) before running outside dev."
    )
# The dev auth bypass authenticates every request as a fixed admin — it must
# never be enabled outside local dev.
if settings.env != "dev" and settings.dev_auth_bypass:
    raise RuntimeError("DEV_AUTH_BYPASS must be false outside dev.")
if settings.jwt_secret_is_default:
    logging.getLogger("uvicorn.error").warning(
        "Using the default dev JWT secret — override JWT_SECRET before deploying."
    )

# Allow the configured browser origins to call the API (credentialed CORS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

API_V1 = "/api/v1"
app.include_router(auth_router, prefix=API_V1)
app.include_router(meta_router, prefix=API_V1)
app.include_router(onboarding_router, prefix=API_V1)
app.include_router(catalog_router, prefix=API_V1)
app.include_router(sales_router, prefix=API_V1)
app.include_router(inventory_router, prefix=API_V1)
app.include_router(procurement_router, prefix=API_V1)
app.include_router(finance_router, prefix=API_V1)
app.include_router(documents_router, prefix=API_V1)
app.include_router(dashboards_router, prefix=API_V1)
app.include_router(production_router, prefix=API_V1)
app.include_router(quality_router, prefix=API_V1)
app.include_router(shipments_router, prefix=API_V1)
app.include_router(admin_router, prefix=API_V1)
app.include_router(ai_router, prefix=API_V1)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "env": settings.env}

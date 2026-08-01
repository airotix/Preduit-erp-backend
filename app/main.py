"""Preduit ERP backend — FastAPI application entrypoint."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.modules.admin.router import router as admin_router
from app.modules.ai.router import router as ai_router
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

# Allow the Next.js dev server to call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_V1 = "/api/v1"
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

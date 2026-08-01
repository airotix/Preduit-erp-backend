# Preduit ERP — Backend (FastAPI)

Phase 0 foundation for the multi-tenant apparel ERP. See
`Docs/BACKEND_ARCHITECTURE_PLAN.md` for the full design.

> **Layout note:** the repo has two top-level folders — `frontend/` and
> `backend/`. The database migrations (`db/`), design docs (`Docs/`) and Azure
> infra (`infra/`) now live **inside `backend/`**.

## What's here

```
backend/
├── app/                       # FastAPI application
│   ├── main.py                # app + router wiring (/api/v1)
│   ├── core/
│   │   ├── config.py          # settings; builds app + system DB URLs
│   │   ├── database.py        # two engines + tenant SESSION_CONTEXT plumbing
│   │   ├── security.py        # Entra External ID JWT validation → Principal
│   │   └── deps.py            # tenant_db: RLS-scoped session dependency
│   ├── models/                # SQLAlchemy 2.0 (reflect via sqlacodegen in prod)
│   ├── presenters/screen.py   # entity → frontend ScreenConfig (BFF seam)
│   └── modules/               # one folder per module: router·service·repo·dto
├── db/                        # SQL Server migrations (V001…V036), seeds, RLS
├── Docs/                      # architecture & module plans
├── infra/                     # Azure Bicep (ACR, Container Apps, SQL)
├── .env.example
└── requirements.txt
```

Two DB principals enforce the isolation model:
- **`erp_app`** — runtime user, **subject to Row-Level Security**. Every request
  sets `SESSION_CONTEXT('tenant_id')`; the pool clears it on checkout so a stale
  tenant can't leak.
- **`erp_system`** — provisioning user, **exempt from RLS**. Used only by
  onboarding to create tenants and seed defaults.

## Local development

```bash
cp .env.example .env          # fill in SQL + Entra values
pip install -r requirements.txt
uvicorn app.main:app --reload # http://localhost:8000/docs
```

You need the **ODBC Driver 18 for SQL Server** installed locally (the Docker
image installs it automatically).

## Database migrations (database-first)

The schema is owned by `db/` (Flyway). Apply it with the **system** principal:

```bash
cd db
flyway -url="$FLYWAY_URL" -user="$SQL_SYSTEM_USER" -password="$SQL_SYSTEM_PASSWORD" migrate
```

Migrations run `V001` (core platform + RLS-ready tables) through the latest
(`V036`), covering catalog, sales, inventory, procurement, finance, production,
the AI snapshot store and product images. Seeds live in `db/seed_dev_*.sql`.

After the schema exists, regenerate ORM models from the live DB instead of
hand-maintaining them:

```bash
sqlacodegen "$SQLALCHEMY_URL" --outfile app/models/_generated.py
```

## Key endpoints (Phase 0)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/health` | none | liveness |
| POST | `/api/v1/onboarding/organization` | signed-in (no tenant yet) | self-serve create org |
| GET  | `/api/v1/catalog/products/screen` | tenant | ScreenConfig for the products list |
| POST | `/api/v1/catalog/products` | tenant | create a product (real write path) |

## Container build & deploy

```bash
docker build -t preduit-backend .
# Push to ACR + deploy via infra/main.bicep (see infra/)
```

## Not yet wired (deliberate Phase 0 boundaries)

- Writing `tenant_id` back to the user's Entra profile after org creation
  (Graph API) so the next token carries the claim — marked TODO in
  `modules/onboarding/service.py`.
- Redis caching, background jobs (FX ingest, forecasts), and the remaining 11
  modules — these follow the catalog module's shape.

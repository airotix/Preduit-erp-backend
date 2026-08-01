# Demand Planning module — how it works with `preduit-f`

This document explains the ERP's **Demand Planning** module (module id `ai`, formerly labelled "AI Insights"): what it is, how it connects to the `preduit-f` forecasting engine, what we built, and the steps to make it live.

Audience: developers working on Preduit ERP. Everything here lives in `preduit-erp`. `preduit-f` is treated as a **read-only reference** and an **external service** — we never modify it.

---

## 1. What it is

`preduit-f` (the "Forcaster") is a standalone demand-forecasting product: a data pipeline that produces per-SKU forecasts, reorder recommendations, customer-coverage analysis and validation metrics, exposed over an HTTP API (the "engine"). It also ships its own Next.js dashboard.

AI Insights is the ERP module that brings those forecasting screens **into the ERP** as native tabs, so users get forecasts, recommendations and validation without leaving Preduit. The forecasting math stays in the engine; the ERP owns presentation, access control, and a local copy of the data.

The tabs (under **Demand Planning** in the left rail):

| Tab | Purpose | Engine endpoint(s) it maps to |
|---|---|---|
| Setup | Season config (ERP-owned) + data sync / forecast / backtest triggers (2-step wizard) | `POST /season-config`, `/ingest/erp`(+`/status`), `/forecast/refresh`(+`/job`), `/forecast/backtest`(+`/status`) |
| Product KPIs | Diffusion / depth / reorder / demand signals per SKU, with a drill-down | `GET /products` |
| SKU Recommendations | Review, adjust (size overrides), lock the engine's order recommendations | `GET /recommendations`, `PUT /recommendations/{ref}/{couleur}`, `POST …/lock`, `…/unlock` |
| Demand Projection | Projected units by scenario, expandable color×size stock matrix | `GET /projections`, `GET /projections/{ref}/detail` |
| Customer Coverage | Visited vs non-visited coverage, AI-matched profiles, purchase history | `GET /customers`, `GET /customer-detail` |
| Order Validation | Scenario comparison, override summary + revert, backtest accuracy, audit | `GET /validation`, `POST /overrides/revert`, `/overrides/revert-all`, `GET /forecast/accuracy`, `GET /audit` |

The Forcaster's **Dashboard** and **Budget** screens are intentionally **not** rebuilt — the ERP's own Dashboards and Finance modules already cover those. The Forcaster's **Season Setup** screen **is** included (as the first tab): season config is stored in the ERP (works offline), while the data-sync / forecast-refresh / backtest actions are proxied to the engine and gated on it being live. Note the ERP remains the system of record for master data — it feeds the engine; the engine feeds forecasts back.

---

## 2. Architecture

The browser never talks to the engine directly. The ERP backend is the only party that converses with the engine.

```
┌────────────┐   /api/v1/ai/*   ┌─────────────────────┐   HTTP   ┌──────────────────┐
│  Browser   │ ───────────────▶ │  ERP backend (ai)   │ ───────▶ │  preduit-f engine │
│ AI tabs    │ ◀─────────────── │  facade + snapshot  │ ◀─────── │  (forecast API)   │
└────────────┘   ERP JSON       │  store (SQL Server) │  engine  └──────────────────┘
                                └─────────────────────┘   JSON
```

Why this shape:

- **No CORS / no browser secrets.** The engine URL and token live only in the backend env. The browser only ever calls the ERP, same origin as every other module.
- **Tenant isolation.** AI data is stored per tenant with Row-Level Security, exactly like the rest of the ERP.
- **Resilience.** Reads are served from a local snapshot store; the engine can be slow, down, or not-yet-built without breaking the tabs.

### The snapshot store (the "same DB tables" you asked for)

Migration `db/V032__ai_insights.sql` creates two tenant-scoped, RLS-protected tables:

- **`ai_snapshot`** — one row per `(tenant_id, kind, scope)` holding the engine's JSON response verbatim in a `data NVARCHAR(MAX)` column, plus `synced_at`.
  - `kind` ∈ `dashboard | products | projections | projection_detail | budget | recommendations | validation | customers | customer_detail | audit | accuracy | season_config`
  - `season_config` is ERP-owned (saved by the Setup tab, works offline); the rest are engine snapshots.
  - `scope` = the scenario (`base` / `conservative` / `optimistic`) for scenario-specific kinds, or a reference/customer key for detail kinds, or `''` otherwise.
- **`ai_sync_state`** — one row per tenant recording the last sync outcome (`status`, `last_synced_at`, `message`).

This is a materialised cache of the engine's answers: real ERP tables, owned by the ERP, populated by conversing with the engine.

---

## 3. What we built

### Backend (`preduit-erp/backend/app`)

- `models/ai.py` — `AiSnapshot`, `AiSyncState` ORM models.
- `modules/ai/repository.py` — snapshot upsert/get/delete + sync-state upsert (RLS-scoped queries).
- `modules/ai/service.py` — the brains:
  - an `httpx` engine client (`_engine_get` / `_engine_send`) using `AI_ENGINE_URL` + optional `AI_ENGINE_TOKEN`;
  - `_cached(...)` — serve from `ai_snapshot`, else fetch from the engine and cache (lazy);
  - typed read helpers (`dashboard`, `products`, `projections`, `projection_detail`, `budget`, `recommendations`, `validation`, `customers`, `customer_detail`, `audit`, `accuracy`);
  - write-throughs (`update_recommendation`, `set_lock`, `revert_override`, `revert_all_overrides`) that POST to the engine then invalidate the affected snapshots so the next read re-syncs;
  - `sync_all(...)` — refresh every collection snapshot; `sync_state(...)` — report status;
  - **graceful degradation**: an `_EMPTY` payload per kind so reads return valid-but-empty data (never 502) when the engine is disabled or unreachable and nothing is cached.
- `modules/ai/router.py` — the `/ai` facade: `GET` reads, `PUT/POST` writes, `POST /ai/sync`, `GET /ai/sync-state`. Registered in `main.py` under `/api/v1`.
- `core/config.py` — new settings: `ai_engine_enabled` (default **false**), `ai_engine_url`, `ai_engine_token`.

### Frontend (`preduit-erp/frontend/src`)

- `lib/ai-client.ts` — thin fetch helper pointing at the ERP backend's `/ai` facade (derives from `NEXT_PUBLIC_API_URL` + `/ai`; **not** the engine).
- `lib/ai-api.ts` — typed calls for every endpoint incl. `runAiSync` / `fetchAiSyncState`.
- `lib/ai-types.ts` — the engine's response contracts as TypeScript types.
- `lib/ai-context.tsx` — module-scoped scenario + season state (persisted).
- `config/navigation.ts` — the five AI tabs.
- `app/(erp)/[module]/[tab]/page.tsx` — branches `module === "ai"` to a bespoke `AiScreen` (same pattern as Finance).
- `components/screens/ai/` — `ai-screen` (dispatcher), `ai-header` (with the Sync control + scenario switcher), `ai-shared` (ConfidenceBadge, DemandSplitBar, ExplainabilityPanel, FinancialImpactCard, etc.), `ai-sync-control`, and the five tab screens (`ai-product-kpis`, `ai-recommendations`, `ai-projection`, `ai-customers`, `ai-validation`) — all restyled to the ERP's orange/navy tokens.

---

## 4. How it works at runtime

**Reads.** A tab calls e.g. `GET /api/v1/ai/products`. The backend looks in `ai_snapshot` for `(tenant, "products", "")`. On a hit it returns the stored JSON. On a miss it fetches `GET {AI_ENGINE_URL}/products`, stores the response, and returns it. Subsequent loads are served from the store until a sync/write refreshes it.

**Scenario-aware reads.** Recommendations / Validation / Budget are stored per scenario (`scope = base|conservative|optimistic`), so switching the scenario switcher reads a different snapshot row.

**Drill-downs.** Projection detail and customer detail are fetched lazily on first open (`scope` = the reference/customer key) and cached.

**Writes.** Editing SKU sizes, locking, or reverting an override calls the ERP (`PUT/POST /api/v1/ai/...`). The backend forwards to the engine, then deletes the affected snapshot rows (`recommendations`, `validation`, `audit`, …) so the next read pulls fresh numbers.

**Sync.** The **Sync now** button in the AI header calls `POST /api/v1/ai/sync`, which refreshes all collection snapshots from the engine and updates `ai_sync_state`. The header shows a status dot + "Synced 3m ago / never synced / Engine offline / Sync failed", read from `GET /api/v1/ai/sync-state`.

**Offline (current state).** With `AI_ENGINE_ENABLED=false` (the default until the engine is live), the backend makes **no** engine calls: reads return empty payloads, writes return a friendly 503, sync reports `offline`, and the header shows the amber "Engine offline" indicator. The tabs load and render as empty states — nothing crashes.

---

## 5. The engine contract (what `preduit-f` must expose)

The AI tabs are wired to this flat API (base = `AI_ENGINE_URL`, e.g. `http://<engine-host>/api`):

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/dashboard` | — | season KPIs + financial summary |
| GET | `/products` | — | `ProductRow[]` (KPIs per SKU) |
| GET | `/projections` | — | `ProjectionRow[]` (scenario totals + explainability) |
| GET | `/projections/{reference}/detail` | — | color×size stock matrix |
| GET | `/budget` | `scenario` | budget vs plan |
| GET | `/recommendations` | `scenario` | `RecommendationRow[]` (scenarios, size breakdown, financial impact) |
| PUT | `/recommendations/{reference}/{couleur}` | body `{sizeBreakdown,reason,notes}` | updated rec |
| POST | `/recommendations/{reference}/{couleur}/lock` \| `/unlock` | — | status |
| GET | `/validation` | `scenario` | scenario metrics + overrides |
| POST | `/overrides/revert` | body `{reference,couleur,taille}` | status |
| POST | `/overrides/revert-all` | — | status |
| GET | `/customers` | — | `{customers, regions, latestSeason}` |
| GET | `/customer-detail` | `customer` | `{customer, purchaseHistory, seasons}` |
| GET | `/audit` | `limit` | audit entries |
| GET | `/forecast/accuracy` | — | backtest WMAPE/bias/coverage |

The exact TypeScript shapes are in `frontend/src/lib/ai-types.ts`.

> **Contract gap to be aware of.** The engine's checked-in README (`preduuit-f/backend-ai/README.md`) only documents `GET /api/articles` and `GET /api/filter-options`. The screens above assume a fuller API (per the "assume the full API exists" decision). Two ways to reconcile when going live:
> 1. **Extend the engine** to expose the endpoints in the table (recommended — the ERP is already wired for them), or
> 2. **Add a translation layer** in the ERP `ai/service.py` that calls the engine's actual endpoints (`/articles`, `/filter-options`, `/customers`, `/customer-detail`) and reshapes them into the contract above. The snapshot store means this only has to be done once per kind, in the service.

---

## 6. Going live — checklist

1. **Run the migration** `db/V032__ai_insights.sql` (after V031) against each environment's database.
2. **Stand up the engine** and confirm it serves the endpoints in §5 (or plan the translation layer per the gap note). Verify CORS isn't needed — only the ERP backend calls it, server-to-server.
3. **Configure the backend** `.env`:
   ```
   AI_ENGINE_ENABLED=true
   AI_ENGINE_URL=http://<engine-host>:<port>/api
   AI_ENGINE_TOKEN=<token if the engine requires auth>
   ```
4. **Restart the backend.** No frontend rebuild needed — it already points at `/api/v1/ai`.
5. **Open an AI tab and click "Sync now"** (or hit `POST /api/v1/ai/sync`) to populate the snapshot store. The header should flip to green "Synced just now".
6. (Optional) **Schedule a nightly sync** so the store refreshes automatically.

### Configuration reference

| Where | Var | Meaning |
|---|---|---|
| backend `.env` | `AI_ENGINE_ENABLED` | `false` = offline/empty (default); `true` = call the engine |
| backend `.env` | `AI_ENGINE_URL` | engine base URL (e.g. `http://host:4000/api`) |
| backend `.env` | `AI_ENGINE_TOKEN` | optional bearer token for the engine |
| frontend `.env.local` | `NEXT_PUBLIC_AI_API_URL` | override the AI facade base (defaults to `NEXT_PUBLIC_API_URL` + `/ai`) |
| frontend `.env.local` | `NEXT_PUBLIC_USE_AI_BACKEND` | `false` to disable AI calls from the browser |

---

## 7. Data ownership & sync direction

- **Master data → engine:** the ERP is the system of record for catalog, customers, orders, stock. Feeding those into the engine is the ERP's responsibility ("ERP sync taken from the ERP itself"), not the AI Insights tabs.
- **Forecasts → ERP:** the engine returns forecasts/recommendations; the ERP stores them in `ai_snapshot` and serves the tabs.
- **User decisions → engine:** overrides, locks and reverts made in the ERP are pushed back to the engine so both stay consistent; validated orders then flow through the normal ERP order/procurement path.

---

## 8. File map

```
preduit-erp/
├─ db/V032__ai_insights.sql                         # ai_snapshot + ai_sync_state (+ RLS)
├─ backend/app/
│  ├─ core/config.py                                # AI_ENGINE_* settings
│  ├─ main.py                                        # registers the ai router
│  ├─ models/ai.py                                   # AiSnapshot, AiSyncState
│  └─ modules/ai/{__init__,dto,repository,service,router}.py
└─ frontend/src/
   ├─ config/navigation.ts                           # the 5 AI tabs
   ├─ app/(erp)/[module]/[tab]/page.tsx              # module === "ai" → AiScreen
   ├─ lib/{ai-client,ai-api,ai-types,ai-context}.ts(x)
   └─ components/screens/ai/
      ├─ ai-screen.tsx  ai-header.tsx  ai-shared.tsx  ai-sync-control.tsx
      └─ ai-setup / ai-product-kpis / ai-recommendations / ai-projection / ai-customers / ai-validation .tsx
```

See also `docs/AI_INSIGHTS_PLAN.md` for the design decisions and history.

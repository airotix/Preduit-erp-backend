# AI Insights — build plan (revised)

**Approach (per your direction):** leave the `backend-ai` engine and its `forecast.db` completely untouched. Instead, **replicate the Forcaster ("preduit") app's screens** inside our ERP's AI Insights module and connect them to the engine over its **external HTTP API**. No ERP-backend work, no DB reads, no pipeline changes — pure frontend replication + a base-URL/CORS config.

## 1. What we're replicating
The attached `preduuit-f/frontend` is a Next.js 14 dashboard for the forecasting engine (READ-ONLY reference — no edits are ever made there). We rebuild its forecasting-specific screens directly inside the ERP.

**Scope rule (per Zaki):** the ERP already has a Dashboards module and a Finance module, so we do **not** re-create the Forcaster Dashboard or Budget/financial screens as AI tabs. Data ingest/sync is owned by the ERP (the ERP feeds the engine), so we skip the Season Setup / import screens too.

| Forcaster screen | Route | AI Insights tab? |
|---|---|---|
| Dashboard | `/dashboard` | ❌ already have (Dashboards module) |
| Budget Tracker | `/budget` | ❌ already have (Finance module) |
| Season Setup | `/setup` | ❌ ERP owns sync/ingest |
| Product KPIs | `/products` | ✅ Product KPIs |
| SKU Recommendations | `/recommendations` | ✅ SKU Recommendations |
| Demand Projection | `/projection` | ✅ Demand Projection |
| Customer Coverage | `/customers` | ✅ Customer Coverage |
| Order Validation | `/validation` | ✅ Order Validation |

These five replace our current placeholder tabs (Forecast / Reorder / Anomalies / AI Reports).

It ships the components we'll port: `KPICard`, `ForecastCharts`, `ForecastMatrix` / `DetailedStockMatrix` / `SKUStockMatrix`, `FilterPanel`, `SeasonSelector`, `ConfidenceBadge`, `ScenarioSwitcher`, `ExplainabilityPanel`, `FinancialImpactCard`, `SizeEditorModal`, `CustomerPanel`, `BudgetProgressBar`, `AlertBanner`, plus typed API modules (`products`, `customers`, `seasons`, `forecast`, `validation`) and Zustand stores (`season`, `dashboard`, `products`, `customers`, `ui`).

> **Architecture update (per Zaki): the ERP backend owns the data.** The browser no longer calls the engine directly. The ERP backend has its own tenant-scoped tables (`ai_snapshot`, `ai_sync_state`, migration `V032`) that it **populates by conversing with the engine over HTTP**, and the AI screens read/write through the ERP `/api/v1/ai` facade like every other module. Reads serve from the snapshot store (lazy-fetched + cached on first miss, refreshed by `POST /ai/sync`); writes (overrides, lock/unlock, revert) are forwarded to the engine and the affected snapshots invalidated. The engine URL + token live in the **backend** env (`AI_ENGINE_URL` / `AI_ENGINE_TOKEN`) — CORS and secrets stay server-side. Sections 2–3 below describe the original direct-to-engine wiring and are superseded by this.

## 2. How it connects to the engine
The Forcaster frontend calls the engine via `apiClient` at `NEXT_PUBLIC_API_URL` (default `http://localhost:4000/api`) with an optional `Authorization: Bearer <token>`. We replicate that:
- A dedicated **AI API client** in our app (ported from `lib/api/client.ts`) reads a **separate** base URL — `NEXT_PUBLIC_AI_API_URL` — so it's independent of our ERP backend (which owns `NEXT_PUBLIC_API_URL`). Default it to the engine host (e.g. `http://localhost:4000/api`).
- Season state drives every request (`?season=`), via a ported `SeasonSelector` + season store.
- **Auth:** their client attaches an MSAL bearer token. For our embed, make the token provider pluggable — in dev, no token (engine dev mode); in prod, reuse our existing auth token. Decision needed (see §6).
- **CORS:** the engine must allow our ERP frontend origin. That's an engine-side config, not ours — flag to the engine owner.

## 3. Module architecture in our app
AI Insights becomes a **bespoke module** (like Finance/Production), not the generic list/board renderer — the screens are rich (matrices, scenario switcher, size-editor overrides, explainability). Plan:
- Port `preduuit-f/frontend/src` screens + components into `frontend/src/modules/ai/forcaster/` (namespaced), adapted to our imports and, where cheap, restyled to our tokens; otherwise kept close to source to minimize risk.
- Reuse their `types/*` and `lib/api/*` verbatim (pointed at `NEXT_PUBLIC_AI_API_URL`) so the screens work against the engine unchanged.
- `navigation.ts`: replace the four AI tabs with the screen set above.
- `app/(erp)/[module]/[tab]/page.tsx`: branch `module === "ai"` to a bespoke `AiScreen` that renders the matching Forcaster screen by tab id (mirrors the finance branch).
- Bring their Zustand stores in scoped to the module (avoid clashing with our React Query usage; they can coexist).

## 4. Per-tab summary (all read from the engine API)
- **Dashboard** — season KPIs + forecast charts + alerts (engine dashboard/forecast endpoints).
- **Customer Coverage** — customer list + coverage panel (`customers` API).
- **Product KPIs** — article KPI grid + forecast/stock matrices (`products` API).
- **Budget Tracker** — budget progress vs plan (`products`/budget data).
- **Demand Projection** — projection charts + scenario switcher (`forecast`/projections API).
- **SKU Recommendations** — reorder/size recommendations with size-editor overrides (`products` recommendations + override payload).
- **Order Validation** — validation queue/approve (`validation` API).

## 5. Build phases
1. AI API client + types + scenario context; add `NEXT_PUBLIC_AI_API_URL`; nav tabs (5 screens) + `AiScreen` branch in the route dispatcher.
2. Product KPIs + forecast/stock matrices.
3. SKU Recommendations (+ size editor / overrides — write back to the engine).
4. Demand Projection + Customer Coverage.
5. Order Validation (approve/revert write-backs).
6. Styling pass to our ERP tokens + static verification against the engine contract.

(Dashboard, Budget and Season Setup are intentionally NOT built — the ERP already owns those.)

## 6. Decisions (confirmed)
- **Engine API surface — ASSUME FULL API EXISTS.** Port every screen against the frontend's full API layer (dashboard/products/customers/projections/recommendations/validation/seasons). Where the engine doesn't yet expose an endpoint, that's an engine-side fix; our screens are wired correctly and will light up once the route exists. (Note: the checked-in engine README documents only `/api/articles` + `/api/filter-options` — so expect some tabs to be empty until the engine is extended.)
- **Styling — RESTYLE TO ERP TOKENS.** Rebuild each screen against our orange/navy ERP design system (not the Forcaster blue/dark theme) so the tabs feel native.
- **Write-backs — ENABLED.** SKU-recommendation overrides and order-validation approvals POST straight through to the engine API from day one.
- **CORS + base URL/port**: engine must allow our ERP origin; `NEXT_PUBLIC_AI_API_URL` points at the engine host (default `http://localhost:4000/api`). Engine-side config — flag to owner.
- **Auth**: token provider is pluggable; dev = no token, prod = reuse our ERP auth token.

# Preduit ERP — Backend & Database Architecture Plan

**Author:** Senior Architecture review
**Date:** 2026-07-24
**Scope:** Backend service + database + Azure hosting for the existing Next.js apparel-ERP frontend (`frontend/`).

## Decisions (locked)

| Concern | Decision |
|---|---|
| Hosting | **Azure**, managed PaaS |
| Backend | **Python / FastAPI** |
| Database | **Azure SQL (SQL Server)** |
| Tenancy | **Multi-tenant SaaS**, **self-serve signup** |
| Currency | **True multi-currency** (transaction + base/reporting, FX rates) |
| Identity | **Microsoft Entra External ID** (CIAM) |
| Data residency | Single region to start; **region-aware design** so it can be added when selling starts |
| Legacy data | **Greenfield** now; a bulk-import seam is designed in for later |
| Method | **Database-first** — the schema is the single source of truth |

The frontend today is a UI shell backed by in-memory mocks. Each module has an `api.ts` that returns a presentation-shaped `ScreenConfig`. The goal is to stand up a real, tenant-isolated backend and a normalized SQL Server database on Azure, and swap those mocks module by module without rewriting the frontend renderers.

**Suggested repo layout** (matching the new `frontend/` + `docs/` split):

```
preduit-erp/
├── frontend/     # existing Next.js app
├── backend/      # (to add) FastAPI service
├── db/           # (to add) Flyway T-SQL migrations — source of truth
├── infra/        # (to add) Bicep/Terraform for Azure
└── docs/         # this plan and future design docs
```

---

## 1. Guiding principles

**Database-first, but disciplined.** The schema is authored as versioned SQL migrations checked into git and is the authority. Application models are generated/derived from it, never the reverse.

**Tenant isolation enforced at the database, not just the app.** In multi-tenant SaaS the most expensive bug class is one tenant reading another's data. We push isolation down to SQL Server Row-Level Security (RLS) so that even a buggy query or a forgotten `WHERE` clause cannot leak across tenants — especially important because **self-serve signup means untrusted tenants share the database from day one**.

**Financial and inventory data are append-only ledgers.** Stock balances and account balances are *derived*, never edited in place. This gives auditability (a hard ERP requirement) and makes reconciliation possible.

**The backend owns domain shape; a thin presenter owns screen shape.** The API exposes clean normalized resources. A small presenter layer maps entities to the existing `ScreenConfig`/`Cell` format so the frontend's generic renderers keep working during migration (a Backend-for-Frontend seam).

---

## 2. Multi-tenancy model

| Model | Isolation | Cost at scale | Ops complexity | Verdict |
|---|---|---|---|---|
| DB-per-tenant | Strongest | High | High | Later, for large/regulated tenants |
| Schema-per-tenant | Medium | Medium | High (schema sprawl) | No |
| **Shared DB + shared schema + `tenant_id` + RLS** | Strong (DB-enforced) | Low | Low | **Yes — start here** |

**Recommendation: shared database, shared schema, `tenant_id` on every business table, enforced by SQL Server Row-Level Security**, on an Azure SQL **Elastic Pool** so a large tenant can graduate to a dedicated database behind the same code path. This model is the right fit for **self-serve SaaS**, where you'll onboard many small tenants cheaply and promote the few large ones later. The application never writes `WHERE tenant_id = ?` by hand — it sets a session context and RLS filters.

### How isolation works end to end

1. Request arrives with a JWT that carries a `tenant_id` claim (issued via Entra External ID; see §7).
2. FastAPI middleware resolves the tenant and, on connection checkout, runs:

   ```sql
   EXEC sp_set_session_context @key = N'tenant_id', @value = @tenant_id, @read_only = 1;
   ```

3. An RLS security policy filters every query and blocks cross-tenant inserts:

   ```sql
   CREATE FUNCTION dbo.fn_tenant_predicate(@tenant_id UNIQUEIDENTIFIER)
   RETURNS TABLE WITH SCHEMABINDING AS
   RETURN SELECT 1 AS ok
   WHERE @tenant_id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER);

   CREATE SECURITY POLICY dbo.TenantSecurityPolicy
     ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.products,
     ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.products AFTER INSERT
     -- ... repeat ADD ... for every tenant-scoped table
     WITH (STATE = ON);
   ```

Because the connection pool is reused, the session context **must be set on every checkout** and reset on return — wired into the SQLAlchemy engine's `checkout` event. A tightly-scoped "system" DB principal bypasses RLS only for provisioning/admin jobs.

---

## 3. Database-first workflow & tooling

```
SQL migrations (T-SQL, git)  →  applied by Flyway in CI/CD  →  live Azure SQL schema
                                                                      │
                                              sqlacodegen reflects → SQLAlchemy 2.0 models
                                                                      │
                                          hand-written Pydantic v2 DTOs (API contracts)
```

- **Migrations:** [Flyway](https://flywaydb.org/) — forward-only, versioned `V001__*.sql` T-SQL scripts (live in `db/`). DBA-readable, CI-friendly. (Pure-Microsoft alternative: an SSDT **DACPAC** project. Flyway recommended for explicit history.)
- **ORM models:** SQLAlchemy 2.0 (typed), reflected from the live schema with `sqlacodegen` so models track the DB rather than drive it.
- **DTOs:** Pydantic v2, hand-written and decoupled from ORM models so API contracts stay stable.
- **Driver:** `pyodbc` with **ODBC Driver 18 for SQL Server**. Note: fully-async SQL Server support is immature — run SQLAlchemy in sync mode inside FastAPI's threadpool (or validate `aioodbc` under load first). A deliberate, low-risk choice for SQL Server specifically.

---

## 4. Data model (database-first core)

### 4.1 Conventions on every table

- **Surrogate PK:** `BIGINT IDENTITY` (clustered, compact) for internal joins.
- **Public id:** `UNIQUEIDENTIFIER DEFAULT NEWSEQUENTIALID()` exposed in the API so we never leak sequential counts or enable enumeration. *(Also fixes the frontend's current "row identity by array index" fragility.)*
- **Tenant:** `tenant_id UNIQUEIDENTIFIER NOT NULL` on every business table (RLS target).
- **Money:** `DECIMAL(19,4)` + explicit `currency_code CHAR(3)` — never floats (see §4.4).
- **Audit:** `created_at`, `created_by`, `updated_at`, `updated_by`.
- **Concurrency:** `row_version ROWVERSION` for optimistic locking.
- **Soft delete:** `is_deleted BIT` + `deleted_at` where history matters.

### 4.2 Module → schema map

| Module | Core tables |
|---|---|
| Platform (cross-cutting) | `tenants`, `subscriptions`, `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `audit_log`, `documents`, `notifications`, `notification_settings`, `system_settings`, `approval_rules`, `currencies`, `exchange_rates` |
| Catalog | `products`, `categories`, `attributes` (color/size), `attribute_values`, `product_variants` |
| Inventory | `warehouses`, `locations`, `stock_movements` (ledger), `stock_balances` (derived), `stock_transfers`, `transfer_lines`, `reorder_rules`, `reorder_alerts` |
| Sales & Orders | `customers`, `sales_orders`, `sales_order_lines`, `invoices`, `invoice_lines`, `returns`, `return_lines`, `fulfillment_status` |
| Procurement | `suppliers`, `purchase_orders`, `po_lines`, `goods_receipts`, `receipt_lines`, `vendor_scorecards`, `approvals` |
| Finance | `chart_of_accounts`, `journal_entries`, `journal_lines`, `payments`; AR/AP aging as **views** |
| Production | `production_orders`, `production_stages`, `bill_of_materials`, `bom_lines` |
| Quality | `inspections`, `inspection_defects`, `defect_types`, `quality_scores` |
| Shipments | `shipments`, `shipment_lines`, `carriers`, `tracking_events` |
| Channels | `channels`, `channel_mappings`, `sync_logs` |
| AI Insights | `forecasts`, `reorder_suggestions`, `anomalies`, `ai_reports` |
| Dashboards | No tables — aggregation queries/views over the above |

### 4.3 Three design points that matter most for apparel

**(a) Product → Variant (color × size matrix).** SKU lives at the variant level.

```sql
CREATE TABLE dbo.product_variants (
  id             BIGINT IDENTITY PRIMARY KEY,
  public_id      UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
  tenant_id      UNIQUEIDENTIFIER NOT NULL,
  product_id     BIGINT NOT NULL REFERENCES dbo.products(id),
  sku            NVARCHAR(64) NOT NULL,
  color_id       BIGINT NULL REFERENCES dbo.attribute_values(id),
  size_id        BIGINT NULL REFERENCES dbo.attribute_values(id),
  barcode        NVARCHAR(64) NULL,
  price          DECIMAL(19,4) NOT NULL,
  currency_code  CHAR(3) NOT NULL,
  status         NVARCHAR(20) NOT NULL DEFAULT 'Active',
  row_version    ROWVERSION,
  CONSTRAINT uq_variant_sku UNIQUE (tenant_id, sku)
);
```

**(b) Inventory as an append-only ledger.** Never `UPDATE` a quantity; every receipt/shipment/transfer/adjustment writes a signed movement, and the current balance is maintained in the *same transaction*.

```sql
CREATE TABLE dbo.stock_movements (
  id           BIGINT IDENTITY PRIMARY KEY,
  tenant_id    UNIQUEIDENTIFIER NOT NULL,
  variant_id   BIGINT NOT NULL REFERENCES dbo.product_variants(id),
  location_id  BIGINT NOT NULL REFERENCES dbo.locations(id),
  qty_delta    DECIMAL(19,4) NOT NULL,          -- +receipt / -shipment
  movement_type NVARCHAR(24) NOT NULL,          -- RECEIPT|SHIP|TRANSFER|ADJUST
  ref_type     NVARCHAR(24) NULL, ref_id BIGINT NULL,
  occurred_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by   BIGINT NOT NULL
);
-- current on-hand = SUM(qty_delta) per (tenant, variant, location),
-- kept in dbo.stock_balances, updated inside the writing transaction.
```

**(c) Finance as strict double-entry.** Debits equal credits per entry (SQL Server can't do a cross-row check, so a posting trigger enforces balance).

```sql
CREATE TABLE dbo.journal_lines (
  id BIGINT IDENTITY PRIMARY KEY, tenant_id UNIQUEIDENTIFIER NOT NULL,
  journal_entry_id BIGINT NOT NULL REFERENCES dbo.journal_entries(id),
  account_id BIGINT NOT NULL REFERENCES dbo.chart_of_accounts(id),
  debit DECIMAL(19,4) NOT NULL DEFAULT 0,
  credit DECIMAL(19,4) NOT NULL DEFAULT 0,
  CONSTRAINT ck_debit_xor_credit CHECK (debit = 0 OR credit = 0)
);
```

AR Aging / AP Aging (schema-less action tabs in the frontend) become **SQL views** with date-bucket `CASE` over invoices/payments.

### 4.4 Multi-currency design

Every monetary amount is stored **twice**: in the transaction currency and in the tenant's base (reporting) currency, with the rate used pinned on the record so historical documents never re-value.

- `currencies` — supported ISO 4217 codes, symbol, decimal places.
- `exchange_rates(tenant_id, from_ccy, to_ccy, rate DECIMAL(19,8), valid_from, source)` — dated rates; a job ingests daily rates (ECB or a provider) via Service Bus.
- Each monetary row carries `amount`, `currency_code`, `fx_rate`, and `base_amount` (= `amount × fx_rate`). Aggregations and dashboards run on `base_amount`; documents display the transaction currency.
- Each **tenant has a `base_currency`** (chosen at signup); customers/suppliers may transact in their own currency.
- Period-end **revaluation** of open AR/AP is a scheduled job that posts FX gain/loss journal entries — keep the hook even if it's phase-5 work.

### 4.5 Bulk-import seam (for future legacy migration)

Greenfield now, but design for it: a `staging` schema plus idempotent, tenant-scoped import endpoints (CSV/Excel → validate → upsert by natural key such as SKU/order-no). This lets a future client bring products, customers, suppliers, and opening balances without ad-hoc scripts. No build cost now beyond reserving the schema and naming convention.

---

## 5. Backend service (FastAPI)

Layered, module-per-domain to mirror the frontend's feature slices:

```
backend/app/
├── main.py                 # app, middleware, router registration
├── core/                   # config, security (JWT), DB engine, session-context hook
├── middleware/tenant.py    # resolve tenant → set SESSION_CONTEXT
├── modules/<domain>/       # router · service · repository · models · dto  (×12)
├── presenters/             # entity → ScreenConfig/Cell mapping (BFF seam)
└── jobs/                   # background tasks (sync, FX rates, forecasts, aging, revaluation)
```

- **API:** REST under `/api/v1`, resource-oriented, keyset pagination, filtering, sorting — this is what backs the frontend's currently-visual filters and placeholder search.
- **Contracts:** FastAPI auto-generates OpenAPI; the frontend generates a typed client from it.
- **Transactions:** multi-table operations (order + lines + inventory movement + ledger entry) run in one DB transaction.
- **Write path the frontend is missing:** today "New …" is a stub that only invalidates the query. The backend supplies real `POST/PUT/DELETE` with Pydantic validation, optimistic concurrency via `row_version` (412 on conflict), returning the created entity.

---

## 6. Self-serve tenant onboarding

Self-serve signup is a first-class flow, not an afterthought:

1. **Sign up** through Entra External ID (email/password or social). On first login the user has no tenant.
2. **Create organization** → backend provisions a `tenant` row, seeds defaults (chart of accounts template, default warehouse/location, roles, base currency, system settings), and makes the signup user the tenant **Owner**.
3. **Subscription/trial** — a `subscriptions` table tracks plan, trial expiry, seat count; usage metering feeds future billing (Stripe or Azure Marketplace).
4. **Invite teammates** — Owner invites users; invitations map to Entra External ID + `user_roles`.
5. **Guardrails** — per-tenant rate limits and quotas (rows, storage, API calls) since untrusted tenants self-provision; abuse/fraud checks on signup.

Provisioning runs under the system principal (bypasses RLS) inside a transaction so a half-created tenant can't exist.

---

## 7. Identity — Microsoft Entra External ID

**Decision: Microsoft Entra External ID** (Microsoft's current CIAM product). Rationale: **Azure AD B2C has been closed to new customers since 1 May 2025**, so B2C is not an option for a greenfield build — Entra External ID is Microsoft's recommended and forward-compatible path for self-serve external identities. It supports OIDC, SAML, WS-Fed and social logins (Google, Apple, etc.), and stays native to the chosen Azure stack.

- Users authenticate against Entra External ID; the app validates JWTs and reads `tenant_id` + roles from claims (custom claims populated at organization-create time).
- Sign-up/sign-in user flows are customizable to match the Systems Limited design system.
- RBAC maps to the existing Admin → Roles/Users data; route guards + per-role nav in the frontend.

---

## 8. Azure hosting & deployment

```
Client ──> Azure Front Door (+ WAF)
              │
              ▼
        Azure Container Apps  ── FastAPI (autoscale, revisions, VNet integrated, Managed Identity)
     ┌────────┼───────────────┬───────────────┬──────────────┐
     ▼        ▼               ▼               ▼              ▼
 Azure SQL  Azure Cache   Azure Blob     Azure Service   App Insights
 (Elastic   for Redis     Storage        Bus / Queues    + Log Analytics
  Pool)     (cache,       (documents,    (async: sync,
            rate-limit)   QC photos)      FX, forecasts,
                                          revaluation)
              │
   Azure Container Registry (images)  ·  Azure Key Vault (secrets via Managed Identity)
                     Auth: Microsoft Entra External ID
```

| Concern | Azure service | Notes |
|---|---|---|
| API compute | **Azure Container Apps** | Docker image, KEDA autoscaling, blue/green revisions. |
| Database | **Azure SQL** in an **Elastic Pool** | GP to start, Business Critical for prod. TDE, PITR, geo-replication, Private Endpoint. |
| Cache / broker | **Azure Cache for Redis** | Response cache, rate limiting, job broker. |
| Object storage | **Azure Blob Storage** | Documents, QC photos, shipment docs. |
| Async jobs | **Azure Service Bus** + Container Apps jobs | Channel sync, FX ingest, forecasts, aging, revaluation. |
| Secrets | **Azure Key Vault** + **Managed Identity** | No secrets in code/env. |
| Identity | **Microsoft Entra External ID** | Self-serve signup, SSO, social. |
| Observability | **App Insights + Log Analytics** | Tracing, structured logs, per-tenant metering. |
| Edge | **Azure Front Door + WAF** | TLS, routing, OWASP. |

**Networking/security:** private endpoints for SQL, Redis, Blob, Key Vault; VNet-integrated Container Apps; Managed Identity for all service-to-service auth; no public DB exposure.

**Region-aware design (data residency, deferred).** Everything is deployed to one Azure region now, but the design supports adding regions later without rework: a `tenants.region` column and a routing layer at Front Door mean a future EU/US/APAC tenant can be pinned to an in-region Elastic Pool. Decide actual regions when you start selling; nothing here blocks it.

**CI/CD (GitHub Actions or Azure DevOps):** lint/test → build image → push ACR → **run Flyway migrations** (gated, forward-only, reviewed) → deploy Container Apps revision → shift traffic after health checks. Environments: **dev / staging / prod**, each with its own Azure SQL + Container App; prod migrations require approval.

---

## 9. Delivery phases

Each phase ends by swapping that module's frontend `api.ts` mock for real HTTP — renderers, tables, and forms don't change.

| Phase | Deliverable |
|---|---|
| **0 — Foundation** | Schema v1 as Flyway migrations, RLS policies, Azure infra as IaC (Bicep/Terraform), CI/CD, Entra External ID + self-serve onboarding, tenant middleware, currency/FX tables. A secure, deployable skeleton. |
| **1 — Platform + Catalog** | Users/roles/RBAC, tenant onboarding UI, Catalog CRUD (products/variants/categories/attributes). Proves the full stack end to end. |
| **2 — Inventory + Sales** | Stock ledger + balances, transfers, reorder alerts; customers, orders, invoices (multi-currency), fulfillment board, returns. |
| **3 — Procurement + Finance** | POs, approvals, receipts, suppliers, scorecards; chart of accounts, journals, payments, AR/AP aging views. |
| **4 — Production + Quality + Shipments** | Production orders/stages/BOM; inspections/defects/scores; shipments/carriers/tracking. |
| **5 — Channels + AI** | Channel connections + sync jobs; forecasts, reorder suggestions, anomalies, AI reports; FX revaluation; dashboards on live aggregates. |

---

## 10. Cross-cutting checklist

- **Security:** RLS tenant isolation, TDE, Key Vault + Managed Identity, RBAC, full `audit_log`, per-tenant rate limits/quotas (critical for self-serve), WAF, private endpoints.
- **Data integrity:** FK + check constraints, transactional multi-table writes, double-entry trigger, append-only ledgers, pinned FX rates.
- **Concurrency:** `ROWVERSION` optimistic locking surfaced via the API (412 on conflict).
- **Performance:** covering indexes on `(tenant_id, …)` for hot queries, keyset pagination, Redis caching for dashboards, optional read replica for reporting.
- **Observability:** structured logging, tracing, health/readiness probes, per-tenant usage metering (for SaaS billing).
- **Compliance/DR:** PITR backups, geo-replication, documented RTO/RPO, per-tenant data export & delete (GDPR).

---

## 11. Next concrete steps

1. Draft the Phase 0 Flyway migration (`db/V001__core.sql`): tenants, users/roles, currencies/FX, RLS policies.
2. Write the Bicep/Terraform for the Azure footprint (SQL Elastic Pool, Container Apps, Key Vault, Redis, Blob, Front Door) under `infra/`.
3. Stand up the FastAPI skeleton (`backend/`) with tenant middleware + Entra External ID token validation.
4. Wire the self-serve onboarding flow (signup → create org → seed defaults).

Sources: [Microsoft Entra External ID overview](https://learn.microsoft.com/en-us/entra/external-id/external-identities-overview), [Azure AD B2C vs Entra External ID (2026) migration guide](https://www.modern42.com/blog/entra-external-id-next-generation-customer-identity-solution-to-replace-azure-b2c)

/* ==========================================================================
   Preduit ERP — Consolidated schema migrations (V001 → V062)
   --------------------------------------------------------------------------
   Run ONCE against the target database to build/upgrade the full schema.
   Every migration is idempotent (IF [NOT] EXISTS guards), so re-running this
   file is safe. Batches are separated by GO, exactly as in the originals.

   Prerequisites (NOT included here — see backend/db/DB_SETUP_GUIDE.md):
     • The database and the erp_system / erp_app logins already exist
       (created by db_setup.ps1). This file only builds objects inside the DB.
     • Demo/sample data (seed_dev_*.sql) is NOT included — schema only.

   HOW TO RUN
     SSMS   : open this file, pick the Preduit-ERP database, Execute (F5).
     sqlcmd : sqlcmd -S localhost -d "Preduit-ERP" -C -i "Preduit-ERP_ALL_MIGRATIONS.sql"

   If your database is not named "Preduit-ERP", change the USE line below.
   ========================================================================== */

USE [Preduit-ERP];
GO


/* ========================================================================
   V001__core_schema.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V001__core_schema.sql
 * Phase 0 core platform schema for Azure SQL / SQL Server.
 *
 * Database-first: this migration is the source of truth for the core tables.
 * Conventions (see docs/BACKEND_ARCHITECTURE_PLAN.md §4.1):
 *   - Business tables: BIGINT IDENTITY surrogate PK + UNIQUEIDENTIFIER public_id.
 *   - The TENANT is the exception: its PK is a UNIQUEIDENTIFIER, because that id
 *     travels in JWT claims and the SQL Server SESSION_CONTEXT used by RLS.
 *   - tenant_id UNIQUEIDENTIFIER on every business table (RLS target) → tenants(id).
 *   - Money: DECIMAL(19,4) + currency_code. Audit + row_version everywhere.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* ------------------------------------------------------------------
 * Reference data — GLOBAL (not tenant-scoped, not under RLS)
 * ------------------------------------------------------------------ */
CREATE TABLE dbo.currencies (
    code            CHAR(3)       NOT NULL CONSTRAINT pk_currencies PRIMARY KEY, -- ISO 4217
    name            NVARCHAR(64)  NOT NULL,
    symbol          NVARCHAR(8)   NULL,
    decimal_places  TINYINT       NOT NULL CONSTRAINT df_currencies_dp     DEFAULT (2),
    is_active       BIT           NOT NULL CONSTRAINT df_currencies_active DEFAULT (1)
);
GO

CREATE TABLE dbo.permissions (
    id           INT IDENTITY   NOT NULL CONSTRAINT pk_permissions PRIMARY KEY,
    code         NVARCHAR(80)   NOT NULL CONSTRAINT uq_permissions_code UNIQUE, -- e.g. 'catalog.product.create'
    description  NVARCHAR(200)  NULL
);
GO

/* ------------------------------------------------------------------
 * Tenants & subscriptions
 * ------------------------------------------------------------------ */
CREATE TABLE dbo.tenants (
    id                  UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_tenants_id DEFAULT NEWSEQUENTIALID()
                                          CONSTRAINT pk_tenants PRIMARY KEY,
    name                NVARCHAR(200)    NOT NULL,
    slug                NVARCHAR(80)     NOT NULL CONSTRAINT uq_tenants_slug UNIQUE, -- subdomain / URL key
    base_currency_code  CHAR(3)          NOT NULL CONSTRAINT fk_tenants_currency REFERENCES dbo.currencies(code),
    region              NVARCHAR(40)     NOT NULL CONSTRAINT df_tenants_region DEFAULT ('primary'),
    status              NVARCHAR(20)     NOT NULL CONSTRAINT df_tenants_status DEFAULT ('Active'), -- Active|Suspended|Deleted
    created_at          DATETIME2        NOT NULL CONSTRAINT df_tenants_created DEFAULT SYSUTCDATETIME(),
    created_by          BIGINT           NULL,   -- users.id; no FK (bootstrap chicken-and-egg)
    updated_at          DATETIME2        NULL,
    updated_by          BIGINT           NULL,
    is_deleted          BIT              NOT NULL CONSTRAINT df_tenants_deleted DEFAULT (0),
    deleted_at          DATETIME2        NULL,
    row_version         ROWVERSION
);
GO

CREATE TABLE dbo.subscriptions (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_subscriptions PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_subs_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_subs_tenant REFERENCES dbo.tenants(id),
    [plan]        NVARCHAR(40)     NOT NULL CONSTRAINT df_subs_plan DEFAULT ('trial'),  -- trial|standard|enterprise ([plan] is a reserved word)
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_subs_status DEFAULT ('trialing'),
    seat_limit    INT              NOT NULL CONSTRAINT df_subs_seats DEFAULT (5),
    trial_ends_at DATETIME2        NULL,
    created_at    DATETIME2        NOT NULL CONSTRAINT df_subs_created DEFAULT SYSUTCDATETIME(),
    row_version   ROWVERSION,
    CONSTRAINT uq_subs_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_subs_tenant ON dbo.subscriptions (tenant_id);
GO

/* ------------------------------------------------------------------
 * Users, roles, RBAC  (users authenticate via Entra External ID;
 * external_id holds the Entra object id / subject)
 * ------------------------------------------------------------------ */
CREATE TABLE dbo.users (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_users PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_users_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_users_tenant REFERENCES dbo.tenants(id),
    external_id  NVARCHAR(128)    NOT NULL,        -- Entra oid/sub
    email        NVARCHAR(256)    NOT NULL,
    display_name NVARCHAR(200)    NULL,
    is_owner     BIT              NOT NULL CONSTRAINT df_users_owner DEFAULT (0),
    status       NVARCHAR(20)     NOT NULL CONSTRAINT df_users_status DEFAULT ('Active'), -- Active|Invited|Disabled
    created_at   DATETIME2        NOT NULL CONSTRAINT df_users_created DEFAULT SYSUTCDATETIME(),
    created_by   BIGINT           NULL,
    updated_at   DATETIME2        NULL,
    updated_by   BIGINT           NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_users_deleted DEFAULT (0),
    deleted_at   DATETIME2        NULL,
    row_version  ROWVERSION,
    CONSTRAINT uq_users_public   UNIQUE (public_id),
    CONSTRAINT uq_users_external UNIQUE (external_id),
    CONSTRAINT uq_users_email    UNIQUE (tenant_id, email)
);
GO
CREATE INDEX ix_users_tenant ON dbo.users (tenant_id);
GO

CREATE TABLE dbo.roles (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_roles PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_roles_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_roles_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(80)     NOT NULL,
    description NVARCHAR(200)    NULL,
    is_system   BIT              NOT NULL CONSTRAINT df_roles_system DEFAULT (0), -- seeded defaults
    created_at  DATETIME2        NOT NULL CONSTRAINT df_roles_created DEFAULT SYSUTCDATETIME(),
    row_version ROWVERSION,
    CONSTRAINT uq_roles_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_roles_tenant ON dbo.roles (tenant_id);
GO

CREATE TABLE dbo.role_permissions (
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_rp_tenant REFERENCES dbo.tenants(id),
    role_id       BIGINT           NOT NULL CONSTRAINT fk_rp_role REFERENCES dbo.roles(id),
    permission_id INT              NOT NULL CONSTRAINT fk_rp_perm REFERENCES dbo.permissions(id),
    CONSTRAINT pk_role_permissions PRIMARY KEY (role_id, permission_id)
);
GO

CREATE TABLE dbo.user_roles (
    tenant_id UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ur_tenant REFERENCES dbo.tenants(id),
    user_id   BIGINT           NOT NULL CONSTRAINT fk_ur_user REFERENCES dbo.users(id),
    role_id   BIGINT           NOT NULL CONSTRAINT fk_ur_role REFERENCES dbo.roles(id),
    CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id)
);
GO

/* ------------------------------------------------------------------
 * Multi-currency (FX rates are tenant-scoped; see plan §4.4)
 * ------------------------------------------------------------------ */
CREATE TABLE dbo.exchange_rates (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_fx PRIMARY KEY,
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_fx_tenant REFERENCES dbo.tenants(id),
    from_ccy     CHAR(3)          NOT NULL CONSTRAINT fk_fx_from REFERENCES dbo.currencies(code),
    to_ccy       CHAR(3)          NOT NULL CONSTRAINT fk_fx_to   REFERENCES dbo.currencies(code),
    rate         DECIMAL(19,8)    NOT NULL,
    valid_from   DATE             NOT NULL,
    source       NVARCHAR(40)     NULL,
    created_at   DATETIME2        NOT NULL CONSTRAINT df_fx_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_fx UNIQUE (tenant_id, from_ccy, to_ccy, valid_from)
);
GO
CREATE INDEX ix_fx_lookup ON dbo.exchange_rates (tenant_id, from_ccy, to_ccy, valid_from DESC);
GO

/* ------------------------------------------------------------------
 * Audit log & system settings
 * ------------------------------------------------------------------ */
CREATE TABLE dbo.audit_log (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_audit PRIMARY KEY,
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_audit_tenant REFERENCES dbo.tenants(id),
    actor_id    BIGINT           NULL,
    action      NVARCHAR(80)     NOT NULL,   -- CREATE|UPDATE|DELETE|LOGIN|...
    entity_type NVARCHAR(80)     NULL,
    entity_id   NVARCHAR(64)     NULL,
    detail      NVARCHAR(MAX)    NULL,        -- JSON diff
    occurred_at DATETIME2        NOT NULL CONSTRAINT df_audit_at DEFAULT SYSUTCDATETIME()
);
GO
CREATE INDEX ix_audit_tenant_time ON dbo.audit_log (tenant_id, occurred_at DESC);
GO

CREATE TABLE dbo.system_settings (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_sys PRIMARY KEY,
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sys_tenant REFERENCES dbo.tenants(id),
    [key]       NVARCHAR(120)    NOT NULL,
    value       NVARCHAR(MAX)    NULL,
    updated_at  DATETIME2        NOT NULL CONSTRAINT df_sys_updated DEFAULT SYSUTCDATETIME(),
    row_version ROWVERSION,
    CONSTRAINT uq_sys UNIQUE (tenant_id, [key])
);
GO

/* ------------------------------------------------------------------
 * Seed: common currencies + baseline permission catalog
 * ------------------------------------------------------------------ */
INSERT INTO dbo.currencies (code, name, symbol, decimal_places) VALUES
  ('USD','US Dollar','$',2), ('EUR','Euro',N'€',2), ('GBP','Pound Sterling',N'£',2),
  ('PKR','Pakistani Rupee',N'₨',2), ('AED','UAE Dirham',N'د.إ',2);
GO

INSERT INTO dbo.permissions (code, description) VALUES
  ('tenant.manage','Manage organization settings & billing'),
  ('user.manage','Invite and manage users & roles'),
  ('catalog.read','View catalog'),      ('catalog.write','Create/edit catalog'),
  ('inventory.read','View inventory'),   ('inventory.write','Adjust inventory'),
  ('sales.read','View sales'),           ('sales.write','Create/edit sales'),
  ('finance.read','View finance'),       ('finance.write','Post finance entries');
GO

GO

/* ========================================================================
   V002__row_level_security.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V002__row_level_security.sql
 * Tenant isolation enforced in the database (plan §2).
 *
 * The app sets the current tenant on every connection checkout:
 *     EXEC sp_set_session_context @key=N'tenant_id', @value=<guid>, @read_only=1;
 * The predicate below then filters/blocks every row that doesn't match.
 *
 * A privileged "system" principal (used only for provisioning/admin jobs) is
 * exempted so it can create tenants and seed defaults across the boundary.
 * Set its name via the SQLCMD variable :setvar SYSTEM_PRINCIPAL, default 'erp_system'.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* Predicate: row is visible when its tenant matches SESSION_CONTEXT('tenant_id'),
 * OR the connection runs as the system principal (bypass for provisioning). */
CREATE FUNCTION dbo.fn_tenant_predicate(@tenant_id UNIQUEIDENTIFIER)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS is_visible
    WHERE @tenant_id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)
       OR DATABASE_PRINCIPAL_ID('erp_system') = DATABASE_PRINCIPAL_ID();
GO

/* One policy covering every tenant-scoped table.
 * FILTER hides other tenants' rows on read; BLOCK stops writing rows for another
 * tenant. The tenants table is matched on its own id column. */
CREATE SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(id)        ON dbo.tenants,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(id)        ON dbo.tenants AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.subscriptions,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.subscriptions AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.users,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.users AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.roles,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.roles AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.role_permissions,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.role_permissions AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.user_roles,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.user_roles AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.exchange_rates,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.exchange_rates AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.audit_log,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.audit_log AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.system_settings,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.system_settings AFTER INSERT
    WITH (STATE = ON);
GO

/* NOTE: as new tenant-scoped tables are added in later migrations, extend this
 * policy with:
 *   ALTER SECURITY POLICY dbo.TenantSecurityPolicy
 *     ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.<table>,
 *     ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.<table> AFTER INSERT;
 */

GO

/* ========================================================================
   V003__catalog.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V003__catalog.sql
 * Phase 1 preview: the Catalog module tables, used by the backend's exemplar
 * vertical slice. Demonstrates the "add tables → extend the RLS policy" pattern.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.categories (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_categories PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_cat_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_cat_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    parent_id   BIGINT           NULL CONSTRAINT fk_cat_parent REFERENCES dbo.categories(id),
    is_active   BIT              NOT NULL CONSTRAINT df_cat_active DEFAULT (1),
    row_version ROWVERSION,
    CONSTRAINT uq_cat_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_cat_tenant ON dbo.categories (tenant_id);
GO

CREATE TABLE dbo.attribute_values (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_attrval PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_attr_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_attr_tenant REFERENCES dbo.tenants(id),
    attr_type   NVARCHAR(20)     NOT NULL,  -- 'Color' | 'Size'
    value       NVARCHAR(60)     NOT NULL,
    code        NVARCHAR(20)     NOT NULL,
    row_version ROWVERSION,
    CONSTRAINT uq_attr UNIQUE (tenant_id, attr_type, code)
);
GO
CREATE INDEX ix_attr_tenant ON dbo.attribute_values (tenant_id);
GO

CREATE TABLE dbo.products (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_products PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_prod_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_prod_tenant REFERENCES dbo.tenants(id),
    title        NVARCHAR(200)    NOT NULL,
    category_id  BIGINT           NULL CONSTRAINT fk_prod_cat REFERENCES dbo.categories(id),
    season       NVARCHAR(40)     NULL,
    status       NVARCHAR(20)     NOT NULL CONSTRAINT df_prod_status DEFAULT ('Draft'), -- Active|Draft|Discontinued
    created_at   DATETIME2        NOT NULL CONSTRAINT df_prod_created DEFAULT SYSUTCDATETIME(),
    created_by   BIGINT           NULL,
    updated_at   DATETIME2        NULL,
    updated_by   BIGINT           NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_prod_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_prod_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_prod_tenant ON dbo.products (tenant_id, status);
GO

CREATE TABLE dbo.product_variants (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_variants PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_var_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_var_tenant REFERENCES dbo.tenants(id),
    product_id    BIGINT           NOT NULL CONSTRAINT fk_var_product REFERENCES dbo.products(id),
    sku           NVARCHAR(64)     NOT NULL,
    color_id      BIGINT           NULL CONSTRAINT fk_var_color REFERENCES dbo.attribute_values(id),
    size_id       BIGINT           NULL CONSTRAINT fk_var_size  REFERENCES dbo.attribute_values(id),
    barcode       NVARCHAR(64)     NULL,
    price         DECIMAL(19,4)    NOT NULL,
    currency_code CHAR(3)          NOT NULL CONSTRAINT fk_var_ccy REFERENCES dbo.currencies(code),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_var_status DEFAULT ('Active'),
    row_version   ROWVERSION,
    CONSTRAINT uq_var_sku UNIQUE (tenant_id, sku),
    CONSTRAINT uq_var_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_var_product ON dbo.product_variants (tenant_id, product_id);
GO

/* Extend tenant isolation to the new tables. */
ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.categories,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.categories AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.attribute_values,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.attribute_values AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.products,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.products AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.product_variants,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.product_variants AFTER INSERT;
GO

GO

/* ========================================================================
   V004__sales.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V004__sales.sql
 * Sales module — starting with Customers. (Orders/invoices follow later.)
 * Run in SSMS against Preduit-ERP after V001–V003.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.customers (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_customers PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_cust_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_cust_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(200)    NOT NULL,
    email       NVARCHAR(256)    NOT NULL,
    [type]      NVARCHAR(20)     NOT NULL CONSTRAINT df_cust_type DEFAULT ('Retail'), -- Wholesale|Retail
    region      NVARCHAR(80)     NULL,
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_cust_status DEFAULT ('Active'),
    created_at  DATETIME2        NOT NULL CONSTRAINT df_cust_created DEFAULT SYSUTCDATETIME(),
    created_by  BIGINT           NULL,
    updated_at  DATETIME2        NULL,
    updated_by  BIGINT           NULL,
    is_deleted  BIT              NOT NULL CONSTRAINT df_cust_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_cust_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_cust_tenant ON dbo.customers (tenant_id);
GO

/* Extend tenant isolation to the new table. */
ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.customers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.customers AFTER INSERT;
GO

GO

/* ========================================================================
   V005__sales_orders.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V005__sales_orders.sql
 * Sales orders (summary level; line items follow with a dedicated order-entry UI).
 * Run in SSMS against Preduit-ERP after V004.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.sales_orders (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_orders PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ord_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ord_tenant REFERENCES dbo.tenants(id),
    order_no      NVARCHAR(32)     NULL,
    customer_id   BIGINT           NULL CONSTRAINT fk_ord_customer REFERENCES dbo.customers(id),
    customer_name NVARCHAR(200)    NOT NULL,
    channel       NVARCHAR(20)     NOT NULL CONSTRAINT df_ord_channel DEFAULT ('Online'),
    item_count    INT              NOT NULL CONSTRAINT df_ord_items DEFAULT (0),
    total         DECIMAL(19,4)    NOT NULL CONSTRAINT df_ord_total DEFAULT (0),
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_ord_ccy DEFAULT ('EUR'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_ord_status DEFAULT ('New'),
    order_date    DATE             NOT NULL CONSTRAINT df_ord_date DEFAULT CAST(SYSUTCDATETIME() AS DATE),
    created_at    DATETIME2        NOT NULL CONSTRAINT df_ord_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_ord_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_ord_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ord_tenant ON dbo.sales_orders (tenant_id, order_date DESC);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_orders,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_orders AFTER INSERT;
GO

GO

/* ========================================================================
   V006__sales_invoices_returns.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V006__sales_invoices_returns.sql
 * Invoices and Returns (RMA). Run in SSMS against Preduit-ERP after V005.
 * ("sales_returns" avoids the reserved word RETURNS.)
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.invoices (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_invoices PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_inv_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_inv_tenant REFERENCES dbo.tenants(id),
    invoice_no    NVARCHAR(32)     NULL,
    customer_id   BIGINT           NULL CONSTRAINT fk_inv_customer REFERENCES dbo.customers(id),
    customer_name NVARCHAR(200)    NOT NULL,
    issued_date   DATE             NOT NULL CONSTRAINT df_inv_issued DEFAULT CAST(SYSUTCDATETIME() AS DATE),
    due_date      NVARCHAR(40)     NULL,
    amount        DECIMAL(19,4)    NOT NULL CONSTRAINT df_inv_amount DEFAULT (0),
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_inv_ccy DEFAULT ('EUR'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_inv_status DEFAULT ('Open'),
    created_at    DATETIME2        NOT NULL CONSTRAINT df_inv_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_inv_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_inv_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_inv_tenant ON dbo.invoices (tenant_id, issued_date DESC);
GO

CREATE TABLE dbo.sales_returns (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_returns PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ret_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ret_tenant REFERENCES dbo.tenants(id),
    rma_no        NVARCHAR(32)     NULL,
    order_ref     NVARCHAR(40)     NULL,
    customer_name NVARCHAR(200)    NOT NULL,
    reason        NVARCHAR(80)     NULL,
    refund        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ret_refund DEFAULT (0),
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_ret_ccy DEFAULT ('EUR'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_ret_status DEFAULT ('Inspecting'),
    created_at    DATETIME2        NOT NULL CONSTRAINT df_ret_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_ret_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_ret_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ret_tenant ON dbo.sales_returns (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoices,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoices AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_returns,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_returns AFTER INSERT;
GO

GO

/* ========================================================================
   V007__catalog_detail_fields.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V007__catalog_detail_fields.sql
 * Adds the fields the product DETAIL page shows, so the backend can feed the
 * frontend's original shape with real data:
 *   - product specs (composition, gauge, care, origin, HS code, weight)
 *   - per-variant on-hand quantity (drives the variant matrix cells)
 *   - color swatch hex + size sort order (attribute_values)
 * Run in SSMS against Preduit-ERP after V006.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.products ADD
    composition NVARCHAR(120) NULL,
    gauge       NVARCHAR(40)  NULL,
    care        NVARCHAR(120) NULL,
    origin      NVARCHAR(80)  NULL,
    hs_code     NVARCHAR(20)  NULL,
    weight      NVARCHAR(20)  NULL;
GO

ALTER TABLE dbo.product_variants
    ADD qty_on_hand INT NOT NULL CONSTRAINT df_var_qty DEFAULT (0);
GO

ALTER TABLE dbo.attribute_values ADD
    hex        NVARCHAR(9) NULL,
    sort_order INT NOT NULL CONSTRAINT df_attr_sort DEFAULT (0);
GO

GO

/* ========================================================================
   V008__inventory.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V008__inventory.sql
 * Inventory module: locations, per-variant/per-location stock, transfers,
 * reorder alerts. Shapes match the frontend Inventory tabs.
 * Run in SSMS against Preduit-ERP after V007.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.locations (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_locations PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_loc_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_loc_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    code        NVARCHAR(40)     NULL,
    [type]      NVARCHAR(20)     NOT NULL CONSTRAINT df_loc_type DEFAULT ('Warehouse'), -- Warehouse|Retail
    region      NVARCHAR(80)     NULL,
    capacity    INT              NULL,
    is_deleted  BIT              NOT NULL CONSTRAINT df_loc_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_loc_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_loc_tenant ON dbo.locations (tenant_id);
GO

CREATE TABLE dbo.stock_levels (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_stock PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_stk_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_stk_tenant REFERENCES dbo.tenants(id),
    variant_id  BIGINT           NOT NULL CONSTRAINT fk_stk_variant REFERENCES dbo.product_variants(id),
    location_id BIGINT           NOT NULL CONSTRAINT fk_stk_location REFERENCES dbo.locations(id),
    on_hand     INT              NOT NULL CONSTRAINT df_stk_onhand DEFAULT (0),
    reserved    INT              NOT NULL CONSTRAINT df_stk_reserved DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_stock UNIQUE (tenant_id, variant_id, location_id)
);
GO
CREATE INDEX ix_stk_tenant ON dbo.stock_levels (tenant_id);
GO

CREATE TABLE dbo.stock_transfers (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_transfers PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_trf_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_trf_tenant REFERENCES dbo.tenants(id),
    transfer_no      NVARCHAR(32)     NULL,
    from_location_id BIGINT           NULL CONSTRAINT fk_trf_from REFERENCES dbo.locations(id),
    to_location_id   BIGINT           NULL CONSTRAINT fk_trf_to   REFERENCES dbo.locations(id),
    units            INT              NOT NULL CONSTRAINT df_trf_units DEFAULT (0),
    status           NVARCHAR(20)     NOT NULL CONSTRAINT df_trf_status DEFAULT ('Draft'),
    eta              NVARCHAR(40)     NULL,
    is_deleted       BIT              NOT NULL CONSTRAINT df_trf_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_trf_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_trf_tenant ON dbo.stock_transfers (tenant_id);
GO

CREATE TABLE dbo.reorder_alerts (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_alerts PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_alr_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_alr_tenant REFERENCES dbo.tenants(id),
    variant_id    BIGINT           NULL CONSTRAINT fk_alr_variant REFERENCES dbo.product_variants(id),
    sku           NVARCHAR(64)     NOT NULL,
    available     INT              NOT NULL CONSTRAINT df_alr_avail DEFAULT (0),
    reorder_point INT              NOT NULL CONSTRAINT df_alr_rop DEFAULT (0),
    suggested     INT              NOT NULL CONSTRAINT df_alr_sugg DEFAULT (0),
    supplier      NVARCHAR(200)    NULL,
    severity      NVARCHAR(20)     NOT NULL CONSTRAINT df_alr_sev DEFAULT ('Low'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_alr_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_alr_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_alr_tenant ON dbo.reorder_alerts (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.locations,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.locations AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_levels,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_levels AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfers AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.reorder_alerts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.reorder_alerts AFTER INSERT;
GO

GO

/* ========================================================================
   V009__sales_detail_fields.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V009__sales_detail_fields.sql
 * Fields the Sales DETAIL pages show: customer contact, and order/invoice line
 * items. Run in SSMS against Preduit-ERP after V008.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.customers ADD
    phone   NVARCHAR(40)  NULL,
    address NVARCHAR(300) NULL;
GO

CREATE TABLE dbo.sales_order_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_order_lines PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ol_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ol_tenant REFERENCES dbo.tenants(id),
    order_id    BIGINT           NOT NULL CONSTRAINT fk_ol_order REFERENCES dbo.sales_orders(id),
    sku         NVARCHAR(64)     NULL,
    name        NVARCHAR(200)    NOT NULL,
    qty         INT              NOT NULL CONSTRAINT df_ol_qty DEFAULT (0),
    price       DECIMAL(19,4)    NOT NULL CONSTRAINT df_ol_price DEFAULT (0),
    line_total  DECIMAL(19,4)    NOT NULL CONSTRAINT df_ol_total DEFAULT (0)
);
GO
CREATE INDEX ix_ol_order ON dbo.sales_order_lines (tenant_id, order_id);
GO

CREATE TABLE dbo.invoice_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_invoice_lines PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_il_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_il_tenant REFERENCES dbo.tenants(id),
    invoice_id  BIGINT           NOT NULL CONSTRAINT fk_il_invoice REFERENCES dbo.invoices(id),
    sku         NVARCHAR(64)     NULL,
    name        NVARCHAR(200)    NOT NULL,
    qty         INT              NOT NULL CONSTRAINT df_il_qty DEFAULT (0),
    price       DECIMAL(19,4)    NOT NULL CONSTRAINT df_il_price DEFAULT (0),
    line_total  DECIMAL(19,4)    NOT NULL CONSTRAINT df_il_total DEFAULT (0)
);
GO
CREATE INDEX ix_il_invoice ON dbo.invoice_lines (tenant_id, invoice_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_order_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_order_lines AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoice_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoice_lines AFTER INSERT;
GO

GO

/* ========================================================================
   V010__procurement.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V010__procurement.sql
 * Procurement: suppliers (with scorecard metrics), purchase orders, goods
 * receipts. Shapes match the frontend Procurement tabs. Run after V009.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.suppliers (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_suppliers PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_sup_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sup_tenant REFERENCES dbo.tenants(id),
    name          NVARCHAR(200)    NOT NULL,
    region        NVARCHAR(80)     NULL,
    category      NVARCHAR(120)    NULL,      -- e.g. "Knitwear · Yarn"
    country_code  NVARCHAR(4)      NULL,      -- CN / PK / TR / PT
    lead_time     NVARCHAR(40)     NULL,      -- "45 days"
    on_time_pct   INT              NOT NULL CONSTRAINT df_sup_ontime DEFAULT (0),
    defect_rate   DECIMAL(5,2)     NULL,
    price_rating  DECIMAL(3,1)     NULL,
    score         DECIMAL(3,1)     NULL,
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_sup_status DEFAULT ('New'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_sup_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_sup_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_sup_tenant ON dbo.suppliers (tenant_id);
GO

CREATE TABLE dbo.purchase_orders (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pos PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_po_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_po_tenant REFERENCES dbo.tenants(id),
    po_no            NVARCHAR(32)     NULL,
    supplier_id      BIGINT           NULL CONSTRAINT fk_po_supplier REFERENCES dbo.suppliers(id),
    supplier_name    NVARCHAR(200)    NOT NULL,
    supplier_country NVARCHAR(4)      NULL,
    item_count       INT              NOT NULL CONSTRAINT df_po_items DEFAULT (0),
    total            DECIMAL(19,4)    NOT NULL CONSTRAINT df_po_total DEFAULT (0),
    currency_code    CHAR(3)          NOT NULL CONSTRAINT df_po_ccy DEFAULT ('EUR'),
    expected         NVARCHAR(40)     NULL,
    status           NVARCHAR(24)     NOT NULL CONSTRAINT df_po_status DEFAULT ('Pending approval'),
    is_deleted       BIT              NOT NULL CONSTRAINT df_po_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_po_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_po_tenant ON dbo.purchase_orders (tenant_id);
GO

CREATE TABLE dbo.goods_receipts (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_grn PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_grn_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_grn_tenant REFERENCES dbo.tenants(id),
    grn_no           NVARCHAR(32)     NULL,
    po_ref           NVARCHAR(32)     NULL,
    supplier_name    NVARCHAR(200)    NOT NULL,
    supplier_country NVARCHAR(4)      NULL,
    line_count       INT              NOT NULL CONSTRAINT df_grn_lines DEFAULT (0),
    received_count   INT              NOT NULL CONSTRAINT df_grn_recv DEFAULT (0),
    status           NVARCHAR(20)     NOT NULL CONSTRAINT df_grn_status DEFAULT ('Expected'),
    is_deleted       BIT              NOT NULL CONSTRAINT df_grn_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_grn_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_grn_tenant ON dbo.goods_receipts (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.suppliers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.suppliers AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_orders,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_orders AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipts AFTER INSERT;
GO

GO

/* ========================================================================
   V011__procurement_detail_fields.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V011__procurement_detail_fields.sql
 * Fields the Procurement DETAIL pages show: supplier contact, goods-receipt
 * header info, and receipt line items. Run after V010.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.suppliers ADD
    email   NVARCHAR(256) NULL,
    phone   NVARCHAR(40)  NULL,
    address NVARCHAR(300) NULL;
GO

ALTER TABLE dbo.goods_receipts ADD
    received_date NVARCHAR(40)  NULL,
    location      NVARCHAR(120) NULL;
GO

CREATE TABLE dbo.goods_receipt_lines (
    id         BIGINT IDENTITY  NOT NULL CONSTRAINT pk_grn_lines PRIMARY KEY,
    public_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_grnl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_grnl_tenant REFERENCES dbo.tenants(id),
    grn_id     BIGINT           NOT NULL CONSTRAINT fk_grnl_grn REFERENCES dbo.goods_receipts(id),
    name       NVARCHAR(200)    NOT NULL,
    sku        NVARCHAR(64)     NULL,
    ordered    INT              NOT NULL CONSTRAINT df_grnl_ord DEFAULT (0),
    received   INT              NOT NULL CONSTRAINT df_grnl_recv DEFAULT (0)
);
GO
CREATE INDEX ix_grnl_grn ON dbo.goods_receipt_lines (tenant_id, grn_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipt_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipt_lines AFTER INSERT;
GO

GO

/* ========================================================================
   V012__finance.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V012__finance.sql
 * Finance: chart of accounts, journal entries + lines (double-entry),
 * payments, and AR/AP aging snapshots. Shapes match the frontend Finance tabs.
 * Run after V011.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.chart_of_accounts (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_coa PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_coa_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_coa_tenant REFERENCES dbo.tenants(id),
    code        NVARCHAR(20)     NOT NULL,
    name        NVARCHAR(200)    NOT NULL,
    acct_type   NVARCHAR(20)     NOT NULL,  -- Asset|Liability|Equity|Income|Expense
    balance     DECIMAL(19,4)    NOT NULL CONSTRAINT df_coa_bal DEFAULT (0),
    is_deleted  BIT              NOT NULL CONSTRAINT df_coa_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_coa_code UNIQUE (tenant_id, code)
);
GO
CREATE INDEX ix_coa_tenant ON dbo.chart_of_accounts (tenant_id);
GO

CREATE TABLE dbo.journal_entries (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_je PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_je_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_je_tenant REFERENCES dbo.tenants(id),
    entry_no     NVARCHAR(32)     NOT NULL,
    entry_date   NVARCHAR(40)     NULL,
    memo         NVARCHAR(300)    NULL,
    total_debit  DECIMAL(19,4)    NOT NULL CONSTRAINT df_je_dr DEFAULT (0),
    total_credit DECIMAL(19,4)    NOT NULL CONSTRAINT df_je_cr DEFAULT (0),
    status       NVARCHAR(20)     NOT NULL CONSTRAINT df_je_status DEFAULT ('Draft'),
    source_note  NVARCHAR(MAX)    NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_je_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_je_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_je_tenant ON dbo.journal_entries (tenant_id);
GO

CREATE TABLE dbo.journal_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_jl PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_jl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_jl_tenant REFERENCES dbo.tenants(id),
    entry_id    BIGINT           NOT NULL CONSTRAINT fk_jl_entry REFERENCES dbo.journal_entries(id),
    account     NVARCHAR(160)    NOT NULL,
    description NVARCHAR(200)    NULL,
    debit       DECIMAL(19,4)    NOT NULL CONSTRAINT df_jl_dr DEFAULT (0),
    credit      DECIMAL(19,4)    NOT NULL CONSTRAINT df_jl_cr DEFAULT (0)
);
GO
CREATE INDEX ix_jl_entry ON dbo.journal_lines (tenant_id, entry_id);
GO

CREATE TABLE dbo.payments (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pmt PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pmt_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pmt_tenant REFERENCES dbo.tenants(id),
    payment_no    NVARCHAR(32)     NULL,
    pay_date      NVARCHAR(40)     NULL,
    party         NVARCHAR(200)    NOT NULL,
    allocated_to  NVARCHAR(60)     NULL,
    amount        DECIMAL(19,4)    NOT NULL CONSTRAINT df_pmt_amt DEFAULT (0),
    pay_type      NVARCHAR(20)     NOT NULL CONSTRAINT df_pmt_type DEFAULT ('Receipt'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_pmt_status DEFAULT ('Pending'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_pmt_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_pmt_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_pmt_tenant ON dbo.payments (tenant_id);
GO

CREATE TABLE dbo.ar_aging (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_arage PRIMARY KEY,
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_arage_tenant REFERENCES dbo.tenants(id),
    customer_name NVARCHAR(200)    NOT NULL,
    region        NVARCHAR(80)     NULL,
    current_amt   DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_cur DEFAULT (0),
    b1_30         DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_130 DEFAULT (0),
    b31_60        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_3160 DEFAULT (0),
    b61_90        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_6190 DEFAULT (0),
    b90_plus      DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_90 DEFAULT (0)
);
GO
CREATE INDEX ix_arage_tenant ON dbo.ar_aging (tenant_id);
GO

CREATE TABLE dbo.ap_aging (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_apage PRIMARY KEY,
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_apage_tenant REFERENCES dbo.tenants(id),
    supplier_name NVARCHAR(200)    NOT NULL,
    region        NVARCHAR(80)     NULL,
    current_amt   DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_cur DEFAULT (0),
    b1_30         DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_130 DEFAULT (0),
    b31_60        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_3160 DEFAULT (0),
    b61_90        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_6190 DEFAULT (0),
    b90_plus      DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_90 DEFAULT (0)
);
GO
CREATE INDEX ix_apage_tenant ON dbo.ap_aging (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.chart_of_accounts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.chart_of_accounts AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_entries,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_entries AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_lines AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.payments,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.payments AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ar_aging,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ar_aging AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ap_aging,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ap_aging AFTER INSERT;
GO

GO

/* ========================================================================
   V013__coa_detail_fields.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V013__coa_detail_fields.sql
 * Richer Chart of Accounts fields a business owner needs to maintain finances.
 * Run after V012.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.chart_of_accounts ADD
    subtype         NVARCHAR(40)  NULL,
    description     NVARCHAR(300) NULL,
    currency_code   CHAR(3)       NOT NULL CONSTRAINT df_coa_ccy DEFAULT ('EUR'),
    opening_balance DECIMAL(19,4) NOT NULL CONSTRAINT df_coa_open DEFAULT (0),
    tax_rate        DECIMAL(5,2)  NULL,
    parent_code     NVARCHAR(20)  NULL,
    is_active       BIT           NOT NULL CONSTRAINT df_coa_active DEFAULT (1);
GO

GO

/* ========================================================================
   V014__finance_payment_fields.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V014__finance_payment_fields.sql
 * Extra payment fields a business owner records (method, reference, notes).
 * Journal entries already have the fields needed to edit. Run after V013.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.payments ADD
    method    NVARCHAR(30)  NULL,   -- Bank transfer | Card | Cash | Cheque
    reference NVARCHAR(60)  NULL,
    notes     NVARCHAR(300) NULL;
GO

GO

/* ========================================================================
   V015__finance_aging_sources.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V015__finance_aging_sources.sql
 * Sources for LIVE aging: a real due date on invoices (AR) and a supplier-bills
 * / payables table (AP). AR/AP aging is now aggregated from these, not stored.
 * Run after V014.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.invoices ADD due_on DATE NULL;
GO

CREATE TABLE dbo.supplier_bills (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bills PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bill_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bill_tenant REFERENCES dbo.tenants(id),
    bill_no       NVARCHAR(32)     NULL,
    supplier_name NVARCHAR(200)    NOT NULL,
    po_ref        NVARCHAR(32)     NULL,
    amount        DECIMAL(19,4)    NOT NULL CONSTRAINT df_bill_amt DEFAULT (0),
    due_on        DATE             NULL,
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_bill_status DEFAULT ('Open'),  -- Open|Paid
    is_deleted    BIT              NOT NULL CONSTRAINT df_bill_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_bill_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bill_tenant ON dbo.supplier_bills (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.supplier_bills,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.supplier_bills AFTER INSERT;
GO

GO

/* ========================================================================
   V016__documents.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V016__documents.sql
 * Reusable document store: file metadata for uploads attached to any record,
 * with a module-prefixed unique doc id (e.g. PRC-000042). Run after V015.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.documents (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_documents PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_doc_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_doc_tenant REFERENCES dbo.tenants(id),
    doc_id       NVARCHAR(40)     NOT NULL,      -- module-prefixed, e.g. PRC-000042
    module       NVARCHAR(40)     NOT NULL,      -- owning module
    entity_type  NVARCHAR(60)     NULL,          -- e.g. goodsreceipt, shipment
    entity_ref   NVARCHAR(80)     NULL,          -- the record it's attached to (ref/public id)
    filename     NVARCHAR(260)    NOT NULL,
    content_type NVARCHAR(120)    NULL,
    size_bytes   BIGINT           NOT NULL CONSTRAINT df_doc_size DEFAULT (0),
    storage_path NVARCHAR(400)    NOT NULL,
    uploaded_by  BIGINT           NULL,
    created_at   DATETIME2        NOT NULL CONSTRAINT df_doc_created DEFAULT SYSUTCDATETIME(),
    is_deleted   BIT              NOT NULL CONSTRAINT df_doc_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_doc_docid UNIQUE (tenant_id, doc_id),
    CONSTRAINT uq_doc_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_doc_lookup ON dbo.documents (tenant_id, module, entity_ref);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.documents,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.documents AFTER INSERT;
GO

GO

/* ========================================================================
   V017__production.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V017__production.sql
 * Production: manufacturing orders (with stage/progress) + bill of materials.
 * Run after V016.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.production_orders (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_porders PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_po2_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_po2_tenant REFERENCES dbo.tenants(id),
    order_no    NVARCHAR(32)     NULL,
    style       NVARCHAR(200)    NOT NULL,
    factory     NVARCHAR(120)    NULL,
    qty         INT              NOT NULL CONSTRAINT df_po2_qty DEFAULT (0),
    stage       NVARCHAR(20)     NOT NULL CONSTRAINT df_po2_stage DEFAULT ('Cutting'),
    progress    INT              NOT NULL CONSTRAINT df_po2_prog DEFAULT (0),
    is_deleted  BIT              NOT NULL CONSTRAINT df_po2_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_po2_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_po2_tenant ON dbo.production_orders (tenant_id);
GO

CREATE TABLE dbo.bill_of_materials (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bom PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bom_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bom_tenant REFERENCES dbo.tenants(id),
    component    NVARCHAR(200)    NOT NULL,
    style        NVARCHAR(200)    NULL,
    material     NVARCHAR(80)     NULL,
    qty_per_unit NVARCHAR(40)     NULL,
    cost         DECIMAL(19,4)    NOT NULL CONSTRAINT df_bom_cost DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_bom_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_bom_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bom_tenant ON dbo.bill_of_materials (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_orders,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_orders AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bill_of_materials,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bill_of_materials AFTER INSERT;
GO

GO

/* ========================================================================
   V018__quality.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V018__quality.sql
 * Quality: inspections + defect types. Run after V017.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.inspections (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_insp PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_insp_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_insp_tenant REFERENCES dbo.tenants(id),
    inspection_no NVARCHAR(32)    NULL,
    order_ref    NVARCHAR(40)     NULL,
    stage        NVARCHAR(20)     NOT NULL CONSTRAINT df_insp_stage DEFAULT ('Final'),  -- Inline|Final
    aql          NVARCHAR(10)     NULL,
    defect_count INT              NOT NULL CONSTRAINT df_insp_def DEFAULT (0),
    result       NVARCHAR(20)     NOT NULL CONSTRAINT df_insp_res DEFAULT ('Pending'),  -- Pending|Pass|Fail
    inspector    NVARCHAR(120)    NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_insp_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_insp_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_insp_tenant ON dbo.inspections (tenant_id);
GO

CREATE TABLE dbo.defect_types (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_deftype PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_deft_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_deft_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    category    NVARCHAR(40)     NULL,   -- Stitching|Fabric|Trim
    severity    NVARCHAR(20)     NULL,   -- Major|Minor
    frequency   INT              NOT NULL CONSTRAINT df_deft_freq DEFAULT (0),  -- percent
    is_deleted  BIT              NOT NULL CONSTRAINT df_deft_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_deft_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_deft_tenant ON dbo.defect_types (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspections,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspections AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.defect_types,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.defect_types AFTER INSERT;
GO

GO

/* ========================================================================
   V019__shipments.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V019__shipments.sql
 * Shipments + carriers + shipment contents (line items). Run after V018.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.shipments (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_ship PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ship_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ship_tenant REFERENCES dbo.tenants(id),
    shipment_no NVARCHAR(32)     NULL,
    order_ref   NVARCHAR(40)     NULL,
    carrier     NVARCHAR(120)    NULL,
    destination NVARCHAR(160)    NULL,
    status      NVARCHAR(24)     NOT NULL CONSTRAINT df_ship_status DEFAULT ('Label created'),
    eta         NVARCHAR(40)     NULL,
    is_deleted  BIT              NOT NULL CONSTRAINT df_ship_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_ship_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ship_tenant ON dbo.shipments (tenant_id);
GO

CREATE TABLE dbo.carriers (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_carrier PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_car_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_car_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    service     NVARCHAR(80)     NULL,
    avg_transit NVARCHAR(40)     NULL,
    on_time_pct INT              NOT NULL CONSTRAINT df_car_ontime DEFAULT (0),
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_car_status DEFAULT ('Active'),
    is_deleted  BIT              NOT NULL CONSTRAINT df_car_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_car_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_car_tenant ON dbo.carriers (tenant_id);
GO

CREATE TABLE dbo.shipment_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_shipline PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_sl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sl_tenant REFERENCES dbo.tenants(id),
    shipment_id BIGINT           NOT NULL CONSTRAINT fk_sl_ship REFERENCES dbo.shipments(id),
    sku         NVARCHAR(64)     NULL,
    description NVARCHAR(200)    NOT NULL,
    qty         INT              NOT NULL CONSTRAINT df_sl_qty DEFAULT (0)
);
GO
CREATE INDEX ix_sl_ship ON dbo.shipment_lines (tenant_id, shipment_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipments,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipments AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.carriers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.carriers AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipment_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipment_lines AFTER INSERT;
GO

GO

/* ========================================================================
   V020__admin.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V020__admin.sql
 * Admin fields: user role/department, role scope, and an approval-rules table.
 * (Document Library reads the documents table; Audit Log reads audit_log.)
 * Run after V019.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.users ADD
    role        NVARCHAR(60)  NULL,
    department  NVARCHAR(120) NULL,
    last_active NVARCHAR(40)  NULL;
GO

ALTER TABLE dbo.roles ADD scope NVARCHAR(200) NULL;
GO

CREATE TABLE dbo.approval_rules (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_apprules PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ar_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ar_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(200)    NOT NULL,
    [condition] NVARCHAR(300)    NULL,
    approver    NVARCHAR(200)    NULL,
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_ar_status DEFAULT ('Active'),
    is_deleted  BIT              NOT NULL CONSTRAINT df_ar_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_ar_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ar_tenant ON dbo.approval_rules (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.approval_rules,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.approval_rules AFTER INSERT;
GO

GO

/* ========================================================================
   V021__purchase_order_lines.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V021__purchase_order_lines.sql
 * Line items for purchase orders (item, color, qty, price) so the New PO form
 * captures a real basket and the PO drill-down can show its lines. Run after V020.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.purchase_order_lines (
    id         BIGINT IDENTITY  NOT NULL CONSTRAINT pk_po_lines PRIMARY KEY,
    public_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pol_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pol_tenant REFERENCES dbo.tenants(id),
    po_id      BIGINT           NOT NULL CONSTRAINT fk_pol_po REFERENCES dbo.purchase_orders(id),
    name       NVARCHAR(200)    NOT NULL,
    color      NVARCHAR(60)     NULL,
    sku        NVARCHAR(64)     NULL,
    qty        INT              NOT NULL CONSTRAINT df_pol_qty DEFAULT (0),
    price      DECIMAL(19,4)    NOT NULL CONSTRAINT df_pol_price DEFAULT (0),
    line_total DECIMAL(19,4)    NOT NULL CONSTRAINT df_pol_total DEFAULT (0)
);
GO
CREATE INDEX ix_pol_po ON dbo.purchase_order_lines (tenant_id, po_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_order_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_order_lines AFTER INSERT;
GO
PRINT 'purchase_order_lines created.';
GO

GO

/* ========================================================================
   V022__po_line_size.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V022__po_line_size.sql
 * Adds a per-size breakdown to purchase order lines. Each (item, color, size)
 * with a quantity becomes its own line. Run after V021.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.purchase_order_lines ADD size NVARCHAR(60) NULL;
GO
PRINT 'purchase_order_lines.size added.';
GO

GO

/* ========================================================================
   V023__finance_ledgers.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V023__finance_ledgers.sql
 * Customer & Supplier ledger support: party code / payment terms / opening
 * balance, plus a credit_notes table for customer credits. Run after V022.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.customers ADD
    code            NVARCHAR(20)  NULL,
    terms           NVARCHAR(20)  NULL,
    opening_balance DECIMAL(19,4) NOT NULL CONSTRAINT df_cust_ob DEFAULT (0);
GO

ALTER TABLE dbo.suppliers ADD
    code            NVARCHAR(20)  NULL,
    terms           NVARCHAR(20)  NULL,
    opening_balance DECIMAL(19,4) NOT NULL CONSTRAINT df_sup_ob DEFAULT (0);
GO

CREATE TABLE dbo.credit_notes (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_cn PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_cn_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_cn_tenant REFERENCES dbo.tenants(id),
    cn_no        NVARCHAR(32)     NULL,
    customer_id  BIGINT           NULL,
    customer_name NVARCHAR(200)   NOT NULL,
    cn_date      DATE             NULL,
    amount       DECIMAL(19,4)    NOT NULL CONSTRAINT df_cn_amt DEFAULT (0),
    reason       NVARCHAR(200)    NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_cn_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_cn_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_cn_tenant ON dbo.credit_notes (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.credit_notes,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.credit_notes AFTER INSERT;
GO
PRINT 'Finance ledger fields + credit_notes created.';
GO

GO

/* ========================================================================
   V024__gl_engine.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V024__gl_engine.sql
 * General-ledger engine foundation: real posting dates, line→account links,
 * account normal side, and reversal linkage. Run after V023.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.journal_entries ADD
    entry_on       DATE       NULL,          -- real posting date (entry_date kept for display)
    posted_at      DATETIME2  NULL,
    period_id      BIGINT     NULL,
    reversed_of_id BIGINT     NULL;
GO

ALTER TABLE dbo.journal_lines ADD
    account_id BIGINT NULL CONSTRAINT fk_jl_account REFERENCES dbo.chart_of_accounts(id);
GO

ALTER TABLE dbo.chart_of_accounts ADD
    normal_side CHAR(1) NULL;               -- 'D' debit-normal, 'C' credit-normal
GO

/* Backfill normal side from account type. */
UPDATE dbo.chart_of_accounts
   SET normal_side = CASE WHEN acct_type IN ('Asset', 'Expense') THEN 'D' ELSE 'C' END
 WHERE normal_side IS NULL;
GO

/* Backfill line→account from the leading code in the label ('1100 · Name'). */
UPDATE jl
   SET account_id = a.id
  FROM dbo.journal_lines jl
  JOIN dbo.chart_of_accounts a
    ON a.tenant_id = jl.tenant_id
   AND jl.account LIKE a.code + ' %'
 WHERE jl.account_id IS NULL;
GO

/* Best-effort backfill of a real date from ISO-ish strings (others stay NULL). */
UPDATE dbo.journal_entries
   SET entry_on = TRY_CONVERT(DATE, entry_date)
 WHERE entry_on IS NULL;
GO

PRINT 'GL engine columns added.';
GO

GO

/* ========================================================================
   V025__gl_posting_flags.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V025__gl_posting_flags.sql
 * Links source documents to their general-ledger journal entry so each posts
 * exactly once. Run after V024.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.invoices ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_inv_posted DEFAULT (0);
GO
ALTER TABLE dbo.supplier_bills ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_bill_posted DEFAULT (0);
GO
ALTER TABLE dbo.payments ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_pmt_posted DEFAULT (0);
GO
ALTER TABLE dbo.credit_notes ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_cn_posted DEFAULT (0);
GO

PRINT 'GL posting flags added to source documents.';
GO

GO

/* ========================================================================
   V026__finance_controls.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V026__finance_controls.sql
 * Period close, budgeting and fixed assets. Run after V025.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.fiscal_periods (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_fp PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_fp_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_fp_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(40)     NOT NULL,        -- "Jul 2026"
    start_date  DATE             NOT NULL,
    end_date    DATE             NOT NULL,
    status      NVARCHAR(12)     NOT NULL CONSTRAINT df_fp_status DEFAULT ('Open'),  -- Open|Closed
    is_deleted  BIT              NOT NULL CONSTRAINT df_fp_deleted DEFAULT (0),
    CONSTRAINT uq_fp_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_fp_tenant ON dbo.fiscal_periods (tenant_id, start_date);
GO

CREATE TABLE dbo.budget_lines (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bl PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bl_tenant REFERENCES dbo.tenants(id),
    fiscal_year  INT              NOT NULL,
    account_code NVARCHAR(20)     NOT NULL,
    account_name NVARCHAR(200)    NULL,
    amount       DECIMAL(19,4)    NOT NULL CONSTRAINT df_bl_amt DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_bl_deleted DEFAULT (0),
    CONSTRAINT uq_bl_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bl_tenant ON dbo.budget_lines (tenant_id, fiscal_year);
GO

CREATE TABLE dbo.fixed_assets (
    id              BIGINT IDENTITY  NOT NULL CONSTRAINT pk_fa PRIMARY KEY,
    public_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_fa_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_fa_tenant REFERENCES dbo.tenants(id),
    asset_no        NVARCHAR(32)     NULL,
    name            NVARCHAR(200)    NOT NULL,
    category        NVARCHAR(80)     NULL,
    cost            DECIMAL(19,4)    NOT NULL CONSTRAINT df_fa_cost DEFAULT (0),
    salvage         DECIMAL(19,4)    NOT NULL CONSTRAINT df_fa_salv DEFAULT (0),
    life_months     INT              NOT NULL CONSTRAINT df_fa_life DEFAULT (36),
    in_service_date DATE             NULL,
    accumulated     DECIMAL(19,4)    NOT NULL CONSTRAINT df_fa_acc DEFAULT (0),
    status          NVARCHAR(12)     NOT NULL CONSTRAINT df_fa_status DEFAULT ('Active'),  -- Active|Disposed
    is_deleted      BIT              NOT NULL CONSTRAINT df_fa_deleted DEFAULT (0),
    CONSTRAINT uq_fa_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_fa_tenant ON dbo.fixed_assets (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.fiscal_periods,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.fiscal_periods AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.budget_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.budget_lines AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.fixed_assets,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.fixed_assets AFTER INSERT;
GO
PRINT 'Finance controls (periods, budgets, fixed assets) created.';
GO

GO

/* ========================================================================
   V027__ledger_links.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V027__ledger_links.sql
 * Link ledger documents to their party by ID (not name): supplier_id on bills,
 * party_type/party_id on payments, and backfill invoice/bill/CN/payment links
 * from the existing name matches. Run after V026.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.supplier_bills ADD supplier_id BIGINT NULL;
GO
ALTER TABLE dbo.payments ADD
    party_type NVARCHAR(20) NULL,   -- 'customer' | 'supplier'
    party_id   BIGINT       NULL;
GO

/* Backfill links from names. */
UPDATE inv SET customer_id = c.id
  FROM dbo.invoices inv
  JOIN dbo.customers c ON c.tenant_id = inv.tenant_id AND c.name = inv.customer_name
 WHERE inv.customer_id IS NULL;
GO

UPDATE b SET supplier_id = s.id
  FROM dbo.supplier_bills b
  JOIN dbo.suppliers s ON s.tenant_id = b.tenant_id AND s.name = b.supplier_name
 WHERE b.supplier_id IS NULL;
GO

UPDATE cn SET customer_id = c.id
  FROM dbo.credit_notes cn
  JOIN dbo.customers c ON c.tenant_id = cn.tenant_id AND c.name = cn.customer_name
 WHERE cn.customer_id IS NULL;
GO

UPDATE p SET party_type = 'customer', party_id = c.id
  FROM dbo.payments p
  JOIN dbo.customers c ON c.tenant_id = p.tenant_id AND c.name = p.party
 WHERE p.pay_type = 'Receipt' AND p.party_id IS NULL;
GO

UPDATE p SET party_type = 'supplier', party_id = s.id
  FROM dbo.payments p
  JOIN dbo.suppliers s ON s.tenant_id = p.tenant_id AND s.name = p.party
 WHERE p.pay_type <> 'Receipt' AND p.party_id IS NULL;
GO

PRINT 'Ledger party links added and backfilled.';
GO

GO

/* ========================================================================
   V028__bank_reconciliation.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V028__bank_reconciliation.sql
 * Bank accounts and imported statement lines for reconciliation. Run after V027.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.bank_accounts (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bank PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bank_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bank_tenant REFERENCES dbo.tenants(id),
    name          NVARCHAR(120)    NOT NULL,
    account_no    NVARCHAR(40)     NULL,
    gl_code       NVARCHAR(20)     NULL,          -- chart_of_accounts.code of the cash account
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_bank_ccy DEFAULT ('EUR'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_bank_deleted DEFAULT (0),
    CONSTRAINT uq_bank_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bank_tenant ON dbo.bank_accounts (tenant_id);
GO

CREATE TABLE dbo.bank_transactions (
    id                BIGINT IDENTITY  NOT NULL CONSTRAINT pk_banktxn PRIMARY KEY,
    public_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bt_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bt_tenant REFERENCES dbo.tenants(id),
    bank_account_id   BIGINT           NOT NULL,
    txn_date          DATE             NULL,
    description       NVARCHAR(200)    NULL,
    amount            DECIMAL(19,4)    NOT NULL CONSTRAINT df_bt_amt DEFAULT (0),  -- + deposit, - withdrawal
    matched_payment_id BIGINT          NULL,
    status            NVARCHAR(12)     NOT NULL CONSTRAINT df_bt_status DEFAULT ('Unmatched'), -- Unmatched|Matched|Reconciled
    is_deleted        BIT              NOT NULL CONSTRAINT df_bt_deleted DEFAULT (0),
    CONSTRAINT uq_bt_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bt_acct ON dbo.bank_transactions (tenant_id, bank_account_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_accounts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_accounts AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_transactions,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_transactions AFTER INSERT;
GO
PRINT 'Bank reconciliation tables created.';
GO

GO

/* ========================================================================
   V029__renumber_skus.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V029__renumber_skus.sql
 * Renumber existing product variant SKUs to the uniform 6-digit format
 * SKU-000001 … per tenant (ordered by id). New SKUs continue the sequence.
 * Run after V028. (Order/receipt line SKUs are historical snapshots and are
 * intentionally left as-is; reorder alerts are re-pointed to the new SKUs.)
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

;WITH v AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY id) AS rn
      FROM dbo.product_variants
)
UPDATE pv
   SET sku = 'SKU-' + RIGHT('000000' + CAST(v.rn AS VARCHAR(6)), 6)
  FROM dbo.product_variants pv
  JOIN v ON v.id = pv.id;
GO

/* Keep reorder alerts pointed at their variant's new SKU. */
UPDATE ra
   SET sku = pv.sku
  FROM dbo.reorder_alerts ra
  JOIN dbo.product_variants pv ON pv.id = ra.variant_id;
GO

PRINT 'Variant SKUs renumbered to SKU-000000 format.';
GO

GO

/* ========================================================================
   V030__variant_price_types.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V030__variant_price_types.sql
 * Three price types per SKU: retail, wholesale, online. Existing `price` is
 * kept as the base (= retail) for back-compat. Run after V029.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.product_variants ADD
    retail_price    DECIMAL(19,4) NULL,
    wholesale_price DECIMAL(19,4) NULL,
    online_price    DECIMAL(19,4) NULL;
GO

/* Backfill all three from the existing single price. */
UPDATE dbo.product_variants
   SET retail_price    = COALESCE(retail_price, price),
       wholesale_price = COALESCE(wholesale_price, price),
       online_price    = COALESCE(online_price, price)
 WHERE retail_price IS NULL OR wholesale_price IS NULL OR online_price IS NULL;
GO

PRINT 'Variant price types (retail/wholesale/online) added.';
GO

GO

/* ========================================================================
   V031__production_stages.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V031__production_stages.sql
 * Per-order production stage timeline (Trims → Lining → Cutting → Sewing →
 * Finishing → Packed). Created when production is started. Run after V030.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.production_stages (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pstages PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ps_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ps_tenant REFERENCES dbo.tenants(id),
    order_id      BIGINT           NOT NULL CONSTRAINT fk_ps_order REFERENCES dbo.production_orders(id),
    seq           INT              NOT NULL,
    name          NVARCHAR(40)     NOT NULL,
    duration_days INT              NOT NULL CONSTRAINT df_ps_dur DEFAULT (0),
    status        NVARCHAR(16)     NOT NULL CONSTRAINT df_ps_status DEFAULT ('Pending'),  -- Pending|In Progress|Completed
    start_on      DATE             NULL,
    end_on        DATE             NULL,
    worker        NVARCHAR(120)    NULL,
    notes         NVARCHAR(400)    NULL,
    is_deleted    BIT              NOT NULL CONSTRAINT df_ps_deleted DEFAULT (0),
    CONSTRAINT uq_ps_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ps_order ON dbo.production_stages (tenant_id, order_id, seq);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_stages,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_stages AFTER INSERT;
GO
PRINT 'production_stages created.';
GO

GO

/* ========================================================================
   V032__ai_insights.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V032__ai_insights.sql
 * AI Insights store. The ERP backend converses with the external Forcaster
 * forecasting engine and materialises its responses into these tenant-scoped
 * tables, so the AI Insights screens read/write through the ERP like every
 * other module (no direct browser→engine calls). Run after V031.
 *
 *  - ai_snapshot     one row per (tenant, kind, scope) holding the engine's
 *                    JSON payload (kind = dashboard|products|projections|
 *                    projection_detail|budget|recommendations|validation|
 *                    customers|customer_detail|audit|accuracy; scope = scenario
 *                    or reference/customer key, '' when not applicable).
 *  - ai_sync_state   last sync outcome per tenant.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.ai_snapshot (
    id         BIGINT IDENTITY  NOT NULL CONSTRAINT pk_aisnap PRIMARY KEY,
    public_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_aisnap_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_aisnap_tenant REFERENCES dbo.tenants(id),
    kind       NVARCHAR(40)     NOT NULL,
    scope      NVARCHAR(200)    NOT NULL CONSTRAINT df_aisnap_scope DEFAULT (''),
    data       NVARCHAR(MAX)    NOT NULL,
    synced_at  DATETIME2        NOT NULL CONSTRAINT df_aisnap_synced DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT uq_aisnap_public UNIQUE (public_id)
);
GO
CREATE UNIQUE INDEX ux_aisnap_key ON dbo.ai_snapshot (tenant_id, kind, scope);
GO

CREATE TABLE dbo.ai_sync_state (
    id             BIGINT IDENTITY  NOT NULL CONSTRAINT pk_aisync PRIMARY KEY,
    public_id      UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_aisync_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id      UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_aisync_tenant REFERENCES dbo.tenants(id),
    last_synced_at DATETIME2        NULL,
    status         NVARCHAR(20)     NOT NULL CONSTRAINT df_aisync_status DEFAULT ('idle'),  -- idle|ok|error
    message        NVARCHAR(400)    NULL,
    CONSTRAINT uq_aisync_public UNIQUE (public_id),
    CONSTRAINT uq_aisync_tenant UNIQUE (tenant_id)
);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ai_snapshot,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ai_snapshot AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ai_sync_state,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ai_sync_state AFTER INSERT;
GO
PRINT 'ai_snapshot + ai_sync_state created.';
GO

GO

/* ========================================================================
   V033__fx_gain_loss.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V033__fx_gain_loss.sql  (NO-OP)
 *
 * The transactional FX gain/loss module was removed at the client's request.
 * The FX booking columns and fx_revaluations table this migration originally
 * created are no longer needed. Exchange-rate sync (Frankfurter/ECB) is retained
 * and uses the existing dbo.exchange_rates table from V001, so nothing to create.
 *
 * Kept as a no-op to preserve migration numbering. Safe to run on databases
 * where the earlier version was already applied (it simply does nothing here).
 * ==========================================================================*/
PRINT 'V033 is a no-op (FX gain/loss module removed; rate sync uses V001 exchange_rates).';
GO

GO

/* ========================================================================
   V034__po_invoices.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V034__po_invoices.sql
 * Commercial invoices generated against a purchase order (procurement Invoices
 * tab). The full, editable invoice document is stored as JSON in `data`; a few
 * scalar columns are kept for the list view. Run after V033.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.po_invoices (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_poinv PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_poinv_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_poinv_tenant REFERENCES dbo.tenants(id),
    invoice_no    NVARCHAR(40)     NULL,
    po_no         NVARCHAR(40)     NULL,
    supplier_name NVARCHAR(200)    NULL,
    currency_code CHAR(3)          NULL,
    total         DECIMAL(19,4)    NOT NULL CONSTRAINT df_poinv_total DEFAULT (0),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_poinv_status DEFAULT ('Draft'),
    data          NVARCHAR(MAX)    NOT NULL,
    created_at    DATETIME2        NOT NULL CONSTRAINT df_poinv_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_poinv_deleted DEFAULT (0),
    CONSTRAINT uq_poinv_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_poinv_list ON dbo.po_invoices (tenant_id, is_deleted, id DESC);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.po_invoices,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.po_invoices AFTER INSERT;
GO
PRINT 'po_invoices created.';
GO

GO

/* ========================================================================
   V035__product_image.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V035__product_image.sql
 * Product image URL — shown on the product, and pulled onto the commercial
 * invoice (per article) generated from a PO. Run after V034.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

-- NVARCHAR(MAX): holds either a URL or an uploaded image as a data URL.
ALTER TABLE dbo.products ADD image_url NVARCHAR(MAX) NULL;
GO
PRINT 'products.image_url added.';
GO

GO

/* ========================================================================
   V036__product_image_widen.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V036__product_image_widen.sql
 * Ensure products.image_url can hold an uploaded image (data URL), not just a
 * short link. Earlier V035 may have created it as NVARCHAR(1000); widen to MAX.
 * Idempotent: safe if the column is already NVARCHAR(MAX). Run after V035.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.products ALTER COLUMN image_url NVARCHAR(MAX) NULL;
GO
PRINT 'products.image_url widened to NVARCHAR(MAX).';
GO

GO

/* ========================================================================
   V037__sales_invoices.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V037__sales_invoices.sql
 * Commercial / retail invoices generated against a sales order (Sales & Orders
 * "Invoices" tab). Mirrors V034 po_invoices: the full, editable invoice document
 * is stored as JSON in `data`; a few scalar columns are kept for the list view.
 * `invoice_type` records the template used — Retail | Online | Wholesale
 * (Retail and Online share the flat receipt layout; Wholesale uses the matrix
 * layout). Run after V036.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.sales_invoices (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_sinv PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_sinv_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sinv_tenant REFERENCES dbo.tenants(id),
    invoice_no    NVARCHAR(40)     NULL,
    order_no      NVARCHAR(40)     NULL,
    customer_name NVARCHAR(200)    NULL,
    invoice_type  NVARCHAR(20)     NOT NULL CONSTRAINT df_sinv_type DEFAULT ('Retail'),
    currency_code CHAR(3)          NULL,
    total         DECIMAL(19,4)    NOT NULL CONSTRAINT df_sinv_total DEFAULT (0),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_sinv_status DEFAULT ('Draft'),
    data          NVARCHAR(MAX)    NOT NULL,
    created_at    DATETIME2        NOT NULL CONSTRAINT df_sinv_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_sinv_deleted DEFAULT (0),
    CONSTRAINT uq_sinv_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_sinv_list ON dbo.sales_invoices (tenant_id, is_deleted, id DESC);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_invoices,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_invoices AFTER INSERT;
GO
PRINT 'sales_invoices created.';
GO

GO

/* ========================================================================
   V038__sales_order_line_variants.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V038__sales_order_line_variants.sql
 * Adds colour + per-size breakdown to sales order lines, mirroring
 * purchase_order_lines. Each (item, colour, size) with a quantity becomes its
 * own line — the New Order form now captures articles the same way the New PO
 * form does. Run after V037.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.sales_order_lines ADD color NVARCHAR(60) NULL;
GO
ALTER TABLE dbo.sales_order_lines ADD size NVARCHAR(60) NULL;
GO
PRINT 'sales_order_lines.color + size added.';
GO

GO

/* ========================================================================
   V039__auth.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V039__auth.sql
 * Self-managed authentication: password credentials, activation, and a
 * platform-admin (Super Admin) flag on users. Roles are stored in users.role
 * (already present); the role → permission mapping lives in the app.
 * Run after V038.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.users ADD
    password_hash     NVARCHAR(255) NULL,
    is_active         BIT           NOT NULL CONSTRAINT df_users_active     DEFAULT (1),
    is_platform_admin BIT           NOT NULL CONSTRAINT df_users_platadmin  DEFAULT (0),
    last_login        DATETIME2     NULL;
GO

-- Fast lookup by email at login (uniqueness enforced in the app to avoid
-- failing the migration on any pre-existing duplicate/seed rows).
CREATE INDEX ix_users_email ON dbo.users (email);
GO
PRINT 'users auth columns added.';
GO

GO

/* ========================================================================
   V040__auth_flows.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V040__auth_flows.sql
 * Auth flows layered on top of V039__auth.sql:
 *   - email verification codes (6-digit OTP at sign-up)
 *   - password reset tokens (single-use, time-boxed)
 *   - team invitations (owner/admin invites a teammate into their company)
 * Plus an email_verified flag on users. All three tables are tenant-scoped
 * and join the existing TenantSecurityPolicy. Run after V039.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.users ADD
    email_verified BIT NOT NULL CONSTRAINT df_users_emailverified DEFAULT (0);
GO

-- 6-digit codes sent at sign-up; hashed at rest, throttled by attempt count.
CREATE TABLE dbo.email_verifications (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_emailver PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_emailver_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_emailver_tenant REFERENCES dbo.tenants(id),
    user_id     BIGINT           NOT NULL CONSTRAINT fk_emailver_user   REFERENCES dbo.users(id),
    code_hash   NVARCHAR(128)    NOT NULL,
    attempts    INT              NOT NULL CONSTRAINT df_emailver_att DEFAULT (0),
    expires_at  DATETIME2        NOT NULL,
    consumed_at DATETIME2        NULL,
    created_at  DATETIME2        NOT NULL CONSTRAINT df_emailver_created DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT uq_emailver_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_emailver_user ON dbo.email_verifications (user_id, consumed_at);
GO

-- Single-use password-reset tokens (opaque random, sha256 at rest).
CREATE TABLE dbo.password_resets (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pwreset PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pwreset_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pwreset_tenant REFERENCES dbo.tenants(id),
    user_id     BIGINT           NOT NULL CONSTRAINT fk_pwreset_user   REFERENCES dbo.users(id),
    token_hash  NVARCHAR(128)    NOT NULL,
    expires_at  DATETIME2        NOT NULL,
    consumed_at DATETIME2        NULL,
    created_at  DATETIME2        NOT NULL CONSTRAINT df_pwreset_created DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT uq_pwreset_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_pwreset_token ON dbo.password_resets (token_hash);
GO

-- Team invitations. email+role captured by an owner/admin; accepted via token.
CREATE TABLE dbo.invitations (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_invite PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_invite_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_invite_tenant REFERENCES dbo.tenants(id),
    email       NVARCHAR(256)    NOT NULL,
    role        NVARCHAR(60)     NOT NULL,
    token_hash  NVARCHAR(128)    NOT NULL,
    invited_by  BIGINT           NULL CONSTRAINT fk_invite_by REFERENCES dbo.users(id),
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_invite_status DEFAULT ('pending'),  -- pending|accepted|revoked
    expires_at  DATETIME2        NOT NULL,
    accepted_at DATETIME2        NULL,
    created_at  DATETIME2        NOT NULL CONSTRAINT df_invite_created DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT uq_invite_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_invite_token  ON dbo.invitations (token_hash);
CREATE INDEX ix_invite_tenant ON dbo.invitations (tenant_id, status);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.email_verifications,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.email_verifications AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.password_resets,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.password_resets AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invitations,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invitations AFTER INSERT;
GO
PRINT 'email_verifications + password_resets + invitations created.';
GO

GO

/* ========================================================================
   V041__auth_hardening.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V041__auth_hardening.sql
 * Auth hardening (AUTH-E):
 *   - failed-login counter + lockout timestamp on users
 *   - refresh_tokens store for rotation & reuse detection (one row per issued
 *     refresh token, identified by its jti; rotated tokens are revoked and
 *     linked via replaced_by).
 * Tenant-scoped table joins the existing TenantSecurityPolicy. Run after V040.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.users ADD
    failed_logins INT       NOT NULL CONSTRAINT df_users_failed DEFAULT (0),
    locked_until  DATETIME2 NULL;
GO

CREATE TABLE dbo.refresh_tokens (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_rtok PRIMARY KEY,
    jti         UNIQUEIDENTIFIER NOT NULL,
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_rtok_tenant REFERENCES dbo.tenants(id),
    user_id     BIGINT           NOT NULL CONSTRAINT fk_rtok_user   REFERENCES dbo.users(id),
    expires_at  DATETIME2        NOT NULL,
    revoked_at  DATETIME2        NULL,
    replaced_by UNIQUEIDENTIFIER NULL,
    created_at  DATETIME2        NOT NULL CONSTRAINT df_rtok_created DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT uq_rtok_jti UNIQUE (jti)
);
GO
CREATE INDEX ix_rtok_user ON dbo.refresh_tokens (user_id, revoked_at);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.refresh_tokens,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.refresh_tokens AFTER INSERT;
GO
PRINT 'users lockout columns + refresh_tokens created.';
GO

GO

/* ========================================================================
   V042__rls_bypass_flag.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V042__rls_bypass_flag.sql
 * Local-dev / provisioning fix.
 *
 * The tenant predicate only exempts the `erp_system` DB principal. On LocalDB
 * with Windows auth the backend connects as dbo (NOT erp_system), so the
 * "system" connection was still filtered by RLS — breaking cross-tenant
 * provisioning reads (find-user-by-email at login/verify/reset, company list).
 *
 * This adds a second, controlled bypass: a connection may opt out of RLS by
 * setting SESSION_CONTEXT('rls_bypass') = 1. ONLY the backend's system session
 * sets it (see core/database.system_session); the RLS-scoped app session never
 * does, and its pool checkout clears it defensively. Every tenant-scoped read on
 * the system session still filters by tenant_id explicitly or uses unique keys,
 * so this cannot leak data across tenants.
 *
 * The security policy is dropped and rebuilt dynamically over every dbo table
 * that has a tenant_id column (plus dbo.tenants keyed on id), so it also
 * re-covers tables added since V002 in one shot. Run after V041.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
    DROP SECURITY POLICY dbo.TenantSecurityPolicy;
GO

CREATE OR ALTER FUNCTION dbo.fn_tenant_predicate(@tenant_id UNIQUEIDENTIFIER)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS is_visible
    WHERE @tenant_id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)
       OR DATABASE_PRINCIPAL_ID('erp_system') = DATABASE_PRINCIPAL_ID()
       OR CAST(SESSION_CONTEXT(N'rls_bypass') AS BIT) = 1;
GO

DECLARE @preds NVARCHAR(MAX);
SELECT @preds = STRING_AGG(pred, ',' + CHAR(10))
FROM (
    SELECT N'    ADD FILTER PREDICATE dbo.fn_tenant_predicate(id) ON dbo.tenants' AS pred
    UNION ALL
    SELECT N'    ADD BLOCK PREDICATE dbo.fn_tenant_predicate(id) ON dbo.tenants AFTER INSERT'
    UNION ALL
    SELECT N'    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.' + QUOTENAME(t.name)
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id AND c.name = 'tenant_id'
    WHERE t.schema_id = SCHEMA_ID('dbo')
    UNION ALL
    SELECT N'    ADD BLOCK PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.' + QUOTENAME(t.name) + N' AFTER INSERT'
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id AND c.name = 'tenant_id'
    WHERE t.schema_id = SCHEMA_ID('dbo')
) x;

DECLARE @sql NVARCHAR(MAX) =
    N'CREATE SECURITY POLICY dbo.TenantSecurityPolicy' + CHAR(10) + @preds + CHAR(10) + N'    WITH (STATE = ON);';
EXEC sys.sp_executesql @sql;
GO
PRINT 'RLS bypass flag added; TenantSecurityPolicy rebuilt.';
GO

GO

/* ========================================================================
   V043__rls_policy_rebuild_fix.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V043__rls_policy_rebuild_fix.sql
 * Fixes V042: STRING_AGG capped at 8000 bytes, so the CREATE SECURITY POLICY
 * step failed and the policy was left DROPPED (RLS temporarily OFF). This
 * re-applies the bypass-aware predicate and rebuilds the policy, casting the
 * aggregate to NVARCHAR(MAX) so the full statement is emitted. Idempotent —
 * safe to run whether or not the policy currently exists. Run after V042.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
    DROP SECURITY POLICY dbo.TenantSecurityPolicy;
GO

CREATE OR ALTER FUNCTION dbo.fn_tenant_predicate(@tenant_id UNIQUEIDENTIFIER)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS is_visible
    WHERE @tenant_id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)
       OR DATABASE_PRINCIPAL_ID('erp_system') = DATABASE_PRINCIPAL_ID()
       OR CAST(SESSION_CONTEXT(N'rls_bypass') AS BIT) = 1;
GO

DECLARE @preds NVARCHAR(MAX);
SELECT @preds = STRING_AGG(CONVERT(NVARCHAR(MAX), pred), ',' + CHAR(10))
FROM (
    SELECT N'    ADD FILTER PREDICATE dbo.fn_tenant_predicate(id) ON dbo.tenants' AS pred
    UNION ALL
    SELECT N'    ADD BLOCK PREDICATE dbo.fn_tenant_predicate(id) ON dbo.tenants AFTER INSERT'
    UNION ALL
    SELECT N'    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.' + QUOTENAME(t.name)
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id AND c.name = 'tenant_id'
    WHERE t.schema_id = SCHEMA_ID('dbo')
    UNION ALL
    SELECT N'    ADD BLOCK PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.' + QUOTENAME(t.name) + N' AFTER INSERT'
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id AND c.name = 'tenant_id'
    WHERE t.schema_id = SCHEMA_ID('dbo')
) x;

DECLARE @sql NVARCHAR(MAX) =
    N'CREATE SECURITY POLICY dbo.TenantSecurityPolicy' + CHAR(10) + @preds + CHAR(10) + N'    WITH (STATE = ON);';
EXEC sys.sp_executesql @sql;
GO

/* Verify: policy exists and is enabled (is_enabled must be 1). */
SELECT name, is_enabled FROM sys.security_policies WHERE name = 'TenantSecurityPolicy';
GO
PRINT 'TenantSecurityPolicy rebuilt with bypass-aware predicate.';
GO

GO

/* ========================================================================
   V044__tenant_setup.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V044__tenant_setup.sql
 * Company setup wizard: the owner completes business details after sign-up.
 *   - setup_complete gates the wizard (forced once, never again).
 *   - country / city / tax_registration captured on the "Outlets" step.
 * Enabled modules + team invites are stored elsewhere (system_settings /
 * invitations), so no columns are needed for those.
 * Existing tenants are backfilled as already set up. Run after V043.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.tenants ADD
    setup_complete   BIT           NOT NULL CONSTRAINT df_tenant_setup DEFAULT (0),
    country          NVARCHAR(80)  NULL,
    city             NVARCHAR(120) NULL,
    tax_registration NVARCHAR(60)  NULL;
GO

/* Backfill: tenants that already exist predate the wizard — treat as complete
   so their owners aren't bounced into setup. New sign-ups default to 0. */
UPDATE dbo.tenants SET setup_complete = 1;
GO
PRINT 'tenants setup columns added.';
GO

GO

/* ========================================================================
   V045__company_profile.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V045__company_profile.sql
 * Rich company profile (logo/cover, identity, industry, HQ, contact, socials,
 * legal) edited from the Company Profile page. Every field is its own typed
 * column on dbo.tenants — so it can be queried/joined and reused across the
 * app (HQ address on invoices, legal name on tax filings, contact on receipts).
 * name / country / city / tax_registration already exist (V044); this adds the
 * rest. Run after V044.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.tenants ADD
    about                 NVARCHAR(400) NULL,
    logo_doc_id           NVARCHAR(64)  NULL,   -- documents.public_id of the logo
    cover_doc_id          NVARCHAR(64)  NULL,   -- documents.public_id of the cover
    industry              NVARCHAR(60)  NULL,
    business_type         NVARCHAR(80)  NULL,
    sales_model           NVARCHAR(60)  NULL,
    founded               NVARCHAR(10)  NULL,
    street                NVARCHAR(200) NULL,
    [state]               NVARCHAR(80)  NULL,
    postal                NVARCHAR(20)  NULL,
    business_email        NVARCHAR(256) NULL,
    phone                 NVARCHAR(40)  NULL,
    support_line          NVARCHAR(40)  NULL,
    opening_hours         NVARCHAR(120) NULL,
    website               NVARCHAR(200) NULL,
    social_linkedin       NVARCHAR(120) NULL,
    social_instagram      NVARCHAR(120) NULL,
    social_facebook       NVARCHAR(120) NULL,
    social_x              NVARCHAR(120) NULL,
    legal_name            NVARCHAR(200) NULL,
    legal_same_as_company BIT           NOT NULL CONSTRAINT df_tenant_legalsame DEFAULT (0),
    registration_number   NVARCHAR(60)  NULL;
GO
PRINT 'tenants company-profile columns added.';
GO

GO

/* ========================================================================
   V046__production_sales_link.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V046__production_sales_link.sql
 * Link a production (work) order back to the sales order that spawned it, so a
 * single work order can represent the whole order and its detail can show the
 * order's line items. Run after V045.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.production_orders ADD sales_order_id BIGINT NULL;
GO
PRINT 'production_orders.sales_order_id added.';
GO

GO

/* ========================================================================
   V047__production_order_lines.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V047__production_order_lines.sql
 * Production order lines (per-style items within a manufacturing order) and
 * re-parent production_stages to lines so each item gets its own timeline.
 *
 * Self-healing / idempotent: step 0 cleans up any partial or failed prior run
 * (orphaned pol_* constraints, a half-created table, stray line_id values) so
 * the file can be re-run safely to a clean, correct end state. Run after V046.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* ---------------------------------------------------------------------------
 * 0. Recovery: undo any partial prior apply so creation can proceed cleanly.
 * --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_ps_line')
    ALTER TABLE dbo.production_stages DROP CONSTRAINT fk_ps_line;
GO

IF OBJECT_ID('dbo.production_order_lines', 'U') IS NOT NULL
    DROP TABLE dbo.production_order_lines;
GO

/* Any line_id set before is now orphaned (its line row is gone) — reset it so
   the backfill below can re-attach stages correctly. */
IF COL_LENGTH('dbo.production_stages', 'line_id') IS NOT NULL
    UPDATE dbo.production_stages SET line_id = NULL WHERE line_id IS NOT NULL;
GO

/* Drop any orphaned pol_* named constraints left behind by a failed CREATE
   (this is what caused the "object named df_pol_pub already exists" error). */
DECLARE @name SYSNAME, @tbl NVARCHAR(300), @sql NVARCHAR(MAX);
DECLARE c CURSOR LOCAL FAST_FORWARD FOR
    SELECT o.name, QUOTENAME(SCHEMA_NAME(t.schema_id)) + N'.' + QUOTENAME(t.name)
    FROM sys.objects o
    JOIN sys.tables t ON t.object_id = o.parent_object_id
    WHERE o.name IN ('pk_pol', 'df_pol_pub', 'fk_pol_tenant', 'fk_pol_order',
                     'fk_pol_sol', 'df_pol_qty', 'df_pol_deleted', 'uq_pol_public');
OPEN c;
FETCH NEXT FROM c INTO @name, @tbl;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'ALTER TABLE ' + @tbl + N' DROP CONSTRAINT ' + QUOTENAME(@name) + N';';
    EXEC sp_executesql @sql;
    FETCH NEXT FROM c INTO @name, @tbl;
END
CLOSE c;
DEALLOCATE c;
GO

/* ---------------------------------------------------------------------------
 * 1. Production order lines — one per style/item within a production order
 * --------------------------------------------------------------------------- */
CREATE TABLE dbo.production_order_lines (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pol PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pol_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pol_tenant REFERENCES dbo.tenants(id),
    order_id         BIGINT           NOT NULL CONSTRAINT fk_pol_order REFERENCES dbo.production_orders(id),
    sales_order_line_id BIGINT        NULL CONSTRAINT fk_pol_sol REFERENCES dbo.sales_order_lines(id),
    sku              NVARCHAR(64)     NULL,
    name             NVARCHAR(200)    NOT NULL,            -- style / product name
    color            NVARCHAR(60)     NULL,
    size             NVARCHAR(60)     NULL,
    qty              INT              NOT NULL CONSTRAINT df_pol_qty DEFAULT (0),
    is_deleted       BIT              NOT NULL CONSTRAINT df_pol_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_pol_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_pol_order ON dbo.production_order_lines (tenant_id, order_id);
GO
CREATE INDEX ix_pol_sol ON dbo.production_order_lines (tenant_id, sales_order_line_id);
GO

/* RLS predicates — add only if not already present on the table. */
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.production_order_lines'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_order_lines,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_order_lines AFTER INSERT;
GO

/* ---------------------------------------------------------------------------
 * 2. Re-parent production_stages: line_id + FK + indexes
 * --------------------------------------------------------------------------- */
IF COL_LENGTH('dbo.production_stages', 'line_id') IS NULL
    ALTER TABLE dbo.production_stages ADD line_id BIGINT NULL;
GO

ALTER TABLE dbo.production_stages
    ADD CONSTRAINT fk_ps_line FOREIGN KEY (line_id) REFERENCES dbo.production_order_lines(id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_ps_line'
               AND object_id = OBJECT_ID('dbo.production_stages'))
    CREATE INDEX ix_ps_line ON dbo.production_stages (tenant_id, line_id, seq);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'uq_ps_line_seq'
               AND object_id = OBJECT_ID('dbo.production_stages'))
    CREATE UNIQUE INDEX uq_ps_line_seq ON dbo.production_stages (line_id, seq)
        WHERE line_id IS NOT NULL;
GO

/* Stages now belong to a line — make order_id nullable (safe to re-run). */
ALTER TABLE dbo.production_stages ALTER COLUMN order_id BIGINT NULL;
GO

/* ---------------------------------------------------------------------------
 * 3. Backfill (set-based, idempotent): one line per existing order, then
 *    attach that order's stray stages to it.
 * --------------------------------------------------------------------------- */
INSERT INTO dbo.production_order_lines (tenant_id, order_id, name, qty)
SELECT po.tenant_id, po.id, po.style, po.qty
FROM dbo.production_orders po
WHERE po.is_deleted = 0
  AND NOT EXISTS (SELECT 1 FROM dbo.production_order_lines l WHERE l.order_id = po.id);
GO

UPDATE ps
SET line_id = x.line_id
FROM dbo.production_stages ps
CROSS APPLY (
    SELECT TOP 1 l.id AS line_id
    FROM dbo.production_order_lines l
    WHERE l.order_id = ps.order_id
    ORDER BY l.id
) x
WHERE ps.line_id IS NULL;
GO

PRINT 'production_order_lines created and production_stages re-parented (self-healing).';
GO

GO

/* ========================================================================
   V048__variant_supplier_price.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V048__variant_supplier_price.sql
 * Add a fourth price type per SKU: supplier price (the cost paid to suppliers,
 * used throughout procurement — PO creation through to the supplier invoice).
 * Backfilled from the existing base price. Idempotent. Run after V047.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.product_variants', 'supplier_price') IS NULL
    ALTER TABLE dbo.product_variants ADD supplier_price DECIMAL(19,4) NULL;
GO

/* Backfill from the existing single price so PO pricing has a sensible default. */
UPDATE dbo.product_variants
   SET supplier_price = COALESCE(supplier_price, price)
 WHERE supplier_price IS NULL;
GO

PRINT 'Variant supplier price added.';
GO

GO

/* ========================================================================
   V049__invoice_order_link_and_backfill.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V049__invoice_order_link_and_backfill.sql
 *  1) Add invoices.order_no so an AR receivable can reference its sales order.
 *  2) One-time backfill: create an AR invoice (receivable) for every generated
 *     invoice DOCUMENT (dbo.sales_invoices) that doesn't have one yet, so those
 *     documents finally land on the customer ledger. customer_id is resolved by
 *     name within the tenant; posted = 0 so the GL sync posts them on next load.
 *
 * The backfill spans every tenant, so the tenant RLS policy is toggled OFF for
 * the set-based INSERT (its BLOCK predicate would otherwise reject cross-tenant
 * rows) and switched back ON immediately after. Idempotent: re-running inserts
 * nothing new (guarded by NOT EXISTS on the computed invoice_no). Run after V048.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* 1. Link column: which sales order this receivable came from. */
IF COL_LENGTH('dbo.invoices', 'order_no') IS NULL
    ALTER TABLE dbo.invoices ADD order_no NVARCHAR(32) NULL;
GO

/* 2. Backfill AR invoices from generated invoice documents (all tenants). */
ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = OFF);
GO

INSERT INTO dbo.invoices (tenant_id, invoice_no, order_no, customer_id, customer_name,
                          issued_date, amount, currency_code, status, posted, is_deleted)
SELECT
    si.tenant_id,
    COALESCE(si.invoice_no, CONCAT('SI-', si.id)),
    si.order_no,
    c.id,
    si.customer_name,
    CAST(COALESCE(si.created_at, SYSUTCDATETIME()) AS DATE),
    si.total,
    ISNULL(si.currency_code, 'EUR'),
    'Open',
    0,
    0
FROM dbo.sales_invoices si
LEFT JOIN dbo.customers c
       ON c.tenant_id = si.tenant_id
      AND c.name = si.customer_name
      AND c.is_deleted = 0
WHERE si.is_deleted = 0
  AND ISNULL(si.total, 0) > 0
  AND NOT EXISTS (
        SELECT 1 FROM dbo.invoices inv
        WHERE inv.tenant_id = si.tenant_id
          AND inv.invoice_no = COALESCE(si.invoice_no, CONCAT('SI-', si.id))
  );
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = ON);
GO

PRINT 'V049: invoices.order_no added; AR receivables backfilled from sales_invoices documents.';
GO

GO

/* ========================================================================
   V050__ledger_entries.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V050__ledger_entries.sql
 *  1) ledger_entries: manual customer-ledger entries (a debit and/or credit with
 *     a description) created from the customer ledger "New entry" button.
 *  2) invoices.memo: editable description for auto/receivable invoice rows.
 * Idempotent. Run after V049.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* 1. Manual ledger entries. */
IF OBJECT_ID('dbo.ledger_entries', 'U') IS NULL
CREATE TABLE dbo.ledger_entries (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_le PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_le_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_le_tenant REFERENCES dbo.tenants(id),
    customer_id  BIGINT           NULL,
    entry_date   DATE             NULL,
    description  NVARCHAR(400)    NULL,
    debit        DECIMAL(19,4)    NOT NULL CONSTRAINT df_le_debit  DEFAULT (0),
    credit       DECIMAL(19,4)    NOT NULL CONSTRAINT df_le_credit DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_le_del     DEFAULT (0),
    CONSTRAINT uq_le_public UNIQUE (public_id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_le_customer'
               AND object_id = OBJECT_ID('dbo.ledger_entries'))
    CREATE INDEX ix_le_customer ON dbo.ledger_entries (tenant_id, customer_id);
GO

/* RLS predicates — add only if not already present on the table. */
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.ledger_entries'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ledger_entries,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ledger_entries AFTER INSERT;
GO

/* 2. Editable description for invoice (receivable) rows. */
IF COL_LENGTH('dbo.invoices', 'memo') IS NULL
    ALTER TABLE dbo.invoices ADD memo NVARCHAR(400) NULL;
GO

PRINT 'V050: ledger_entries created; invoices.memo added.';
GO

GO

/* ========================================================================
   V051__supplier_ledger.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V051__supplier_ledger.sql
 * Mirror the customer-ledger capability onto suppliers:
 *   1) ledger_entries.supplier_id — manual entries can target a supplier.
 *   2) supplier_bills.memo         — editable description for bill (payable) rows.
 * Idempotent. Run after V050.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.ledger_entries', 'supplier_id') IS NULL
    ALTER TABLE dbo.ledger_entries ADD supplier_id BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_le_supplier'
               AND object_id = OBJECT_ID('dbo.ledger_entries'))
    CREATE INDEX ix_le_supplier ON dbo.ledger_entries (tenant_id, supplier_id);
GO

IF COL_LENGTH('dbo.supplier_bills', 'memo') IS NULL
    ALTER TABLE dbo.supplier_bills ADD memo NVARCHAR(400) NULL;
GO

PRINT 'V051: ledger_entries.supplier_id + supplier_bills.memo added.';
GO

GO

/* ========================================================================
   V052__supplier_bill_issued_date.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V052__supplier_bill_issued_date.sql
 * Give supplier bills an issued (creation) date so the supplier ledger dates
 * each bill by when it was added — mirroring invoices.issued_date — instead of
 * its due date. Backfills existing rows: PO-created bills use due_on − 30 days
 * (the PO flow sets due = created + 30); otherwise fall back to due_on / today.
 *
 * The backfill spans tenants, so the RLS policy is toggled off around the
 * UPDATE and switched back on. Idempotent. Run after V051.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.supplier_bills', 'issued_date') IS NULL
    ALTER TABLE dbo.supplier_bills ADD issued_date DATE NULL;
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = OFF);
GO

UPDATE dbo.supplier_bills
   SET issued_date = COALESCE(
        CASE WHEN due_on IS NOT NULL THEN DATEADD(DAY, -30, due_on) END,
        due_on,
        CAST(SYSUTCDATETIME() AS DATE))
 WHERE issued_date IS NULL;
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = ON);
GO

PRINT 'V052: supplier_bills.issued_date added and backfilled.';
GO

GO

/* ========================================================================
   V053__supplier_details.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V053__supplier_details.sql
 * Extra supplier details surfaced on the supplier drill-down "Details" panel and
 * auto-filled into generated PO invoices: VAT number, contact person, bank
 * details. Idempotent. Run after V052.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.suppliers', 'vat_number') IS NULL
    ALTER TABLE dbo.suppliers ADD vat_number NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'contact_person') IS NULL
    ALTER TABLE dbo.suppliers ADD contact_person NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_details') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_details NVARCHAR(400) NULL;
GO

PRINT 'V053: suppliers.vat_number, contact_person, bank_details added.';
GO

GO

/* ========================================================================
   V054__customer_details.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V054__customer_details.sql
 * Extra customer details for the redesigned customer card and auto-filled into
 * generated sales invoices: tax id, bank name, bank account, currency, contact
 * title. Idempotent. Run after V053.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.customers', 'tax_id') IS NULL
    ALTER TABLE dbo.customers ADD tax_id NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.customers', 'bank_name') IS NULL
    ALTER TABLE dbo.customers ADD bank_name NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.customers', 'bank_account') IS NULL
    ALTER TABLE dbo.customers ADD bank_account NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.customers', 'currency') IS NULL
    ALTER TABLE dbo.customers ADD currency NVARCHAR(3) NULL;
GO
IF COL_LENGTH('dbo.customers', 'contact_title') IS NULL
    ALTER TABLE dbo.customers ADD contact_title NVARCHAR(120) NULL;
GO

PRINT 'V054: customer tax_id, bank_name, bank_account, currency, contact_title added.';
GO

GO

/* ========================================================================
   V055__ledger_payment_type.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V055__ledger_payment_type.sql  (SUPERSEDED)
 * This migration shared the V055 version number with
 * V055__stock_transfer_lines.sql, so it could be skipped by the migration
 * runner. Its work has been moved to V059__ledger_payment_type.sql (idempotent).
 * This file is intentionally a no-op to remove the duplicate-version conflict.
 * ==========================================================================*/
PRINT 'V055__ledger_payment_type: superseded by V059__ledger_payment_type.sql (no-op).';
GO

GO

/* ========================================================================
   V055__stock_transfer_lines.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V055__stock_transfer_lines.sql
 * Line items for a stock transfer (article + colour + size + qty), so a
 * transfer captures what's moving — mirroring sales order lines. Idempotent.
 * Run after V054.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF OBJECT_ID('dbo.stock_transfer_lines', 'U') IS NULL
CREATE TABLE dbo.stock_transfer_lines (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_stl PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_stl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_stl_tenant REFERENCES dbo.tenants(id),
    transfer_id  BIGINT           NOT NULL CONSTRAINT fk_stl_transfer REFERENCES dbo.stock_transfers(id),
    name         NVARCHAR(200)    NOT NULL,
    color        NVARCHAR(60)     NULL,
    size         NVARCHAR(60)     NULL,
    sku          NVARCHAR(64)     NULL,
    qty          INT              NOT NULL CONSTRAINT df_stl_qty DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_stl_del DEFAULT (0),
    CONSTRAINT uq_stl_public UNIQUE (public_id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_stl_transfer'
               AND object_id = OBJECT_ID('dbo.stock_transfer_lines'))
    CREATE INDEX ix_stl_transfer ON dbo.stock_transfer_lines (tenant_id, transfer_id);
GO

IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.stock_transfer_lines'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfer_lines,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfer_lines AFTER INSERT;
GO

PRINT 'V055: stock_transfer_lines created.';
GO

GO

/* ========================================================================
   V056__company_bank_details.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V056__company_bank_details.sql
 * Company (tenant) banking details for the "Remit to" block on sales invoices —
 * these are OUR OWN bank details, edited in Company Profile → Banking details.
 * Idempotent. Run after V055.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.tenants', 'bank_name') IS NULL
    ALTER TABLE dbo.tenants ADD bank_name NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.tenants', 'bank_account') IS NULL
    ALTER TABLE dbo.tenants ADD bank_account NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.tenants', 'bank_iban') IS NULL
    ALTER TABLE dbo.tenants ADD bank_iban NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.tenants', 'bank_swift') IS NULL
    ALTER TABLE dbo.tenants ADD bank_swift NVARCHAR(20) NULL;
GO

PRINT 'V056: tenants.bank_name, bank_account, bank_iban, bank_swift added.';
GO

GO

/* ========================================================================
   V057__product_fabric.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V057__product_fabric.sql
 * Dedicated "Fabric / Matière" specification for products. Surfaces on the
 * catalog Specifications panel and auto-fills the "Matière / fabric" field on
 * both sales and procurement commercial invoices. Idempotent. Run after V056.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.products', 'fabric') IS NULL
    ALTER TABLE dbo.products ADD fabric NVARCHAR(120) NULL;
GO

PRINT 'V057: products.fabric added.';
GO

GO

/* ========================================================================
   V058__supplier_bank_fields.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V058__supplier_bank_fields.sql
 * Structured supplier banking details (replacing the single free-text
 * bank_details on the UI): bank name, account title / beneficiary, account
 * number, SWIFT/BIC, IBAN. These auto-fill the "Coordonnées bancaires ·
 * Bank & payment details" block on procurement invoices. Idempotent.
 * The legacy bank_details column is kept for back-compat. Run after V057.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.suppliers', 'bank_name') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_name NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_account_title') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_account_title NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_account_number') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_account_number NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_swift') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_swift NVARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_iban') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_iban NVARCHAR(60) NULL;
GO

PRINT 'V058: suppliers bank_name, bank_account_title, bank_account_number, bank_swift, bank_iban added.';
GO

GO

/* ========================================================================
   V059__ledger_payment_type.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V059__ledger_payment_type.sql
 * "Payment Type" (Cash / Bank) against every ledger line — invoices, supplier
 * bills, payments, credit notes, and manual ledger entries — so the Cash and
 * Bank ledgers can be derived from it.
 *
 * NOTE: this supersedes the earlier V055__ledger_payment_type.sql, which shared
 * the V055 version number with V055__stock_transfer_lines.sql and could be
 * skipped by the migration runner. A missing payment_type column is what made
 * /finance/bank-ledger return HTTP 500. Nullable, idempotent. Run after V058.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.invoices', 'payment_type') IS NULL
    ALTER TABLE dbo.invoices ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_invoices_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.supplier_bills', 'payment_type') IS NULL
    ALTER TABLE dbo.supplier_bills ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_supplier_bills_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.payments', 'payment_type') IS NULL
    ALTER TABLE dbo.payments ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_payments_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.credit_notes', 'payment_type') IS NULL
    ALTER TABLE dbo.credit_notes ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_credit_notes_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.ledger_entries', 'payment_type') IS NULL
    ALTER TABLE dbo.ledger_entries ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_ledger_entries_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO

PRINT 'V059: payment_type (cash/bank) ensured on invoices, supplier_bills, payments, credit_notes, ledger_entries.';
GO

GO

/* ========================================================================
   V060__inspection_workflow.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V060__inspection_workflow.sql
 * Phase 1 of the Quality Inspection workflow upgrade. Extends the inspections
 * table with the full inspection header + AQL sampling + disposition + linkage
 * fields, and adds the per-inspection checklist and defect line tables.
 * Idempotent. Preserves all existing columns/data. Run after V059.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* ---- inspections: enriched header, AQL, disposition, linkage ---- */
IF COL_LENGTH('dbo.inspections', 'sku') IS NULL
    ALTER TABLE dbo.inspections ADD sku NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'product') IS NULL
    ALTER TABLE dbo.inspections ADD product NVARCHAR(200) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'batch_lot') IS NULL
    ALTER TABLE dbo.inspections ADD batch_lot NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'inspection_type') IS NULL
    ALTER TABLE dbo.inspections ADD inspection_type NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'prod_qty') IS NULL
    ALTER TABLE dbo.inspections ADD prod_qty INT NULL;
GO
IF COL_LENGTH('dbo.inspections', 'sample_size') IS NULL
    ALTER TABLE dbo.inspections ADD sample_size INT NULL;
GO
IF COL_LENGTH('dbo.inspections', 'max_defects') IS NULL
    ALTER TABLE dbo.inspections ADD max_defects INT NULL;
GO
IF COL_LENGTH('dbo.inspections', 'inspection_date') IS NULL
    ALTER TABLE dbo.inspections ADD inspection_date DATE NULL
        CONSTRAINT df_inspections_date DEFAULT CAST(SYSUTCDATETIME() AS DATE);
GO
IF COL_LENGTH('dbo.inspections', 'started_at') IS NULL
    ALTER TABLE dbo.inspections ADD started_at DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.inspections', 'finalized_at') IS NULL
    ALTER TABLE dbo.inspections ADD finalized_at DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.inspections', 'disposition') IS NULL
    ALTER TABLE dbo.inspections ADD disposition NVARCHAR(24) NULL;   -- Rework|Hold|Scrap|RTV|ReInspection
GO
IF COL_LENGTH('dbo.inspections', 'disposition_notes') IS NULL
    ALTER TABLE dbo.inspections ADD disposition_notes NVARCHAR(400) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'assigned_to') IS NULL
    ALTER TABLE dbo.inspections ADD assigned_to NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'due_date') IS NULL
    ALTER TABLE dbo.inspections ADD due_date DATE NULL;
GO
IF COL_LENGTH('dbo.inspections', 'parent_inspection_id') IS NULL
    ALTER TABLE dbo.inspections ADD parent_inspection_id BIGINT NULL;  -- re-inspection → original
GO
IF COL_LENGTH('dbo.inspections', 'shipment_ref') IS NULL
    ALTER TABLE dbo.inspections ADD shipment_ref NVARCHAR(32) NULL;    -- linked shipment on pass
GO

/* ---- checklist lines (one row per inspection criterion) ---- */
IF OBJECT_ID('dbo.inspection_checks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.inspection_checks (
        id            BIGINT IDENTITY(1,1) PRIMARY KEY,
        public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_inschk_pub DEFAULT NEWSEQUENTIALID(),
        tenant_id     UNIQUEIDENTIFIER NOT NULL,
        inspection_id BIGINT NOT NULL,
        seq           INT NOT NULL CONSTRAINT df_inschk_seq DEFAULT 0,
        criterion     NVARCHAR(120) NOT NULL,
        requirement   NVARCHAR(200) NULL,
        target_value  DECIMAL(19,4) NULL,   -- numeric spec centre, when measurable
        tolerance     DECIMAL(19,4) NULL,   -- ± tolerance, when measurable
        actual        NVARCHAR(120) NULL,   -- free text OR numeric string
        result        NVARCHAR(12) NOT NULL CONSTRAINT df_inschk_res DEFAULT 'Pending', -- Pass|Fail|Warning|NA|Pending
        notes         NVARCHAR(400) NULL,
        is_deleted    BIT NOT NULL CONSTRAINT df_inschk_del DEFAULT 0,
        CONSTRAINT fk_inschk_ins FOREIGN KEY (inspection_id) REFERENCES dbo.inspections(id)
    );
    CREATE INDEX ix_inschk_ins ON dbo.inspection_checks (inspection_id, seq);
END
GO

/* ---- defect lines (link to defect_types catalog) ---- */
IF OBJECT_ID('dbo.inspection_defects', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.inspection_defects (
        id             BIGINT IDENTITY(1,1) PRIMARY KEY,
        public_id      UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_insdef_pub DEFAULT NEWSEQUENTIALID(),
        tenant_id      UNIQUEIDENTIFIER NOT NULL,
        inspection_id  BIGINT NOT NULL,
        defect_no      NVARCHAR(32) NULL,
        defect_type_id BIGINT NULL,          -- FK into defect_types (nullable → free text fallback)
        defect_name    NVARCHAR(120) NULL,   -- snapshot of the catalog name
        category       NVARCHAR(40) NULL,    -- snapshot
        severity       NVARCHAR(20) NULL,    -- snapshot (Critical|Major|Minor)
        qty_affected   INT NOT NULL CONSTRAINT df_insdef_qty DEFAULT 1,
        location       NVARCHAR(120) NULL,
        description    NVARCHAR(400) NULL,
        corrective     NVARCHAR(400) NULL,
        is_deleted     BIT NOT NULL CONSTRAINT df_insdef_del DEFAULT 0,
        CONSTRAINT fk_insdef_ins FOREIGN KEY (inspection_id) REFERENCES dbo.inspections(id)
    );
    CREATE INDEX ix_insdef_ins ON dbo.inspection_defects (inspection_id);
END
GO

/* ---- register the two new tables with tenant Row-Level Security ---- */
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.inspection_checks'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_checks,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_checks AFTER INSERT;
GO
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.inspection_defects'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_defects,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_defects AFTER INSERT;
GO

PRINT 'V060: inspections enriched + inspection_checks + inspection_defects created (RLS applied).';
GO

GO

/* ========================================================================
   V061__inspection_defect_image.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V061__inspection_defect_image.sql
 * Per-defect photo evidence: store the uploaded document id directly on the
 * inspection defect, so images are attached inline when logging a defect (no
 * separate Photos/Defects tab needed). Idempotent. Run after V060.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.inspection_defects', 'image_doc_id') IS NULL
    ALTER TABLE dbo.inspection_defects ADD image_doc_id NVARCHAR(64) NULL;
GO

PRINT 'V061: inspection_defects.image_doc_id added.';
GO

GO

/* ========================================================================
   V062__inspection_item.sql
   ======================================================================== */
GO
/* ============================================================================
 * Preduit ERP — V062__inspection_item.sql
 * Per-item inspections: a production order with several items (production lines)
 * now gets one inspection per item, all sharing the order reference. This adds
 * the item label so multiple inspections can coexist under one order.
 * Idempotent. Run after V061.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.inspections', 'item') IS NULL
    ALTER TABLE dbo.inspections ADD item NVARCHAR(200) NULL;
GO

PRINT 'V062: inspections.item added.';
GO

GO

/* ==========================================================================
   End of consolidated migrations.
   ========================================================================== */

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

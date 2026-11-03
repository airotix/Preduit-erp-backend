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

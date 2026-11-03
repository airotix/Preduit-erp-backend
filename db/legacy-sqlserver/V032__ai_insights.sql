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

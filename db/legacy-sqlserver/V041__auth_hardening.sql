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

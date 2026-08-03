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

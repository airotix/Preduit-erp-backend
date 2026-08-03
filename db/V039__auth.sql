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

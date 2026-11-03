/* ============================================================================
 * One-off fix: create the dbo.subscriptions table that failed in the first
 * V001 run ("Incorrect syntax near the keyword 'plan'").
 * Run this once in SSMS, then continue with V002 and V003.
 * (V001 has been corrected too, so a clean re-run won't need this.)
 * ==========================================================================*/
USE preduit;
GO

IF OBJECT_ID('dbo.subscriptions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.subscriptions (
        id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_subscriptions PRIMARY KEY,
        public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_subs_pub DEFAULT NEWSEQUENTIALID(),
        tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_subs_tenant REFERENCES dbo.tenants(id),
        [plan]        NVARCHAR(40)     NOT NULL CONSTRAINT df_subs_plan DEFAULT ('trial'),
        status        NVARCHAR(20)     NOT NULL CONSTRAINT df_subs_status DEFAULT ('trialing'),
        seat_limit    INT              NOT NULL CONSTRAINT df_subs_seats DEFAULT (5),
        trial_ends_at DATETIME2        NULL,
        created_at    DATETIME2        NOT NULL CONSTRAINT df_subs_created DEFAULT SYSUTCDATETIME(),
        row_version   ROWVERSION,
        CONSTRAINT uq_subs_public UNIQUE (public_id)
    );

    CREATE INDEX ix_subs_tenant ON dbo.subscriptions (tenant_id);

    PRINT 'dbo.subscriptions created.';
END
ELSE
    PRINT 'dbo.subscriptions already exists — nothing to do.';
GO

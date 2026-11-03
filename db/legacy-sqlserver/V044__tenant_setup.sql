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

/* ============================================================================
 * Preduit ERP — seed_dev_sales.sql  (DEV ONLY)
 * A couple of demo customers for the Preduit Demo tenant so the Customers list
 * isn't empty. Run after V004. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO

DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.customers WHERE tenant_id = @t)
    INSERT INTO dbo.customers (tenant_id, name, email, [type], region) VALUES
      (@t, 'Maison Lyon',         'maison.lyon@b2b.fr',   'Wholesale', 'France'),
      (@t, 'Nordic Retail Group', 'buy@nordicretail.se',  'Wholesale', 'Sweden'),
      (@t, 'Amelia Chen',         'amelia.c@gmail.com',   'Retail',    'Singapore');

REVERT;
GO
PRINT 'Demo customers seeded.';
GO

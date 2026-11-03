/* ============================================================================
 * Preduit ERP — seed_dev_controls.sql  (DEV ONLY)
 * Demo periods (one closed), FY budget lines, and a couple of fixed assets.
 * Run after V026. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.fiscal_periods WHERE tenant_id = @t)
    INSERT INTO dbo.fiscal_periods (tenant_id, name, start_date, end_date, status) VALUES
      (@t, 'Jun 2026', '2026-06-01', '2026-06-30', 'Closed'),
      (@t, 'Jul 2026', '2026-07-01', '2026-07-31', 'Open'),
      (@t, 'Aug 2026', '2026-08-01', '2026-08-31', 'Open');

IF NOT EXISTS (SELECT 1 FROM dbo.budget_lines WHERE tenant_id = @t)
    INSERT INTO dbo.budget_lines (tenant_id, fiscal_year, account_code, account_name, amount) VALUES
      (@t, 2026, '4000', 'Sales revenue',       5000000),
      (@t, 2026, '5000', 'Cost of goods sold',  2800000);

IF NOT EXISTS (SELECT 1 FROM dbo.fixed_assets WHERE tenant_id = @t)
    INSERT INTO dbo.fixed_assets (tenant_id, asset_no, name, category, cost, salvage, life_months, in_service_date, accumulated, status) VALUES
      (@t, 'FA-1001', 'Cutting machine', 'Equipment', 360000,  0,      36, '2026-01-01', 0, 'Active'),
      (@t, 'FA-1002', 'Delivery van',    'Vehicles',  1200000, 120000, 60, '2026-03-01', 0, 'Active');

REVERT;
GO
PRINT 'Finance controls demo data seeded.';
GO

/* ============================================================================
 * Preduit ERP — seed_dev_fx.sql  (DEV ONLY)
 * Exchange-rate matrix among PKR / USD / EUR / AED so the finance currency
 * toggle converts real amounts. Rates are indicative demo values (USD-anchored).
 * Run after V012 (exchange_rates exists from V001). Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
DECLARE @d DATE = '2026-01-01';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.exchange_rates WHERE tenant_id = @t)
    INSERT INTO dbo.exchange_rates (tenant_id, from_ccy, to_ccy, rate, valid_from, source) VALUES
      (@t, 'USD', 'EUR', 0.92590000,   @d, 'seed'),
      (@t, 'USD', 'PKR', 285.71000000, @d, 'seed'),
      (@t, 'USD', 'AED', 3.67200000,   @d, 'seed'),
      (@t, 'EUR', 'USD', 1.08000000,   @d, 'seed'),
      (@t, 'EUR', 'PKR', 308.57000000, @d, 'seed'),
      (@t, 'EUR', 'AED', 3.96600000,   @d, 'seed'),
      (@t, 'PKR', 'USD', 0.00350000,   @d, 'seed'),
      (@t, 'PKR', 'EUR', 0.00324100,   @d, 'seed'),
      (@t, 'PKR', 'AED', 0.01285400,   @d, 'seed'),
      (@t, 'AED', 'USD', 0.27230000,   @d, 'seed'),
      (@t, 'AED', 'EUR', 0.25213000,   @d, 'seed'),
      (@t, 'AED', 'PKR', 77.80000000,  @d, 'seed');

REVERT;
GO
PRINT 'FX rate matrix seeded.';
GO

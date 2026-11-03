/* ============================================================================
 * Preduit ERP — seed_dev_gl.sql  (DEV ONLY)
 * Makes the GL statements substantive: carries chart balances into opening
 * balances, sets normal sides, and inserts a balancing equity account so the
 * balance sheet ties out (Assets = Liabilities + Equity + Retained earnings).
 * Run after V024/V025. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* Normal sides (idempotent). */
UPDATE dbo.chart_of_accounts
   SET normal_side = CASE WHEN acct_type IN ('Asset', 'Expense') THEN 'D' ELSE 'C' END
 WHERE tenant_id = @t AND normal_side IS NULL;

/* Carry existing balances into opening balances so statements have substance. */
UPDATE dbo.chart_of_accounts
   SET opening_balance = balance
 WHERE tenant_id = @t AND opening_balance = 0 AND balance <> 0;

/* Insert a retained-earnings/equity plug so the balance sheet balances. */
IF NOT EXISTS (SELECT 1 FROM dbo.chart_of_accounts WHERE tenant_id = @t AND acct_type = 'Equity')
BEGIN
    DECLARE @assets DECIMAL(19,4) = (SELECT COALESCE(SUM(opening_balance),0) FROM dbo.chart_of_accounts WHERE tenant_id=@t AND acct_type='Asset');
    DECLARE @liab   DECIMAL(19,4) = (SELECT COALESCE(SUM(opening_balance),0) FROM dbo.chart_of_accounts WHERE tenant_id=@t AND acct_type='Liability');
    DECLARE @inc    DECIMAL(19,4) = (SELECT COALESCE(SUM(opening_balance),0) FROM dbo.chart_of_accounts WHERE tenant_id=@t AND acct_type='Income');
    DECLARE @exp    DECIMAL(19,4) = (SELECT COALESCE(SUM(opening_balance),0) FROM dbo.chart_of_accounts WHERE tenant_id=@t AND acct_type='Expense');
    DECLARE @plug   DECIMAL(19,4) = @assets - @liab - (@inc - @exp);
    INSERT INTO dbo.chart_of_accounts (tenant_id, code, name, acct_type, currency_code, opening_balance, balance, normal_side, is_active)
    VALUES (@t, '3000', 'Retained earnings b/f', 'Equity', 'EUR', @plug, @plug, 'C', 1);
END

REVERT;
GO
PRINT 'GL opening balances + equity plug seeded.';
GO

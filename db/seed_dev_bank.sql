/* ============================================================================
 * Preduit ERP — seed_dev_bank.sql  (DEV ONLY)
 * A bank account + statement lines that match the seeded payments (so
 * Auto-match links three and leaves a bank charge unmatched). Run after V028.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.bank_accounts WHERE tenant_id = @t)
    INSERT INTO dbo.bank_accounts (tenant_id, name, account_no, gl_code, currency_code)
      VALUES (@t, 'Main current account', 'ACC-4821', '1000', 'EUR');

DECLARE @ba BIGINT = (SELECT TOP 1 id FROM dbo.bank_accounts WHERE tenant_id = @t ORDER BY id);

IF @ba IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dbo.bank_transactions WHERE tenant_id = @t)
    INSERT INTO dbo.bank_transactions (tenant_id, bank_account_id, txn_date, description, amount, status) VALUES
      (@t, @ba, '2026-07-05', 'Customer receipt',     9460,   'Unmatched'),
      (@t, @ba, '2026-07-08', 'Supplier payment',    -28400,  'Unmatched'),
      (@t, @ba, '2026-07-12', 'Customer receipt',     2400,   'Unmatched'),
      (@t, @ba, '2026-07-15', 'Bank service charge',  -150,   'Unmatched');

REVERT;
GO
PRINT 'Bank reconciliation demo data seeded.';
GO

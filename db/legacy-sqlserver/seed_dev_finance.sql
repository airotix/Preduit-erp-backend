/* ============================================================================
 * Preduit ERP — seed_dev_finance.sql  (DEV ONLY)
 * Chart of accounts, journal entries + lines, payments, AR/AP aging snapshots.
 * Run after V012. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* Chart of accounts */
IF NOT EXISTS (SELECT 1 FROM dbo.chart_of_accounts WHERE tenant_id=@t)
    INSERT INTO dbo.chart_of_accounts (tenant_id, code, name, acct_type, balance) VALUES
      (@t, '1000', 'Cash & bank',          'Asset',     1840000),
      (@t, '1100', 'Accounts receivable',  'Asset',     284000),
      (@t, '1200', 'Inventory',            'Asset',     612000),
      (@t, '2000', 'Accounts payable',     'Liability', 198400),
      (@t, '4000', 'Sales revenue',        'Income',    4820000),
      (@t, '5000', 'Cost of goods sold',   'Expense',   2710000);

/* Journal entries + lines */
IF NOT EXISTS (SELECT 1 FROM dbo.journal_entries WHERE tenant_id=@t)
BEGIN
    INSERT INTO dbo.journal_entries (tenant_id, entry_no, entry_date, memo, total_debit, total_credit, status, source_note) VALUES
      (@t, 'JE-4471', '27 Jun', 'Revenue & COGS · SO-12353 shipped', 416, 416, 'Posted',
       'Auto-posted when SO-12353 shipped: recognizes revenue against receivables and relieves inventory at cost into COGS. Debits equal credits, so the entry is balanced.'),
      (@t, 'JE-4468', '24 Jun', 'Payroll accrual · June', 84200, 84200, 'Draft', NULL);

    DECLARE @je BIGINT = (SELECT id FROM dbo.journal_entries WHERE tenant_id=@t AND entry_no='JE-4471');
    INSERT INTO dbo.journal_lines (tenant_id, entry_id, account, description, debit, credit) VALUES
      (@t, @je, '1100 · Accounts receivable', 'Invoice to customer',   287, 0),
      (@t, @je, '4000 · Sales revenue',       'Revenue recognized',    0,   287),
      (@t, @je, '5000 · Cost of goods sold',  'COGS posted at cost',   129, 0),
      (@t, @je, '1200 · Inventory',           'Inventory relieved',    0,   129);
END

/* Payments */
IF NOT EXISTS (SELECT 1 FROM dbo.payments WHERE tenant_id=@t)
    INSERT INTO dbo.payments (tenant_id, payment_no, pay_date, party, allocated_to, amount, pay_type, status) VALUES
      (@t, 'PMT-2231', '25 Jun', 'Boutique Atlas',     'INV-8839', 9460,  'Receipt',      'Cleared'),
      (@t, 'PMT-2230', '24 Jun', 'Lahore Textile Co.', 'BILL-1182',28400, 'Disbursement', 'Cleared'),
      (@t, 'PMT-2229', '23 Jun', 'Maison Lyon',        'INV-8841', 2400,  'Receipt',      'Pending');

/* AR / AP aging snapshots */
IF NOT EXISTS (SELECT 1 FROM dbo.ar_aging WHERE tenant_id=@t)
    INSERT INTO dbo.ar_aging (tenant_id, customer_name, region, current_amt, b1_30, b31_60, b61_90, b90_plus) VALUES
      (@t, 'Maison Lyon',         'France',    2420, 2400, 0,    0, 0),
      (@t, 'Nordic Retail Group', 'Sweden',    8100, 0,    0,    0, 0),
      (@t, 'Studio Norte',        'Portugal',  0,    0,    11900,0, 0);

IF NOT EXISTS (SELECT 1 FROM dbo.ap_aging WHERE tenant_id=@t)
    INSERT INTO dbo.ap_aging (tenant_id, supplier_name, region, current_amt, b1_30, b31_60, b61_90, b90_plus) VALUES
      (@t, 'Anhui Knit Mills',   'China',    42800, 0, 0, 0, 0),
      (@t, 'Bursa Denim A.S.',   'Turkey',   0,     0, 19400, 0, 0);

REVERT;
GO
PRINT 'Finance demo data seeded.';
GO

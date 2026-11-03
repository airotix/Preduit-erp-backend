/* ============================================================================
 * Preduit ERP — V024__gl_engine.sql
 * General-ledger engine foundation: real posting dates, line→account links,
 * account normal side, and reversal linkage. Run after V023.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.journal_entries ADD
    entry_on       DATE       NULL,          -- real posting date (entry_date kept for display)
    posted_at      DATETIME2  NULL,
    period_id      BIGINT     NULL,
    reversed_of_id BIGINT     NULL;
GO

ALTER TABLE dbo.journal_lines ADD
    account_id BIGINT NULL CONSTRAINT fk_jl_account REFERENCES dbo.chart_of_accounts(id);
GO

ALTER TABLE dbo.chart_of_accounts ADD
    normal_side CHAR(1) NULL;               -- 'D' debit-normal, 'C' credit-normal
GO

/* Backfill normal side from account type. */
UPDATE dbo.chart_of_accounts
   SET normal_side = CASE WHEN acct_type IN ('Asset', 'Expense') THEN 'D' ELSE 'C' END
 WHERE normal_side IS NULL;
GO

/* Backfill line→account from the leading code in the label ('1100 · Name'). */
UPDATE jl
   SET account_id = a.id
  FROM dbo.journal_lines jl
  JOIN dbo.chart_of_accounts a
    ON a.tenant_id = jl.tenant_id
   AND jl.account LIKE a.code + ' %'
 WHERE jl.account_id IS NULL;
GO

/* Best-effort backfill of a real date from ISO-ish strings (others stay NULL). */
UPDATE dbo.journal_entries
   SET entry_on = TRY_CONVERT(DATE, entry_date)
 WHERE entry_on IS NULL;
GO

PRINT 'GL engine columns added.';
GO

/* ============================================================================
 * Preduit ERP — V025__gl_posting_flags.sql
 * Links source documents to their general-ledger journal entry so each posts
 * exactly once. Run after V024.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.invoices ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_inv_posted DEFAULT (0);
GO
ALTER TABLE dbo.supplier_bills ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_bill_posted DEFAULT (0);
GO
ALTER TABLE dbo.payments ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_pmt_posted DEFAULT (0);
GO
ALTER TABLE dbo.credit_notes ADD
    gl_journal_id BIGINT NULL,
    posted        BIT NOT NULL CONSTRAINT df_cn_posted DEFAULT (0);
GO

PRINT 'GL posting flags added to source documents.';
GO

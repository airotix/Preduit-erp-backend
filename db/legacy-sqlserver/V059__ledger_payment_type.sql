/* ============================================================================
 * Preduit ERP — V059__ledger_payment_type.sql
 * "Payment Type" (Cash / Bank) against every ledger line — invoices, supplier
 * bills, payments, credit notes, and manual ledger entries — so the Cash and
 * Bank ledgers can be derived from it.
 *
 * NOTE: this supersedes the earlier V055__ledger_payment_type.sql, which shared
 * the V055 version number with V055__stock_transfer_lines.sql and could be
 * skipped by the migration runner. A missing payment_type column is what made
 * /finance/bank-ledger return HTTP 500. Nullable, idempotent. Run after V058.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.invoices', 'payment_type') IS NULL
    ALTER TABLE dbo.invoices ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_invoices_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.supplier_bills', 'payment_type') IS NULL
    ALTER TABLE dbo.supplier_bills ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_supplier_bills_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.payments', 'payment_type') IS NULL
    ALTER TABLE dbo.payments ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_payments_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.credit_notes', 'payment_type') IS NULL
    ALTER TABLE dbo.credit_notes ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_credit_notes_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO
IF COL_LENGTH('dbo.ledger_entries', 'payment_type') IS NULL
    ALTER TABLE dbo.ledger_entries ADD payment_type NVARCHAR(10) NULL
        CONSTRAINT ck_ledger_entries_payment_type CHECK (payment_type IN ('cash', 'bank'));
GO

PRINT 'V059: payment_type (cash/bank) ensured on invoices, supplier_bills, payments, credit_notes, ledger_entries.';
GO

/* ============================================================================
 * Preduit ERP — V052__supplier_bill_issued_date.sql
 * Give supplier bills an issued (creation) date so the supplier ledger dates
 * each bill by when it was added — mirroring invoices.issued_date — instead of
 * its due date. Backfills existing rows: PO-created bills use due_on − 30 days
 * (the PO flow sets due = created + 30); otherwise fall back to due_on / today.
 *
 * The backfill spans tenants, so the RLS policy is toggled off around the
 * UPDATE and switched back on. Idempotent. Run after V051.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.supplier_bills', 'issued_date') IS NULL
    ALTER TABLE dbo.supplier_bills ADD issued_date DATE NULL;
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = OFF);
GO

UPDATE dbo.supplier_bills
   SET issued_date = COALESCE(
        CASE WHEN due_on IS NOT NULL THEN DATEADD(DAY, -30, due_on) END,
        due_on,
        CAST(SYSUTCDATETIME() AS DATE))
 WHERE issued_date IS NULL;
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = ON);
GO

PRINT 'V052: supplier_bills.issued_date added and backfilled.';
GO

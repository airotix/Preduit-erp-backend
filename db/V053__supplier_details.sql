/* ============================================================================
 * Preduit ERP — V053__supplier_details.sql
 * Extra supplier details surfaced on the supplier drill-down "Details" panel and
 * auto-filled into generated PO invoices: VAT number, contact person, bank
 * details. Idempotent. Run after V052.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.suppliers', 'vat_number') IS NULL
    ALTER TABLE dbo.suppliers ADD vat_number NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'contact_person') IS NULL
    ALTER TABLE dbo.suppliers ADD contact_person NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_details') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_details NVARCHAR(400) NULL;
GO

PRINT 'V053: suppliers.vat_number, contact_person, bank_details added.';
GO

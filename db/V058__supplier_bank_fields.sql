/* ============================================================================
 * Preduit ERP — V058__supplier_bank_fields.sql
 * Structured supplier banking details (replacing the single free-text
 * bank_details on the UI): bank name, account title / beneficiary, account
 * number, SWIFT/BIC, IBAN. These auto-fill the "Coordonnées bancaires ·
 * Bank & payment details" block on procurement invoices. Idempotent.
 * The legacy bank_details column is kept for back-compat. Run after V057.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.suppliers', 'bank_name') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_name NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_account_title') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_account_title NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_account_number') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_account_number NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_swift') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_swift NVARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.suppliers', 'bank_iban') IS NULL
    ALTER TABLE dbo.suppliers ADD bank_iban NVARCHAR(60) NULL;
GO

PRINT 'V058: suppliers bank_name, bank_account_title, bank_account_number, bank_swift, bank_iban added.';
GO

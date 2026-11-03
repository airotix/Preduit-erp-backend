/* ============================================================================
 * Preduit ERP — V056__company_bank_details.sql
 * Company (tenant) banking details for the "Remit to" block on sales invoices —
 * these are OUR OWN bank details, edited in Company Profile → Banking details.
 * Idempotent. Run after V055.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.tenants', 'bank_name') IS NULL
    ALTER TABLE dbo.tenants ADD bank_name NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.tenants', 'bank_account') IS NULL
    ALTER TABLE dbo.tenants ADD bank_account NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.tenants', 'bank_iban') IS NULL
    ALTER TABLE dbo.tenants ADD bank_iban NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.tenants', 'bank_swift') IS NULL
    ALTER TABLE dbo.tenants ADD bank_swift NVARCHAR(20) NULL;
GO

PRINT 'V056: tenants.bank_name, bank_account, bank_iban, bank_swift added.';
GO

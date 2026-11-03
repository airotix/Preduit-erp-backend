/* ============================================================================
 * Preduit ERP — V054__customer_details.sql
 * Extra customer details for the redesigned customer card and auto-filled into
 * generated sales invoices: tax id, bank name, bank account, currency, contact
 * title. Idempotent. Run after V053.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.customers', 'tax_id') IS NULL
    ALTER TABLE dbo.customers ADD tax_id NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.customers', 'bank_name') IS NULL
    ALTER TABLE dbo.customers ADD bank_name NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.customers', 'bank_account') IS NULL
    ALTER TABLE dbo.customers ADD bank_account NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.customers', 'currency') IS NULL
    ALTER TABLE dbo.customers ADD currency NVARCHAR(3) NULL;
GO
IF COL_LENGTH('dbo.customers', 'contact_title') IS NULL
    ALTER TABLE dbo.customers ADD contact_title NVARCHAR(120) NULL;
GO

PRINT 'V054: customer tax_id, bank_name, bank_account, currency, contact_title added.';
GO

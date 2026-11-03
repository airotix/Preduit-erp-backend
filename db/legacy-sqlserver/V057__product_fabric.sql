/* ============================================================================
 * Preduit ERP — V057__product_fabric.sql
 * Dedicated "Fabric / Matière" specification for products. Surfaces on the
 * catalog Specifications panel and auto-fills the "Matière / fabric" field on
 * both sales and procurement commercial invoices. Idempotent. Run after V056.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.products', 'fabric') IS NULL
    ALTER TABLE dbo.products ADD fabric NVARCHAR(120) NULL;
GO

PRINT 'V057: products.fabric added.';
GO

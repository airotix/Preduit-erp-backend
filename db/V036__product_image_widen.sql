/* ============================================================================
 * Preduit ERP — V036__product_image_widen.sql
 * Ensure products.image_url can hold an uploaded image (data URL), not just a
 * short link. Earlier V035 may have created it as NVARCHAR(1000); widen to MAX.
 * Idempotent: safe if the column is already NVARCHAR(MAX). Run after V035.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.products ALTER COLUMN image_url NVARCHAR(MAX) NULL;
GO
PRINT 'products.image_url widened to NVARCHAR(MAX).';
GO

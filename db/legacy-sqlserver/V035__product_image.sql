/* ============================================================================
 * Preduit ERP — V035__product_image.sql
 * Product image URL — shown on the product, and pulled onto the commercial
 * invoice (per article) generated from a PO. Run after V034.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

-- NVARCHAR(MAX): holds either a URL or an uploaded image as a data URL.
ALTER TABLE dbo.products ADD image_url NVARCHAR(MAX) NULL;
GO
PRINT 'products.image_url added.';
GO

/* ============================================================================
 * Preduit ERP — seed_dev_catalog_detail.sql  (DEV ONLY)
 * Fills the new detail fields for the demo tenant so the product detail page
 * shows a real variant matrix + specs. Run after V007. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* Color swatches + size ordering */
UPDATE dbo.attribute_values SET hex = '#262B3F'         WHERE tenant_id=@t AND code='NVY';
UPDATE dbo.attribute_values SET hex = '#E8E2D0'         WHERE tenant_id=@t AND code='CRM';
UPDATE dbo.attribute_values SET sort_order = 2          WHERE tenant_id=@t AND code='M';
UPDATE dbo.attribute_values SET sort_order = 3          WHERE tenant_id=@t AND code='L';

/* On-hand units per variant (drives the matrix cells) */
UPDATE dbo.product_variants SET qty_on_hand = 120 WHERE tenant_id=@t AND sku='KNT-NVY-M';
UPDATE dbo.product_variants SET qty_on_hand = 64  WHERE tenant_id=@t AND sku='KNT-NVY-L';
UPDATE dbo.product_variants SET qty_on_hand = 18  WHERE tenant_id=@t AND sku='KNT-CRM-M';
UPDATE dbo.product_variants SET qty_on_hand = 42  WHERE tenant_id=@t AND sku='CHN-NVY-M';
UPDATE dbo.product_variants SET qty_on_hand = 0   WHERE tenant_id=@t AND sku='CHN-NVY-L';
UPDATE dbo.product_variants SET qty_on_hand = 30  WHERE tenant_id=@t AND sku='OXF-CRM-M';

/* Product specs */
UPDATE dbo.products
   SET composition='100% Extrafine Merino Wool', gauge='12gg',
       care='Hand wash cold · Dry flat', origin='Made in Pakistan',
       hs_code='6110.11.00', weight='320 g'
 WHERE tenant_id=@t AND title='Merino Crew Knit';

UPDATE dbo.products
   SET composition='98% Cotton · 2% Elastane', gauge='—',
       care='Machine wash 30°', origin='Made in Portugal',
       hs_code='6203.42.00', weight='410 g'
 WHERE tenant_id=@t AND title='Tailored Chino';

UPDATE dbo.products
   SET composition='100% Organic Cotton', gauge='—',
       care='Machine wash 40°', origin='Made in Türkiye',
       hs_code='6205.20.00', weight='260 g'
 WHERE tenant_id=@t AND title='Oxford Shirt';

REVERT;
GO
PRINT 'Catalog detail fields seeded.';
GO



USE [Preduit-ERP];
GO

SELECT *
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_NAME = 'attribute_values';

USE [Preduit-ERP];
GO

SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
ORDER BY TABLE_NAME;
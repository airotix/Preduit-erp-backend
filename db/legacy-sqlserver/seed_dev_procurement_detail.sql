/* ============================================================================
 * Preduit ERP — seed_dev_procurement_detail.sql  (DEV ONLY)
 * Supplier contact, goods-receipt header + line items. Run after V011.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

UPDATE dbo.suppliers SET email='sales@anhuiknit.cn', phone='+86 551 6000 1234',
       address='Hefei, Anhui, China' WHERE tenant_id=@t AND name='Anhui Knit Mills';
UPDATE dbo.suppliers SET email='orders@lahoretextile.pk', phone='+92 42 111 2222',
       address='Kot Lakhpat, Lahore, Pakistan' WHERE tenant_id=@t AND name='Lahore Textile Co.';
UPDATE dbo.suppliers SET email='satis@bursadenim.com.tr', phone='+90 224 000 1122',
       address='Osmangazi, Bursa, Türkiye' WHERE tenant_id=@t AND name='Bursa Denim A.S.';
UPDATE dbo.suppliers SET email='hello@portotrims.pt', phone='+351 22 000 3344',
       address='Vila Nova de Gaia, Portugal' WHERE tenant_id=@t AND name='Porto Trims Ltd.';

UPDATE dbo.goods_receipts SET received_date='26 Jun 2026', location='Lahore DC'
 WHERE tenant_id=@t AND grn_no IN ('GRN-3320','GRN-3319','GRN-3318');

/* Receipt line items for the partial GRN-3319 */
IF NOT EXISTS (SELECT 1 FROM dbo.goods_receipt_lines WHERE tenant_id=@t)
BEGIN
    DECLARE @g BIGINT = (SELECT id FROM dbo.goods_receipts WHERE tenant_id=@t AND grn_no='GRN-3319');
    INSERT INTO dbo.goods_receipt_lines (tenant_id, grn_id, name, sku, ordered, received) VALUES
      (@t, @g, 'Merino yarn 2/28 · Navy',     'YRN-MER-NVY', 800, 800),
      (@t, @g, 'Merino yarn 2/28 · Charcoal', 'YRN-MER-CHR', 600, 400),
      (@t, @g, 'Merino yarn 2/28 · Stone',    'YRN-MER-STN', 400, 0);
END

REVERT;
GO
PRINT 'Procurement detail demo data seeded.';
GO

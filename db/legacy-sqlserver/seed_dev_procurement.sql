/* ============================================================================
 * Preduit ERP — seed_dev_procurement.sql  (DEV ONLY)
 * Suppliers (with scorecard metrics), POs (for the approval board), and goods
 * receipts for the demo tenant. Run after V010. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* Suppliers */
IF NOT EXISTS (SELECT 1 FROM dbo.suppliers WHERE tenant_id=@t)
    INSERT INTO dbo.suppliers (tenant_id, name, region, category, country_code, lead_time, on_time_pct, defect_rate, price_rating, score, status) VALUES
      (@t, 'Anhui Knit Mills',   'China',    'Knitwear · Yarn',  'CN', '45 days', 94, 1.60, 4.3, 4.6, 'Preferred'),
      (@t, 'Lahore Textile Co.', 'Pakistan', 'Woven · Shirting', 'PK', '21 days', 97, 0.80, 4.8, 4.8, 'Preferred'),
      (@t, 'Bursa Denim A.S.',   'Turkey',   'Denim',            'TR', '38 days', 88, 3.10, 4.1, 4.1, 'On watch'),
      (@t, 'Porto Trims Ltd.',   'Portugal', 'Trims · Hardware', 'PT', '14 days', 99, 0.40, 4.9, 4.9, 'Preferred');

/* Purchase orders (drive the approval board too) */
IF NOT EXISTS (SELECT 1 FROM dbo.purchase_orders WHERE tenant_id=@t)
    INSERT INTO dbo.purchase_orders (tenant_id, po_no, supplier_id, supplier_name, supplier_country, item_count, total, expected, status)
    SELECT @t, x.po_no, s.id, s.name, s.country_code, x.items, x.total, x.expected, x.status
    FROM (VALUES
        ('PO-5582','Anhui Knit Mills',   6,  42800, '18 Jul', 'Pending approval'),
        ('PO-5584','Bursa Denim A.S.',   8,  61200, '02 Aug', 'Pending approval'),
        ('PO-5581','Lahore Textile Co.', 12, 28400, '12 Jul', 'Approved'),
        ('PO-5580','Porto Trims Ltd.',   4,  8900,  '09 Jul', 'Approved'),
        ('PO-5578','Bursa Denim A.S.',   5,  19400, '—',      'Rejected')
      ) AS x(po_no, sup, items, total, expected, status)
    JOIN dbo.suppliers s ON s.tenant_id=@t AND s.name = x.sup;

/* Goods receipts */
IF NOT EXISTS (SELECT 1 FROM dbo.goods_receipts WHERE tenant_id=@t)
    INSERT INTO dbo.goods_receipts (tenant_id, grn_no, po_ref, supplier_name, supplier_country, line_count, received_count, status) VALUES
      (@t, 'GRN-3320', 'PO-5581', 'Lahore Textile Co.', 'PK', 12, 12, 'Complete'),
      (@t, 'GRN-3319', 'PO-5582', 'Anhui Knit Mills',   'CN', 6,  4,  'Partial'),
      (@t, 'GRN-3318', 'PO-5580', 'Porto Trims Ltd.',   'PT', 4,  0,  'Expected');

REVERT;
GO
PRINT 'Procurement demo data seeded.';
GO

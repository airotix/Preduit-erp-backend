/* PostgreSQL port of ../seed_dev_procurement.sql + ../seed_dev_procurement_detail.sql (DEV ONLY).
 * Suppliers (with contacts), purchase orders, goods receipts and receipt lines.
 * Schema adaptations: each seeded variant gets a catalog supplier price (V078), which
 * "Create PO" from a sales shortage / catalog requires. The seeded POs are display-level
 * legacy rows (no lines, no production order, no payable), as in the original. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid; g bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM suppliers WHERE tenant_id=t) THEN
    INSERT INTO suppliers (tenant_id, name, region, category, country_code, lead_time, on_time_pct, defect_rate, price_rating, score, status) VALUES
      (t,'Anhui Knit Mills','China','Knitwear · Yarn','CN','45 days',94,1.60,4.3,4.6,'Preferred'),
      (t,'Lahore Textile Co.','Pakistan','Woven · Shirting','PK','21 days',97,0.80,4.8,4.8,'Preferred'),
      (t,'Bursa Denim A.S.','Turkey','Denim','TR','38 days',88,3.10,4.1,4.1,'On watch'),
      (t,'Porto Trims Ltd.','Portugal','Trims · Hardware','PT','14 days',99,0.40,4.9,4.9,'Preferred');
  END IF;
  UPDATE suppliers SET email='sales@anhuiknit.cn', phone='+86 551 6000 1234', address='Hefei, Anhui, China'
   WHERE tenant_id=t AND name='Anhui Knit Mills';
  UPDATE suppliers SET email='orders@lahoretextile.pk', phone='+92 42 111 2222', address='Kot Lakhpat, Lahore, Pakistan'
   WHERE tenant_id=t AND name='Lahore Textile Co.';
  UPDATE suppliers SET email='satis@bursadenim.com.tr', phone='+90 224 000 1122', address='Osmangazi, Bursa, Türkiye'
   WHERE tenant_id=t AND name='Bursa Denim A.S.';
  UPDATE suppliers SET email='hello@portotrims.pt', phone='+351 22 000 3344', address='Vila Nova de Gaia, Portugal'
   WHERE tenant_id=t AND name='Porto Trims Ltd.';

  IF NOT EXISTS (SELECT 1 FROM purchase_orders WHERE tenant_id=t) THEN
    INSERT INTO purchase_orders (tenant_id, po_no, supplier_id, supplier_name, supplier_country, item_count, total, expected, status)
    SELECT t, x.po_no, s.id, s.name, s.country_code, x.items, x.total, x.expected, x.status
      FROM (VALUES
        ('PO-5582','Anhui Knit Mills',  6,42800,'18 Jul','Pending approval'),
        ('PO-5584','Bursa Denim A.S.',  8,61200,'02 Aug','Pending approval'),
        ('PO-5581','Lahore Textile Co.',12,28400,'12 Jul','Approved'),
        ('PO-5580','Porto Trims Ltd.',   4, 8900,'09 Jul','Approved'),
        ('PO-5578','Bursa Denim A.S.',   5,19400,'—',     'Rejected')) x(po_no,sup,items,total,expected,status)
      JOIN suppliers s ON s.tenant_id=t AND s.name=x.sup;
  END IF;

  INSERT INTO goods_receipts (tenant_id, grn_no, po_ref, supplier_name, supplier_country, line_count, received_count, status, received_date, location)
  SELECT t, x.grn_no, x.po_ref, x.supplier_name, x.supplier_country, x.line_count, x.received_count, x.status, x.received_date, x.location
    FROM (VALUES
      ('GRN-3320','PO-5581','Lahore Textile Co.','PK',12,12,'Complete','26 Jun 2026','Lahore DC'),
      ('GRN-3319','PO-5582','Anhui Knit Mills',  'CN', 6, 4,'Partial', '26 Jun 2026','Lahore DC'),
      ('GRN-3318','PO-5580','Porto Trims Ltd.',  'PT', 4, 0,'Expected','26 Jun 2026','Lahore DC')
    ) x(grn_no, po_ref, supplier_name, supplier_country, line_count, received_count, status, received_date, location)
   WHERE NOT EXISTS (SELECT 1 FROM goods_receipts r WHERE r.tenant_id=t AND r.grn_no=x.grn_no);

  SELECT id INTO g FROM goods_receipts WHERE tenant_id=t AND grn_no='GRN-3319' AND NOT is_deleted;
  IF g IS NOT NULL AND NOT EXISTS (SELECT 1 FROM goods_receipt_lines WHERE tenant_id=t AND grn_id=g) THEN
    INSERT INTO goods_receipt_lines (tenant_id, grn_id, name, sku, ordered, received) VALUES
      (t,g,'Merino yarn 2/28 · Navy',    'YRN-MER-NVY',800,800),
      (t,g,'Merino yarn 2/28 · Charcoal','YRN-MER-CHR',600,400),
      (t,g,'Merino yarn 2/28 · Stone',   'YRN-MER-STN',400,  0);
  END IF;

  -- Added for the current schema: catalog supplier prices (one active supplier per style).
  INSERT INTO catalog_supplier_prices (tenant_id, variant_id, supplier_id, price, currency, active)
  SELECT t, v.id, s.id, v.supplier_price, 'EUR', true
    FROM product_variants v
    JOIN products p ON p.id=v.product_id AND p.tenant_id=t
    JOIN suppliers s ON s.tenant_id=t AND s.name = CASE p.title
         WHEN 'Merino Crew Knit' THEN 'Anhui Knit Mills'
         WHEN 'Tailored Chino'   THEN 'Bursa Denim A.S.'
         WHEN 'Oxford Shirt'     THEN 'Lahore Textile Co.' END
   WHERE v.tenant_id=t AND v.supplier_price IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM catalog_supplier_prices c WHERE c.tenant_id=t AND c.variant_id=v.id AND c.supplier_id=s.id);
END $$;

/* PostgreSQL port of ../seed_dev_sales.sql + ../seed_dev_sales_detail.sql (DEV ONLY).
 * Customers (with contact details), two legacy-style orders with lines and one
 * invoice with lines. Orders keep fulfillment_tracked = false (legacy path), so no
 * stock holds are implied.
 * Schema adaptations: order lines carry colour/size/variant_id (the app resolves
 * catalog variants by these); invoices are linked to their order via order_no (V049). */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid; ml bigint; nr bigint; o1 bigint; i1 bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM customers WHERE tenant_id=t) THEN
    INSERT INTO customers (tenant_id, name, email, type, region) VALUES
      (t,'Maison Lyon','maison.lyon@b2b.fr','Wholesale','France'),
      (t,'Nordic Retail Group','buy@nordicretail.se','Wholesale','Sweden'),
      (t,'Amelia Chen','amelia.c@gmail.com','Retail','Singapore');
  END IF;
  UPDATE customers SET phone='+33 4 72 00 11 22', address='14 Rue de la République, 69002 Lyon, France'
   WHERE tenant_id=t AND name='Maison Lyon';
  UPDATE customers SET phone='+46 8 123 4567', address='Kungsgatan 12, 111 43 Stockholm, Sweden'
   WHERE tenant_id=t AND name='Nordic Retail Group';
  UPDATE customers SET phone='+65 6123 4567', address='1 Orchard Road, Singapore'
   WHERE tenant_id=t AND name='Amelia Chen';

  SELECT id INTO ml FROM customers WHERE tenant_id=t AND name='Maison Lyon';
  SELECT id INTO nr FROM customers WHERE tenant_id=t AND name='Nordic Retail Group';

  IF NOT EXISTS (SELECT 1 FROM sales_orders WHERE tenant_id=t) THEN
    INSERT INTO sales_orders (tenant_id, order_no, customer_id, customer_name, channel, item_count, total, status) VALUES
      (t,'SO-12001',ml,'Maison Lyon','Wholesale',12,4820,'Picking'),
      (t,'SO-12002',nr,'Nordic Retail Group','Wholesale',46,18240,'New');
    SELECT id INTO o1 FROM sales_orders WHERE tenant_id=t AND order_no='SO-12001';
    INSERT INTO sales_order_lines (tenant_id, order_id, sku, name, color, size, variant_id, qty, price, line_total)
    SELECT t, o1, x.sku, x.name, x.color, x.size,
           (SELECT id FROM product_variants WHERE tenant_id=t AND sku=x.sku), x.qty, x.price, x.qty*x.price
      FROM (VALUES
        ('KNT-NVY-M','Merino Crew Knit · Navy · M','Navy','M',4,129),
        ('OXF-CRM-M','Oxford Shirt · Cream · M',   'Cream','M',3,69),
        ('CHN-NVY-M','Tailored Chino · Navy · M',  'Navy','M',3,89)) x(sku,name,color,size,qty,price);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM invoices WHERE tenant_id=t) THEN
    INSERT INTO invoices (tenant_id, invoice_no, customer_id, customer_name, due_date, amount, status, order_no)
    VALUES (t,'INV-8841',ml,'Maison Lyon','14 Jul 2026',4820,'Open','SO-12001');
    SELECT id INTO i1 FROM invoices WHERE tenant_id=t AND invoice_no='INV-8841';
    INSERT INTO invoice_lines (tenant_id, invoice_id, sku, name, qty, price, line_total) VALUES
      (t,i1,'KNT-NVY-M','Merino Crew Knit · Navy · M',4,129,516),
      (t,i1,'OXF-CRM-M','Oxford Shirt · Cream · M',   3, 69,207);
  END IF;
END $$;

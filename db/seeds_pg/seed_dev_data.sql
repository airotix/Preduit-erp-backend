/* PostgreSQL port of ../seed_dev_data.sql (DEV ONLY).
 * Demo company (only if the target tenant does not exist yet), categories,
 * colours/sizes, products and SKU-level variants. Re-runnable.
 * Schema adaptation: variants also get retail/wholesale/online/supplier prices,
 * because sales orders now require a positive channel price (V077/V078). */
-- Target tenant comes from the psql variable :tenant (see run_seeds.py).
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid;
        p1 bigint; p2 bigint; p3 bigint; navy bigint; cream bigint; m bigint; l bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = t) THEN
    INSERT INTO tenants (id, name, slug, base_currency_code) VALUES (t, 'Preduit Demo', 'demo', 'EUR');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM subscriptions WHERE tenant_id = t) THEN
    INSERT INTO subscriptions (tenant_id, plan, status) VALUES (t, 'trial', 'trialing');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM categories WHERE tenant_id = t) THEN
    INSERT INTO categories (tenant_id, name) VALUES (t,'Knitwear'), (t,'Bottoms'), (t,'Shirts');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM attribute_values WHERE tenant_id = t AND code IN ('NVY','CRM')) THEN
    INSERT INTO attribute_values (tenant_id, attr_type, value, code) VALUES
      (t,'Color','Navy','NVY'), (t,'Color','Cream','CRM');
  END IF;
  INSERT INTO attribute_values (tenant_id, attr_type, value, code)
    SELECT t, 'Size', s, s FROM (VALUES ('M'),('L')) x(s)
     WHERE NOT EXISTS (SELECT 1 FROM attribute_values a WHERE a.tenant_id=t AND a.attr_type='Size' AND a.code=x.s);
  IF NOT EXISTS (SELECT 1 FROM products WHERE tenant_id = t) THEN
    INSERT INTO products (tenant_id, title, category_id, season, status) VALUES
      (t,'Merino Crew Knit',(SELECT id FROM categories WHERE tenant_id=t AND name='Knitwear'),'Fall ''26','Active'),
      (t,'Tailored Chino',  (SELECT id FROM categories WHERE tenant_id=t AND name='Bottoms'),  'Core',     'Active'),
      (t,'Oxford Shirt',    (SELECT id FROM categories WHERE tenant_id=t AND name='Shirts'),   'Spring ''26','Draft');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM product_variants WHERE tenant_id = t) THEN
    SELECT id INTO p1 FROM products WHERE tenant_id=t AND title='Merino Crew Knit';
    SELECT id INTO p2 FROM products WHERE tenant_id=t AND title='Tailored Chino';
    SELECT id INTO p3 FROM products WHERE tenant_id=t AND title='Oxford Shirt';
    SELECT id INTO navy  FROM attribute_values WHERE tenant_id=t AND code='NVY';
    SELECT id INTO cream FROM attribute_values WHERE tenant_id=t AND code='CRM';
    SELECT id INTO m FROM attribute_values WHERE tenant_id=t AND attr_type='Size' AND code='M';
    SELECT id INTO l FROM attribute_values WHERE tenant_id=t AND attr_type='Size' AND code='L';
    INSERT INTO product_variants (tenant_id, product_id, sku, color_id, size_id, price, retail_price,
                                  wholesale_price, online_price, supplier_price, currency_code, status) VALUES
      (t,p1,'KNT-NVY-M',navy, m,129,129,110,135,38,'EUR','Active'),
      (t,p1,'KNT-NVY-L',navy, l,129,129,110,135,38,'EUR','Active'),
      (t,p1,'KNT-CRM-M',cream,m,129,129,110,135,38,'EUR','Active'),
      (t,p2,'CHN-NVY-M',navy, m, 89, 89, 75, 94,24,'EUR','Active'),
      (t,p2,'CHN-NVY-L',navy, l, 89, 89, 75, 94,24,'EUR','Active'),
      (t,p3,'OXF-CRM-M',cream,m, 69, 69, 58, 72,18,'EUR','Active');
  END IF;
END $$;

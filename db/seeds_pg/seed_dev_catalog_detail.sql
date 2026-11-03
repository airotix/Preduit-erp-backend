/* PostgreSQL port of ../seed_dev_catalog_detail.sql (DEV ONLY).
 * Colour swatches, size ordering, per-variant on-hand (drives the matrix) and
 * product specs. (The stray INFORMATION_SCHEMA SELECTs in the original were dropped.) */
-- Target tenant comes from the psql variable :tenant (see run_seeds.py).
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid;
BEGIN
  UPDATE attribute_values SET hex='#262B3F' WHERE tenant_id=t AND code='NVY';
  UPDATE attribute_values SET hex='#E8E2D0' WHERE tenant_id=t AND code='CRM';
  UPDATE product_variants v SET qty_on_hand = x.q
    FROM (VALUES ('KNT-NVY-M',120),('KNT-NVY-L',64),('KNT-CRM-M',18),
                 ('CHN-NVY-M',42),('CHN-NVY-L',0),('OXF-CRM-M',30)) x(sku,q)
   WHERE v.tenant_id=t AND v.sku=x.sku;
  UPDATE products SET composition='100% Extrafine Merino Wool', gauge='12gg',
         care='Hand wash cold · Dry flat', origin='Made in Pakistan', hs_code='6110.11.00', weight='320 g'
   WHERE tenant_id=t AND title='Merino Crew Knit';
  UPDATE products SET composition='98% Cotton · 2% Elastane', gauge='—',
         care='Machine wash 30°', origin='Made in Portugal', hs_code='6203.42.00', weight='410 g'
   WHERE tenant_id=t AND title='Tailored Chino';
  UPDATE products SET composition='100% Organic Cotton', gauge='—',
         care='Machine wash 40°', origin='Made in Türkiye', hs_code='6205.20.00', weight='260 g'
   WHERE tenant_id=t AND title='Oxford Shirt';
END $$;

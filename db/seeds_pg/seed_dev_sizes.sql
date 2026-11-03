/* PostgreSQL port of ../seed_dev_sizes.sql (DEV ONLY). Full apparel size scale
 * XS..7XL; inserts missing sizes and fixes sort_order on existing ones. */
-- Target tenant comes from the psql variable :tenant (see run_seeds.py).
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid;
BEGIN
  INSERT INTO attribute_values (tenant_id, attr_type, value, code, sort_order)
  SELECT t, 'Size', s.v, s.v, s.o
    FROM (VALUES ('XS',1),('S',2),('M',3),('L',4),('XL',5),('2XL',6),('3XL',7),
                 ('4XL',8),('5XL',9),('6XL',10),('7XL',11)) s(v,o)
   WHERE NOT EXISTS (SELECT 1 FROM attribute_values a
                      WHERE a.tenant_id=t AND a.attr_type='Size' AND a.code=s.v);
  UPDATE attribute_values a SET sort_order = s.o
    FROM (VALUES ('XS',1),('S',2),('M',3),('L',4),('XL',5),('2XL',6),('3XL',7),
                 ('4XL',8),('5XL',9),('6XL',10),('7XL',11)) s(v,o)
   WHERE a.tenant_id=t AND a.attr_type='Size' AND a.code=s.v;
END $$;

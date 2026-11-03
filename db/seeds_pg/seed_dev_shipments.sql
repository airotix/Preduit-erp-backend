/* PostgreSQL port of ../seed_dev_shipments.sql (DEV ONLY). Carriers and four legacy
 * outbound shipments (order refs point at orders that are not in this seed, as in the
 * original) with lines for SHP-9912. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid; s bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM carriers WHERE tenant_id=t) THEN
    INSERT INTO carriers (tenant_id, name, service, avg_transit, on_time_pct, status) VALUES
      (t,'DHL Express','Air · Express','2–3 days',96,'Active'),
      (t,'FedEx','Air · Priority','3–4 days',93,'Active'),
      (t,'Maersk LCL','Sea · LCL','18–24 days',88,'Active'),
      (t,'Aramex','Air · Standard','4–6 days',90,'Active');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM shipments WHERE tenant_id=t) THEN
    INSERT INTO shipments (tenant_id, shipment_no, order_ref, carrier, destination, status, eta) VALUES
      (t,'SHP-9912','#SO-12353','DHL Express','Paris, FR',    'In transit',   '29 Jun'),
      (t,'SHP-9911','#SO-12348','FedEx',      'Lisbon, PT',   'Delivered',    '25 Jun'),
      (t,'SHP-9910','#SO-12350','Maersk LCL', 'Stockholm, SE','Customs',      '04 Jul'),
      (t,'SHP-9909','#SO-12351','Aramex',     'Singapore, SG','Label created','01 Jul');
    SELECT id INTO s FROM shipments WHERE tenant_id=t AND shipment_no='SHP-9912';
    INSERT INTO shipment_lines (tenant_id, shipment_id, sku, description, qty) VALUES
      (t,s,'KNT-NVY-M','Merino Crew Knit · Navy · M',4),
      (t,s,'OXF-CRM-M','Oxford Shirt · Cream · M',3),
      (t,s,'CHN-NVY-M','Tailored Chino · Navy · M',3);
  END IF;
END $$;

/* PostgreSQL port of ../seed_dev_inventory.sql (DEV ONLY).
 * Locations, stock, two transfers and a reorder alert.
 * Schema adaptations (V072-V075):
 *   - reserved = 0: holds are now order-owned rows (order_stock_reservations); the
 *     original's 20/10 "reserved" units had no owning order and would block transfers.
 *   - every stock row gets an 'Opening balance' ledger entry, as V075 does, so the
 *     movement history and before/after balances are consistent.
 *   - reorder alert is linked to its variant. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid; lhe bigint; dxb bigint; khi bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM locations WHERE tenant_id=t) THEN
    INSERT INTO locations (tenant_id, name, code, type, region, capacity) VALUES
      (t,'Lahore DC','WH-LHE-01','Warehouse','Pakistan',2000),
      (t,'Karachi DC','WH-KHI-02','Warehouse','Pakistan',1500),
      (t,'Dubai DC','WH-DXB-01','Warehouse','UAE',1200),
      (t,'Flagship Store','RT-LHE-01','Retail','Pakistan',400);
  END IF;
  SELECT id INTO lhe FROM locations WHERE tenant_id=t AND name='Lahore DC';
  SELECT id INTO dxb FROM locations WHERE tenant_id=t AND name='Dubai DC';
  SELECT id INTO khi FROM locations WHERE tenant_id=t AND name='Karachi DC';

  IF NOT EXISTS (SELECT 1 FROM stock_levels WHERE tenant_id=t) THEN
    INSERT INTO stock_levels (tenant_id, variant_id, location_id, on_hand, reserved)
    SELECT t, v.id, lhe,
           CASE v.sku WHEN 'KNT-NVY-M' THEN 120 WHEN 'KNT-NVY-L' THEN 64 WHEN 'KNT-CRM-M' THEN 18
                      WHEN 'CHN-NVY-M' THEN 42  WHEN 'CHN-NVY-L' THEN 0  WHEN 'OXF-CRM-M' THEN 30 ELSE 0 END, 0
      FROM product_variants v WHERE v.tenant_id=t;
  END IF;

  INSERT INTO inventory_stock_entries
      (tenant_id, request_id, kind, reference, reason, actor, variant_id, location_id,
       qty_delta, before_qty, after_qty, quantity, source_module, source_type, source_ref,
       article_name, variant_sku, color_name, size_name, location_name)
  SELECT st.tenant_id, gen_random_uuid(), 'Opening balance', 'Demo seed opening',
         'Demo seed opening stock', 'Seed', st.variant_id, st.location_id,
         st.on_hand, 0, st.on_hand, st.on_hand, 'inventory', 'opening', 'Demo seed opening',
         p.title, v.sku, c.value, z.value, l.name
    FROM stock_levels st
    JOIN product_variants v ON v.id=st.variant_id AND v.tenant_id=st.tenant_id
    JOIN products p ON p.id=v.product_id AND p.tenant_id=st.tenant_id
    JOIN locations l ON l.id=st.location_id AND l.tenant_id=st.tenant_id
    LEFT JOIN attribute_values c ON c.id=v.color_id AND c.tenant_id=st.tenant_id
    LEFT JOIN attribute_values z ON z.id=v.size_id AND z.tenant_id=st.tenant_id
   WHERE st.tenant_id=t
  ON CONFLICT DO NOTHING;

  IF NOT EXISTS (SELECT 1 FROM stock_transfers WHERE tenant_id=t) THEN
    INSERT INTO stock_transfers (tenant_id, transfer_no, from_location_id, to_location_id, units, status, eta) VALUES
      (t,'TRF-2041',lhe,dxb,1200,'In transit','02 Jul'),
      (t,'TRF-2040',khi,lhe, 640,'Received',  '24 Jun');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM reorder_alerts WHERE tenant_id=t) THEN
    INSERT INTO reorder_alerts (tenant_id, variant_id, sku, available, reorder_point, suggested, supplier, severity)
    VALUES (t,(SELECT id FROM product_variants WHERE tenant_id=t AND sku='CHN-NVY-L'),
            'CHN-NVY-L',0,40,200,'Anhui Knit Mills','Critical');
  END IF;
END $$;

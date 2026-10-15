-- Record physical movements and preserve their identities and source documents.
BEGIN;
SET LOCAL app.rls_bypass = 'true';
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS source_module VARCHAR(24);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS source_type VARCHAR(40);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS source_id UUID;
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS source_ref VARCHAR(80);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS batch_id UUID;
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS article_name VARCHAR(200);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS variant_sku VARCHAR(64);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS color_name VARCHAR(60);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS size_name VARCHAR(60);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS location_name VARCHAR(120);
ALTER TABLE inventory_stock_entries ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 0;
DO $$ DECLARE constraint_name text;
BEGIN
    FOR constraint_name IN SELECT conname FROM pg_constraint
      WHERE conrelid = 'inventory_stock_entries'::regclass AND contype = 'c'
        AND (pg_get_constraintdef(oid) LIKE '%kind%' OR pg_get_constraintdef(oid) LIKE '%qty_delta <> 0%')
    LOOP
        EXECUTE format('ALTER TABLE inventory_stock_entries DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END $$;
ALTER TABLE inventory_stock_entries ADD CONSTRAINT ck_inventory_movement_kind CHECK
    (kind IN ('Opening balance', 'Receipt', 'Adjustment', 'Transfer out', 'Transfer in',
             'Dispatch', 'Return to QC', 'Write off', 'Return to supplier'));
ALTER TABLE inventory_stock_entries ADD CONSTRAINT ck_inventory_movement_delta CHECK
    (qty_delta <> 0 OR kind IN ('Opening balance', 'Return to QC'));

-- Preserve existing recorded movements; their old labels can only be populated
-- from current master data once because the earlier workflow kept no snapshots.
UPDATE inventory_stock_entries e SET article_name = p.title, variant_sku = v.sku,
    color_name = c.value, size_name = z.value, location_name = l.name,
    quantity = abs(e.qty_delta), source_module = 'inventory',
    source_type = CASE WHEN e.kind = 'Receipt' THEN 'stock_receipt' ELSE 'count' END,
    source_id = e.request_id, source_ref = e.reference
  FROM product_variants v JOIN products p ON p.id = v.product_id AND p.tenant_id = v.tenant_id
    LEFT JOIN attribute_values c ON c.id = v.color_id AND c.tenant_id = v.tenant_id
    LEFT JOIN attribute_values z ON z.id = v.size_id AND z.tenant_id = v.tenant_id,
    locations l
 WHERE e.variant_id = v.id AND e.tenant_id = v.tenant_id AND e.location_id = l.id
   AND e.tenant_id = l.tenant_id AND e.variant_sku IS NULL;
UPDATE inventory_stock_entries e SET source_module = 'procurement', source_type = 'receipt',
    source_id = g.public_id, source_ref = g.grn_no
  FROM goods_receipts g WHERE g.tenant_id = e.tenant_id AND g.request_id = e.request_id
    AND e.kind = 'Receipt' AND e.source_type = 'stock_receipt';

CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_ledger_opening
    ON inventory_stock_entries (tenant_id, variant_id, location_id) WHERE kind = 'Opening balance';
INSERT INTO inventory_stock_entries
    (tenant_id, request_id, kind, reference, reason, actor, variant_id, location_id,
     qty_delta, before_qty, after_qty, quantity, source_module, source_type, source_ref,
     article_name, variant_sku, color_name, size_name, location_name)
SELECT st.tenant_id, gen_random_uuid(), 'Opening balance', 'V075 ledger opening',
    'Warehouse balance at ledger migration. Earlier movement history may be incomplete.',
    'System migration', st.variant_id, st.location_id, st.on_hand, 0, st.on_hand, st.on_hand,
    'inventory', 'opening', 'V075 ledger opening', p.title, v.sku, c.value, z.value, l.name
  FROM stock_levels st JOIN product_variants v ON v.id = st.variant_id AND v.tenant_id = st.tenant_id
    JOIN products p ON p.id = v.product_id AND p.tenant_id = st.tenant_id
    JOIN locations l ON l.id = st.location_id AND l.tenant_id = st.tenant_id
    LEFT JOIN attribute_values c ON c.id = v.color_id AND c.tenant_id = st.tenant_id
    LEFT JOIN attribute_values z ON z.id = v.size_id AND z.tenant_id = st.tenant_id
ON CONFLICT DO NOTHING;
CREATE INDEX IF NOT EXISTS ix_inventory_movement_source ON inventory_stock_entries (tenant_id, source_id);
CREATE INDEX IF NOT EXISTS ix_inventory_movement_request ON inventory_stock_entries (tenant_id, request_id);
CREATE INDEX IF NOT EXISTS ix_inventory_movement_location_time ON inventory_stock_entries (tenant_id, location_id, created_at DESC);

CREATE OR REPLACE FUNCTION protect_inventory_movement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('app.rls_bypass', true) = 'true' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'Posted inventory movements cannot be edited or deleted. Record a correcting movement.';
END $$;
DROP TRIGGER IF EXISTS protect_inventory_movement ON inventory_stock_entries;
CREATE TRIGGER protect_inventory_movement BEFORE UPDATE OR DELETE ON inventory_stock_entries
    FOR EACH ROW EXECUTE FUNCTION protect_inventory_movement();
REVOKE UPDATE, DELETE ON inventory_stock_entries FROM erp_app;
COMMIT;

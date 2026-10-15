-- Record manual stock receipts and count corrections by exact variant/location.
BEGIN;
CREATE TABLE IF NOT EXISTS inventory_stock_entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    request_id UUID NOT NULL,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('Receipt', 'Adjustment')),
    reference VARCHAR(80) NOT NULL,
    reason VARCHAR(400) NOT NULL,
    actor VARCHAR(256),
    variant_id BIGINT NOT NULL REFERENCES product_variants(id),
    location_id BIGINT NOT NULL REFERENCES locations(id),
    qty_delta INTEGER NOT NULL CHECK (qty_delta <> 0),
    before_qty INTEGER NOT NULL CHECK (before_qty >= 0),
    after_qty INTEGER NOT NULL CHECK (after_qty >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, request_id, variant_id, location_id),
    CHECK (after_qty = before_qty + qty_delta)
);
CREATE INDEX IF NOT EXISTS ix_inventory_stock_entries_tenant_time
    ON inventory_stock_entries (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_inventory_stock_entries_variant_location
    ON inventory_stock_entries (variant_id, location_id);
ALTER TABLE inventory_stock_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_stock_entries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON inventory_stock_entries;
CREATE POLICY tenant_isolation ON inventory_stock_entries
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON inventory_stock_entries TO erp_app;
GRANT ALL ON inventory_stock_entries TO erp_system;
GRANT USAGE, SELECT ON SEQUENCE inventory_stock_entries_id_seq TO erp_app, erp_system;
COMMIT;

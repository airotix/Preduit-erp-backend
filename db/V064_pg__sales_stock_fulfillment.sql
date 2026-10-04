-- Existing orders retain their legacy fulfillment behavior.
BEGIN;
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS fulfillment_tracked BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sales_order_lines ADD COLUMN IF NOT EXISTS variant_id BIGINT REFERENCES product_variants(id);
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS sales_order_id BIGINT REFERENCES sales_orders(id);
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS inventory_received BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS sales_order_line_id BIGINT REFERENCES sales_order_lines(id);
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS sales_order_id BIGINT REFERENCES sales_orders(id);
CREATE INDEX IF NOT EXISTS ix_purchase_sales_order ON purchase_orders (tenant_id, sales_order_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_shipment_sales_order ON shipments (tenant_id, sales_order_id)
    WHERE sales_order_id IS NOT NULL AND NOT is_deleted;
CREATE TABLE IF NOT EXISTS order_stock_reservations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    order_id BIGINT NOT NULL REFERENCES sales_orders(id),
    order_line_id BIGINT NOT NULL REFERENCES sales_order_lines(id),
    stock_level_id BIGINT NOT NULL REFERENCES stock_levels(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    UNIQUE (order_line_id, stock_level_id)
);
CREATE INDEX IF NOT EXISTS ix_order_reservations ON order_stock_reservations (tenant_id, order_id);
ALTER TABLE order_stock_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_stock_reservations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON order_stock_reservations;
CREATE POLICY tenant_isolation ON order_stock_reservations
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON order_stock_reservations TO erp_app;
GRANT ALL ON order_stock_reservations TO erp_system;
GRANT USAGE, SELECT ON SEQUENCE order_stock_reservations_id_seq TO erp_app, erp_system;
COMMIT;

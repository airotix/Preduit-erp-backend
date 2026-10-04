-- PostgreSQL: new production work originates from procurement.
-- Existing production/TNA records are retained without inferred links/backfill.
BEGIN;
ALTER TABLE production_orders
    ADD COLUMN IF NOT EXISTS purchase_order_id BIGINT REFERENCES purchase_orders(id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_production_purchase_order
    ON production_orders (purchase_order_id) WHERE purchase_order_id IS NOT NULL;
ALTER TABLE purchase_order_lines
    ADD COLUMN IF NOT EXISTS production_line_id BIGINT REFERENCES production_order_lines(id);
CREATE INDEX IF NOT EXISTS ix_purchase_production_line
    ON purchase_order_lines (tenant_id, production_line_id);
COMMIT;

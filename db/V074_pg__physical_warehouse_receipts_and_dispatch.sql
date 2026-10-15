-- Warehouse receipt is a physical confirmation; outbound stock leaves at dispatch.
BEGIN;
SET LOCAL app.rls_bypass = 'true';

ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS location_id BIGINT REFERENCES locations(id);
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS request_id UUID;
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS request_payload TEXT;
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS reference VARCHAR(80);
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS reason VARCHAR(400);
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS actor VARCHAR(256);
CREATE UNIQUE INDEX IF NOT EXISTS ux_goods_receipts_tenant_request
    ON goods_receipts (tenant_id, request_id) WHERE request_id IS NOT NULL;
ALTER TABLE goods_receipt_lines ADD COLUMN IF NOT EXISTS po_line_id BIGINT REFERENCES purchase_order_lines(id);
ALTER TABLE goods_receipt_lines ADD COLUMN IF NOT EXISTS variant_id BIGINT REFERENCES product_variants(id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_goods_receipt_po_line
    ON goods_receipt_lines (tenant_id, grn_id, po_line_id) WHERE po_line_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_goods_receipt_lines_po_line ON goods_receipt_lines (tenant_id, po_line_id);

ALTER TABLE shipments ADD COLUMN IF NOT EXISTS inventory_dispatched BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;

-- Delivered shipments were already relieved by the old workflow. Never deduct them again.
UPDATE shipments SET inventory_dispatched = true WHERE status = 'Delivered';

-- Existing normal shipments already in transit still have stock held at the warehouse
-- under the old rules. Relieve those known holds once, only when every quantity reconciles.
CREATE TEMP TABLE migration_dispatch_orders ON COMMIT DROP AS
SELECT DISTINCT so.id, so.tenant_id
  FROM sales_orders so JOIN shipments s ON s.sales_order_id = so.id AND s.tenant_id = so.tenant_id
 WHERE so.fulfillment_tracked AND NOT so.is_deleted AND NOT s.is_deleted AND NOT s.batch_managed
   AND NOT s.inventory_dispatched AND s.status IN ('Dispatched', 'In transit', 'Customs', 'Out for delivery');
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM migration_dispatch_orders m JOIN sales_orders so ON so.id = m.id
         WHERE so.status IN ('Cancelled', 'Inactive')
            OR EXISTS (
                SELECT 1 FROM sales_order_lines l
                 WHERE l.order_id = m.id AND l.tenant_id = m.tenant_id
                   AND l.qty <> COALESCE((SELECT sum(r.qty) FROM order_stock_reservations r
                                          WHERE r.order_line_id = l.id AND r.tenant_id = m.tenant_id), 0))
            OR NOT EXISTS (SELECT 1 FROM order_stock_reservations r WHERE r.order_id = m.id AND r.tenant_id = m.tenant_id)
    ) THEN
        RAISE EXCEPTION 'An existing in-transit shipment has unreconciled order reservations. Reconcile it before applying V074.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stock_levels st JOIN (
            SELECT r.stock_level_id, sum(r.qty) qty FROM order_stock_reservations r
              JOIN migration_dispatch_orders m ON m.id = r.order_id AND m.tenant_id = r.tenant_id
             GROUP BY r.stock_level_id
        ) moved ON moved.stock_level_id = st.id WHERE st.on_hand < moved.qty OR st.reserved < moved.qty
    ) THEN
        RAISE EXCEPTION 'An existing in-transit shipment exceeds warehouse balances. Reconcile it before applying V074.';
    END IF;
END $$;
UPDATE stock_levels st SET on_hand = st.on_hand - moved.qty, reserved = st.reserved - moved.qty
 FROM (SELECT r.stock_level_id, sum(r.qty)::int qty FROM order_stock_reservations r
         JOIN migration_dispatch_orders m ON m.id = r.order_id AND m.tenant_id = r.tenant_id
        GROUP BY r.stock_level_id) moved WHERE moved.stock_level_id = st.id;
UPDATE product_variants v SET qty_on_hand = COALESCE((SELECT sum(st.on_hand) FROM stock_levels st
    WHERE st.variant_id = v.id AND st.tenant_id = v.tenant_id), 0)
 WHERE v.id IN (SELECT st.variant_id FROM stock_levels st JOIN order_stock_reservations r ON r.stock_level_id = st.id
    JOIN migration_dispatch_orders m ON m.id = r.order_id AND m.tenant_id = r.tenant_id);
DELETE FROM order_stock_reservations r USING migration_dispatch_orders m WHERE r.order_id = m.id AND r.tenant_id = m.tenant_id;
UPDATE shipments s SET inventory_dispatched = true, dispatched_at = now()
 FROM migration_dispatch_orders m WHERE s.sales_order_id = m.id AND s.tenant_id = m.tenant_id
   AND NOT s.is_deleted AND NOT s.batch_managed AND s.status IN ('Dispatched', 'In transit', 'Customs', 'Out for delivery');
UPDATE sales_orders so SET status = 'Shipped' FROM migration_dispatch_orders m WHERE so.id = m.id AND so.tenant_id = m.tenant_id;

-- A generated pending shipment means ready to ship, not physically shipped.
UPDATE sales_orders so SET status = 'Packed'
 WHERE so.fulfillment_tracked AND NOT so.is_deleted AND so.status = 'Shipped'
   AND EXISTS (SELECT 1 FROM shipments s WHERE s.sales_order_id = so.id AND s.tenant_id = so.tenant_id
               AND NOT s.is_deleted AND NOT s.batch_managed AND NOT s.inventory_dispatched
               AND s.status IN ('Pending', 'Label created'))
   AND NOT EXISTS (SELECT 1 FROM shipments s WHERE s.sales_order_id = so.id AND s.tenant_id = so.tenant_id
                   AND NOT s.is_deleted AND (s.inventory_dispatched OR s.batch_managed));
COMMIT;

BEGIN;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS supplier_shipment_required boolean NOT NULL DEFAULT false;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS purchase_order_id bigint REFERENCES purchase_orders(id);
ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS po_line_id bigint REFERENCES purchase_order_lines(id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_po_shipment ON shipments (tenant_id, purchase_order_id)
    WHERE purchase_order_id IS NOT NULL AND NOT is_deleted AND status <> 'Cancelled' AND NOT batch_managed;
COMMIT;

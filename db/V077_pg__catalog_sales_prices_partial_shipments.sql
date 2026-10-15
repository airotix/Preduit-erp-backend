BEGIN;
SET LOCAL app.rls_bypass = 'true';
ALTER TABLE sales_orders ADD COLUMN allow_partial_shipments BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE shipments ADD COLUMN packed_at TIMESTAMPTZ;
ALTER TABLE shipments ADD COLUMN packed_by VARCHAR(256);
ALTER TABLE shipment_lines ADD COLUMN order_line_id BIGINT REFERENCES sales_order_lines(id);
CREATE INDEX ix_shipment_order_line ON shipment_lines(tenant_id,order_line_id);
-- Match legacy contents only where the original identity is unambiguous.
WITH candidates AS (
 SELECT sl.id, min(ol.id) AS order_line_id
 FROM shipment_lines sl JOIN shipments s ON s.id=sl.shipment_id AND s.tenant_id=sl.tenant_id
 JOIN sales_order_lines ol ON ol.order_id=s.sales_order_id AND ol.tenant_id=s.tenant_id
 WHERE NOT s.batch_managed AND sl.description=concat_ws(' · ',nullif(ol.name,''),nullif(ol.color,''),nullif(ol.size,''))
 GROUP BY sl.id HAVING count(*)=1
) UPDATE shipment_lines sl SET order_line_id=c.order_line_id FROM candidates c WHERE sl.id=c.id;
-- Existing dispatches retain their physical history; pending shipments require
-- explicit packing confirmation after this migration.
UPDATE shipments SET packed_at=coalesce(dispatched_at,delivered_at,now()),packed_by='Legacy dispatched shipment'
 WHERE inventory_dispatched AND NOT batch_managed;
UPDATE sales_orders o SET status='Ready to pack' WHERE o.status='Packed' AND o.fulfillment_tracked
 AND NOT EXISTS(SELECT 1 FROM shipments s WHERE s.tenant_id=o.tenant_id AND s.sales_order_id=o.id AND s.batch_managed)
 AND NOT EXISTS(SELECT 1 FROM production_orders p JOIN production_batches b ON b.order_id=p.id AND b.tenant_id=p.tenant_id
   LEFT JOIN purchase_orders po ON po.id=p.purchase_order_id AND po.tenant_id=p.tenant_id
   WHERE p.tenant_id=o.tenant_id AND coalesce(p.sales_order_id,po.sales_order_id)=o.id);
COMMIT;

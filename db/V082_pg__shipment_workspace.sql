BEGIN;

ALTER TABLE shipments ADD COLUMN IF NOT EXISTS origin varchar(160);
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS tracking_ref varchar(120);
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS dispatch_on varchar(40);
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS legacy_ambiguous boolean NOT NULL DEFAULT false;

ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS delivered_qty integer;
ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS discrepancy_qty integer NOT NULL DEFAULT 0;
ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS discrepancy_status varchar(24);
ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS discrepancy_reason varchar(400);
ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS discrepancy_resolved_by varchar(256);
ALTER TABLE shipment_lines ADD COLUMN IF NOT EXISTS discrepancy_resolved_at timestamptz;

ALTER TABLE shipment_inbound_allocations ADD COLUMN IF NOT EXISTS discrepancy_qty integer NOT NULL DEFAULT 0;
ALTER TABLE shipment_inbound_allocations ADD COLUMN IF NOT EXISTS discrepancy_status varchar(24);
ALTER TABLE shipment_inbound_allocations ADD COLUMN IF NOT EXISTS discrepancy_reason varchar(400);
ALTER TABLE shipment_inbound_allocations ADD COLUMN IF NOT EXISTS discrepancy_resolved_by varchar(256);
ALTER TABLE shipment_inbound_allocations ADD COLUMN IF NOT EXISTS discrepancy_resolved_at timestamptz;

-- Historical customer deliveries have no recorded arrival quantities.
-- Leave them flagged; do not invent delivered or missing units.
UPDATE shipments AS s
SET legacy_ambiguous = true
WHERE s.status = 'Delivered'
  AND s.direction = 'Outbound'
  AND s.is_deleted = false
  AND NOT EXISTS (
      SELECT 1 FROM shipment_lines AS line
      WHERE line.shipment_id = s.id AND line.delivered_qty IS NOT NULL
  );

COMMIT;

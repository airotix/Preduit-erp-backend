-- Preserve existing batches and outbound shipment history; add batch TNA and inbound receipts.
BEGIN;
ALTER TABLE production_batch_stages ADD COLUMN IF NOT EXISTS start_on DATE;
ALTER TABLE production_batch_stages ADD COLUMN IF NOT EXISTS end_on DATE;
ALTER TABLE production_batch_stages ADD COLUMN IF NOT EXISTS duration_days INTEGER NOT NULL DEFAULT 1;
ALTER TABLE production_batch_stages ADD COLUMN IF NOT EXISTS actual_start_on DATE;
ALTER TABLE production_batch_stages ADD COLUMN IF NOT EXISTS actual_end_on DATE;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS direction VARCHAR(12) NOT NULL DEFAULT 'Outbound';

CREATE TABLE IF NOT EXISTS shipment_inbound_allocations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    shipment_id BIGINT NOT NULL REFERENCES shipments(id),
    shipment_line_id BIGINT NOT NULL REFERENCES shipment_lines(id),
    line_id BIGINT NOT NULL REFERENCES production_batch_lines(id),
    location_id BIGINT NOT NULL REFERENCES locations(id),
    qty INTEGER NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'Reserved',
    UNIQUE (shipment_id, line_id),
    CHECK (qty > 0 AND accepted >= 0 AND accepted <= qty)
);
CREATE INDEX IF NOT EXISTS ix_shipment_inbound_allocations_shipment_id ON shipment_inbound_allocations(shipment_id);
CREATE INDEX IF NOT EXISTS ix_shipment_inbound_allocations_tenant_id ON shipment_inbound_allocations(tenant_id);
CREATE INDEX IF NOT EXISTS ix_shipment_inbound_allocations_line_id ON shipment_inbound_allocations(line_id);
ALTER TABLE shipment_inbound_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE shipment_inbound_allocations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON shipment_inbound_allocations;
CREATE POLICY tenant_isolation ON shipment_inbound_allocations
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON shipment_inbound_allocations TO erp_app;
GRANT ALL ON shipment_inbound_allocations TO erp_system;
GRANT USAGE, SELECT ON SEQUENCE shipment_inbound_allocations_id_seq TO erp_app, erp_system;
COMMIT;

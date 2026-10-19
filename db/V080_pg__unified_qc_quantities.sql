-- Unified QC quantity reservations. Does not replay or reinterpret old results.
BEGIN;

ALTER TABLE inspections ADD COLUMN IF NOT EXISTS sampling_mode VARCHAR(16);
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS exception_reason VARCHAR(400);
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS legacy_clearance BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS batch_id BIGINT REFERENCES production_batches(id);
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS release_decision VARCHAR(24);

-- Ordinary Pass/Fail rows that never stored a quantity disposition stay legacy.
-- They are not assigned approved/held/rejected quantities here.
UPDATE inspections
   SET legacy_clearance = TRUE
 WHERE batch_line_id IS NULL
   AND batch_id IS NULL
   AND approved_qty IS NULL
   AND result IN ('Pass', 'Fail');

CREATE TABLE IF NOT EXISTS qc_line_balances (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    production_line_id BIGINT NOT NULL REFERENCES production_order_lines(id),
    approved INT NOT NULL DEFAULT 0 CHECK (approved >= 0),
    held INT NOT NULL DEFAULT 0 CHECK (held >= 0),
    rejected INT NOT NULL DEFAULT 0 CHECK (rejected >= 0),
    UNIQUE (tenant_id, production_line_id)
);

CREATE TABLE IF NOT EXISTS qc_reservations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    inspection_id BIGINT NOT NULL REFERENCES inspections(id),
    production_line_id BIGINT REFERENCES production_order_lines(id),
    batch_line_id BIGINT REFERENCES production_batch_lines(id),
    qty INT NOT NULL CHECK (qty > 0),
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('awaiting', 'held')),
    CHECK ((production_line_id IS NULL) <> (batch_line_id IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_qc_reservations_inspection ON qc_reservations (inspection_id);

CREATE TABLE IF NOT EXISTS qc_commands (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    request_id UUID NOT NULL,
    result TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, request_id)
);

ALTER TABLE qc_line_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE qc_line_balances FORCE ROW LEVEL SECURITY;
ALTER TABLE qc_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE qc_reservations FORCE ROW LEVEL SECURITY;
ALTER TABLE qc_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE qc_commands FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON qc_line_balances;
CREATE POLICY tenant_isolation ON qc_line_balances
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
DROP POLICY IF EXISTS tenant_isolation ON qc_reservations;
CREATE POLICY tenant_isolation ON qc_reservations
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
DROP POLICY IF EXISTS tenant_isolation ON qc_commands;
CREATE POLICY tenant_isolation ON qc_commands
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON qc_line_balances, qc_reservations, qc_commands TO erp_app;
GRANT ALL ON qc_line_balances, qc_reservations, qc_commands TO erp_system;
GRANT USAGE, SELECT ON SEQUENCE qc_line_balances_id_seq, qc_reservations_id_seq, qc_commands_id_seq TO erp_app, erp_system;

COMMIT;

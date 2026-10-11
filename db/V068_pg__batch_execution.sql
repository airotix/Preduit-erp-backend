-- Batch execution, QC disposition, lot inventory and many-to-many dispatch allocations.

BEGIN;

DROP INDEX IF EXISTS uq_shipment_sales_order;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS batch_managed BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_shipment_sales_order ON shipments (tenant_id, sales_order_id)
    WHERE sales_order_id IS NOT NULL AND NOT is_deleted AND NOT batch_managed;


CREATE TABLE IF NOT EXISTS production_batches (
	order_id BIGINT NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	factory VARCHAR(120), 
	due_on VARCHAR(10), 
	status VARCHAR(24) NOT NULL, 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(order_id) REFERENCES production_orders (id), 
	UNIQUE (public_id)
)

;

CREATE INDEX IF NOT EXISTS ix_production_batches_order_id ON production_batches (order_id);

CREATE INDEX IF NOT EXISTS ix_production_batches_tenant_id ON production_batches (tenant_id);

ALTER TABLE production_batches ENABLE ROW LEVEL SECURITY;

ALTER TABLE production_batches FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON production_batches;
CREATE POLICY tenant_isolation ON production_batches
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON production_batches TO erp_app;

GRANT ALL ON production_batches TO erp_system;

GRANT USAGE, SELECT ON SEQUENCE production_batches_id_seq TO erp_app, erp_system;


CREATE TABLE IF NOT EXISTS production_batch_events (
	batch_id BIGINT, 
	request_id UUID NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	actor VARCHAR(120), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	payload TEXT NOT NULL, 
	result TEXT NOT NULL, 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (tenant_id, request_id), 
	FOREIGN KEY(batch_id) REFERENCES production_batches (id), 
	UNIQUE (public_id)
)

;

CREATE INDEX IF NOT EXISTS ix_production_batch_events_tenant_id ON production_batch_events (tenant_id);

ALTER TABLE production_batch_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE production_batch_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON production_batch_events;
CREATE POLICY tenant_isolation ON production_batch_events
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON production_batch_events TO erp_app;

GRANT ALL ON production_batch_events TO erp_system;

GRANT USAGE, SELECT ON SEQUENCE production_batch_events_id_seq TO erp_app, erp_system;


CREATE TABLE IF NOT EXISTS production_batch_stages (
	batch_id BIGINT NOT NULL, 
	seq INTEGER NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	worker VARCHAR(120), 
	notes VARCHAR(400), 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (batch_id, seq), 
	FOREIGN KEY(batch_id) REFERENCES production_batches (id), 
	UNIQUE (public_id)
)

;

CREATE INDEX IF NOT EXISTS ix_production_batch_stages_tenant_id ON production_batch_stages (tenant_id);

ALTER TABLE production_batch_stages ENABLE ROW LEVEL SECURITY;

ALTER TABLE production_batch_stages FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON production_batch_stages;
CREATE POLICY tenant_isolation ON production_batch_stages
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON production_batch_stages TO erp_app;

GRANT ALL ON production_batch_stages TO erp_system;

GRANT USAGE, SELECT ON SEQUENCE production_batch_stages_id_seq TO erp_app, erp_system;


CREATE TABLE IF NOT EXISTS production_batch_lines (
	batch_id BIGINT NOT NULL, 
	po_line_id BIGINT NOT NULL, 
	variant_id BIGINT NOT NULL, 
	planned INTEGER NOT NULL, 
	completed INTEGER NOT NULL, 
	approved INTEGER NOT NULL, 
	held INTEGER NOT NULL, 
	rejected INTEGER NOT NULL, 
	received INTEGER NOT NULL, 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (batch_id, po_line_id), 
	CHECK (planned > 0 AND completed >= 0 AND completed <= planned), 
	CHECK (approved >= 0 AND held >= 0 AND rejected >= 0 AND approved + held + rejected <= completed), 
	CHECK (received >= 0 AND received <= approved), 
	FOREIGN KEY(batch_id) REFERENCES production_batches (id), 
	FOREIGN KEY(po_line_id) REFERENCES purchase_order_lines (id), 
	FOREIGN KEY(variant_id) REFERENCES product_variants (id), 
	UNIQUE (public_id)
)

;

CREATE INDEX IF NOT EXISTS ix_production_batch_lines_batch_id ON production_batch_lines (batch_id);

CREATE INDEX IF NOT EXISTS ix_production_batch_lines_tenant_id ON production_batch_lines (tenant_id);

ALTER TABLE production_batch_lines ENABLE ROW LEVEL SECURITY;

ALTER TABLE production_batch_lines FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON production_batch_lines;
CREATE POLICY tenant_isolation ON production_batch_lines
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON production_batch_lines TO erp_app;

GRANT ALL ON production_batch_lines TO erp_system;

GRANT USAGE, SELECT ON SEQUENCE production_batch_lines_id_seq TO erp_app, erp_system;


CREATE TABLE IF NOT EXISTS production_batch_lots (
	line_id BIGINT NOT NULL, 
	location_id BIGINT NOT NULL, 
	on_hand INTEGER NOT NULL, 
	allocated INTEGER NOT NULL, 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (line_id, location_id), 
	CHECK (on_hand >= 0 AND allocated >= 0 AND allocated <= on_hand), 
	FOREIGN KEY(line_id) REFERENCES production_batch_lines (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id), 
	UNIQUE (public_id)
)

;

CREATE INDEX IF NOT EXISTS ix_production_batch_lots_tenant_id ON production_batch_lots (tenant_id);

ALTER TABLE production_batch_lots ENABLE ROW LEVEL SECURITY;

ALTER TABLE production_batch_lots FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON production_batch_lots;
CREATE POLICY tenant_isolation ON production_batch_lots
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON production_batch_lots TO erp_app;

GRANT ALL ON production_batch_lots TO erp_system;

GRANT USAGE, SELECT ON SEQUENCE production_batch_lots_id_seq TO erp_app, erp_system;


CREATE TABLE IF NOT EXISTS shipment_batch_allocations (
	shipment_id BIGINT NOT NULL, 
	shipment_line_id BIGINT NOT NULL, 
	lot_id BIGINT NOT NULL, 
	qty INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (qty > 0), 
	UNIQUE (shipment_line_id, lot_id), 
	FOREIGN KEY(shipment_id) REFERENCES shipments (id), 
	FOREIGN KEY(shipment_line_id) REFERENCES shipment_lines (id), 
	FOREIGN KEY(lot_id) REFERENCES production_batch_lots (id), 
	UNIQUE (public_id)
)

;

CREATE INDEX IF NOT EXISTS ix_shipment_batch_allocations_tenant_id ON shipment_batch_allocations (tenant_id);

CREATE INDEX IF NOT EXISTS ix_shipment_batch_allocations_shipment_id ON shipment_batch_allocations (shipment_id);

ALTER TABLE shipment_batch_allocations ENABLE ROW LEVEL SECURITY;

ALTER TABLE shipment_batch_allocations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON shipment_batch_allocations;
CREATE POLICY tenant_isolation ON shipment_batch_allocations
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON shipment_batch_allocations TO erp_app;

GRANT ALL ON shipment_batch_allocations TO erp_system;

GRANT USAGE, SELECT ON SEQUENCE shipment_batch_allocations_id_seq TO erp_app, erp_system;


CREATE TABLE IF NOT EXISTS shipment_inventory_allocations (
	shipment_id BIGINT NOT NULL, 
	shipment_line_id BIGINT NOT NULL, 
	order_line_id BIGINT NOT NULL, 
	stock_level_id BIGINT NOT NULL, 
	qty INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL, 
	public_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (qty > 0), 
	UNIQUE (shipment_id, order_line_id, stock_level_id), 
	FOREIGN KEY(shipment_id) REFERENCES shipments (id), 
	FOREIGN KEY(shipment_line_id) REFERENCES shipment_lines (id), 
	FOREIGN KEY(order_line_id) REFERENCES sales_order_lines (id), 
	FOREIGN KEY(stock_level_id) REFERENCES stock_levels (id), 
	UNIQUE (public_id)
)

;
CREATE INDEX IF NOT EXISTS ix_shipment_inventory_allocations_shipment_id ON shipment_inventory_allocations (shipment_id);
CREATE INDEX IF NOT EXISTS ix_shipment_inventory_allocations_tenant_id ON shipment_inventory_allocations (tenant_id);
ALTER TABLE shipment_inventory_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE shipment_inventory_allocations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON shipment_inventory_allocations;
CREATE POLICY tenant_isolation ON shipment_inventory_allocations
 USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
 WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON shipment_inventory_allocations TO erp_app;
GRANT ALL ON shipment_inventory_allocations TO erp_system;
GRANT USAGE, SELECT ON SEQUENCE shipment_inventory_allocations_id_seq TO erp_app, erp_system;

COMMIT;

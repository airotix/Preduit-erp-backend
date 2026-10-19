BEGIN;
CREATE TABLE supplier_returns (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
 tenant_id UUID NOT NULL REFERENCES tenants(id),
 request_id UUID NOT NULL, request_payload TEXT NOT NULL,
 po_line_id BIGINT NOT NULL REFERENCES purchase_order_lines(id),
 location_id BIGINT NOT NULL REFERENCES locations(id),
 lot_id BIGINT REFERENCES production_batch_lots(id),
 stock_entry_id BIGINT UNIQUE REFERENCES inventory_stock_entries(id),
 quantity INT NOT NULL CHECK(quantity>0), amount NUMERIC(19,4) NOT NULL CHECK(amount>=0),
 resolution VARCHAR(20) NOT NULL CHECK(resolution IN ('Credit','Refund','Replacement')),
 status VARCHAR(32) NOT NULL DEFAULT 'Awaiting supplier',
 reference VARCHAR(80) NOT NULL, reason VARCHAR(400) NOT NULL, actor VARCHAR(256) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 supplier_bill_id BIGINT REFERENCES supplier_bills(id),
 credit_reference VARCHAR(80), credit_on DATE, credit_journal_id BIGINT UNIQUE REFERENCES journal_entries(id),
 refund_reference VARCHAR(80), refund_on DATE, refund_channel VARCHAR(10), refund_journal_id BIGINT UNIQUE REFERENCES journal_entries(id),
 inspection_id BIGINT REFERENCES inspections(id), replaced BOOLEAN NOT NULL DEFAULT false,
 UNIQUE(tenant_id,request_id)
);
CREATE INDEX ix_supplier_returns_source ON supplier_returns(tenant_id,po_line_id);
ALTER TABLE supplier_returns ENABLE ROW LEVEL SECURITY;
ALTER TABLE supplier_returns FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON supplier_returns
 USING(tenant_id=current_setting('app.tenant_id',true)::uuid OR current_setting('app.rls_bypass',true)='true')
 WITH CHECK(tenant_id=current_setting('app.tenant_id',true)::uuid OR current_setting('app.rls_bypass',true)='true');
GRANT SELECT,INSERT,UPDATE ON supplier_returns TO erp_app;
GRANT ALL ON supplier_returns TO erp_system;
GRANT USAGE,SELECT ON SEQUENCE supplier_returns_id_seq TO erp_app,erp_system;
COMMIT;

BEGIN;
ALTER TABLE purchase_orders ADD COLUMN original_due_on DATE;
ALTER TABLE purchase_orders ADD COLUMN due_baseline_recorded BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE goods_receipts ADD COLUMN received_on DATE;
CREATE TABLE supplier_followups (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
 tenant_id UUID NOT NULL REFERENCES tenants(id),
 supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
 source_type VARCHAR(20), source_id UUID,
 summary VARCHAR(240) NOT NULL, owner VARCHAR(256) NOT NULL, due_on DATE NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'Open' CHECK(status IN ('Open','In progress','Resolved')),
 resolution VARCHAR(1000), created_by VARCHAR(256) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ,
 request_id UUID NOT NULL, request_payload TEXT NOT NULL, UNIQUE(tenant_id,request_id)
);
CREATE UNIQUE INDEX ux_supplier_followup_active_source ON supplier_followups(tenant_id,source_type,source_id)
 WHERE status <> 'Resolved' AND source_id IS NOT NULL;
CREATE INDEX ix_supplier_followup_due ON supplier_followups(tenant_id,status,due_on);
ALTER TABLE supplier_followups ENABLE ROW LEVEL SECURITY;
ALTER TABLE supplier_followups FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON supplier_followups
 USING(tenant_id=current_setting('app.tenant_id',true)::uuid OR current_setting('app.rls_bypass',true)='true')
 WITH CHECK(tenant_id=current_setting('app.tenant_id',true)::uuid OR current_setting('app.rls_bypass',true)='true');
GRANT SELECT,INSERT,UPDATE ON supplier_followups TO erp_app;
GRANT ALL ON supplier_followups TO erp_system;
GRANT USAGE,SELECT ON SEQUENCE supplier_followups_id_seq TO erp_app,erp_system;
COMMIT;

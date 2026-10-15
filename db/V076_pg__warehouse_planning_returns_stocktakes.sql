BEGIN;
SET LOCAL app.rls_bypass = 'true';
CREATE TABLE inventory_replenishment_rules (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL REFERENCES tenants(id), variant_id BIGINT NOT NULL REFERENCES product_variants(id),
 location_id BIGINT NOT NULL REFERENCES locations(id), supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
 minimum INTEGER NOT NULL CHECK (minimum >= 0), target INTEGER NOT NULL CHECK (target > minimum),
 lead_days INTEGER NOT NULL CHECK (lead_days BETWEEN 0 AND 3650),
 minimum_order INTEGER NOT NULL DEFAULT 1 CHECK (minimum_order > 0), pack_multiple INTEGER NOT NULL DEFAULT 1 CHECK (pack_multiple > 0),
 active BOOLEAN NOT NULL DEFAULT true, UNIQUE(tenant_id,variant_id,location_id)
);
ALTER TABLE purchase_orders ADD COLUMN incoming_location_id BIGINT REFERENCES locations(id);
ALTER TABLE purchase_orders ADD COLUMN replenishment_rule_id BIGINT REFERENCES inventory_replenishment_rules(id);
ALTER TABLE purchase_orders ADD COLUMN replenishment_request_id UUID;
CREATE UNIQUE INDEX ux_replenishment_po_request ON purchase_orders(tenant_id,replenishment_request_id) WHERE replenishment_request_id IS NOT NULL;
ALTER TABLE purchase_order_lines ADD COLUMN variant_id BIGINT REFERENCES product_variants(id);
ALTER TABLE sales_returns ADD COLUMN sales_order_id BIGINT REFERENCES sales_orders(id);
ALTER TABLE sales_returns ADD COLUMN location_id BIGINT REFERENCES locations(id);
CREATE TABLE inventory_stocktakes (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL REFERENCES tenants(id), location_id BIGINT NOT NULL REFERENCES locations(id),
 reference VARCHAR(80) NOT NULL, reason VARCHAR(400) NOT NULL, counter VARCHAR(256) NOT NULL, creator VARCHAR(256) NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'Counting' CHECK(status IN ('Counting','Submitted','Recount','Posted','Cancelled')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE inventory_stocktake_lines (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
 stocktake_id BIGINT NOT NULL REFERENCES inventory_stocktakes(id), variant_id BIGINT NOT NULL REFERENCES product_variants(id),
 label VARCHAR(400) NOT NULL, book_qty INTEGER NOT NULL CHECK(book_qty>=0), movement_id BIGINT NOT NULL DEFAULT 0,
 counted_qty INTEGER CHECK(counted_qty>=0), recount_qty INTEGER CHECK(recount_qty>=0), UNIQUE(stocktake_id,variant_id)
);
CREATE TABLE inventory_return_lines (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
 return_id BIGINT NOT NULL REFERENCES sales_returns(id), order_line_id BIGINT NOT NULL REFERENCES sales_order_lines(id),
 variant_id BIGINT NOT NULL REFERENCES product_variants(id), label VARCHAR(400) NOT NULL,
 requested INTEGER NOT NULL CHECK(requested>0), cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled>=0),
 received INTEGER NOT NULL DEFAULT 0 CHECK(received>=0), ready INTEGER NOT NULL DEFAULT 0 CHECK(ready>=0),
 repair INTEGER NOT NULL DEFAULT 0 CHECK(repair>=0), scrap INTEGER NOT NULL DEFAULT 0 CHECK(scrap>=0),
 restocked INTEGER NOT NULL DEFAULT 0 CHECK(restocked>=0), UNIQUE(return_id,order_line_id),
 CHECK(received+cancelled<=requested), CHECK(ready+repair+scrap+restocked<=received)
);
CREATE INDEX ix_return_source_line ON inventory_return_lines(tenant_id,order_line_id);
CREATE TABLE inventory_workflow_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
 scope_id UUID NOT NULL, request_id UUID NOT NULL, kind VARCHAR(40) NOT NULL, payload TEXT NOT NULL,
 actor VARCHAR(256) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,request_id)
);
CREATE INDEX ix_inventory_workflow_scope ON inventory_workflow_events(tenant_id,scope_id,id);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['inventory_replenishment_rules','inventory_stocktakes','inventory_stocktake_lines','inventory_return_lines','inventory_workflow_events'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)::uuid OR current_setting(''app.rls_bypass'', true) = ''true'') WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true)::uuid OR current_setting(''app.rls_bypass'', true) = ''true'')',tab);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO erp_app',tab);
  EXECUTE format('GRANT ALL ON %I TO erp_system',tab);
  EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I TO erp_app, erp_system',tab || '_id_seq');
 END LOOP;
END $$;
REVOKE UPDATE, DELETE ON inventory_workflow_events FROM erp_app;
CREATE TRIGGER inventory_workflow_event_immutable BEFORE UPDATE OR DELETE ON inventory_workflow_events
 FOR EACH ROW EXECUTE FUNCTION protect_inventory_movement();
ALTER TABLE inventory_stock_entries DROP CONSTRAINT ck_inventory_movement_kind;
ALTER TABLE inventory_stock_entries DROP CONSTRAINT ck_inventory_movement_delta;
ALTER TABLE inventory_stock_entries ADD CONSTRAINT ck_inventory_movement_kind CHECK
 (kind IN ('Opening balance','Receipt','Adjustment','Transfer out','Transfer in','Dispatch','Return to QC','Write off','Return to supplier','Customer return','Return scrap','Return restock'));
ALTER TABLE inventory_stock_entries ADD CONSTRAINT ck_inventory_movement_delta CHECK
 (qty_delta <> 0 OR kind IN ('Opening balance','Return to QC','Customer return','Return scrap'));
COMMIT;

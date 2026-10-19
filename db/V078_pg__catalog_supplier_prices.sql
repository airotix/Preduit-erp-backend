BEGIN;
CREATE TABLE catalog_supplier_prices (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
 variant_id BIGINT NOT NULL REFERENCES product_variants(id), supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
 price NUMERIC(19,4) NOT NULL CHECK(price>0), currency VARCHAR(3) NOT NULL DEFAULT 'EUR' CHECK(currency='EUR'),
 active BOOLEAN NOT NULL DEFAULT true, UNIQUE(tenant_id,variant_id,supplier_id)
);
CREATE INDEX ix_catalog_supplier_price_supplier ON catalog_supplier_prices(tenant_id,supplier_id);
ALTER TABLE catalog_supplier_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_supplier_prices FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalog_supplier_prices
 USING (tenant_id=current_setting('app.tenant_id',true)::uuid OR current_setting('app.rls_bypass',true)='true')
 WITH CHECK (tenant_id=current_setting('app.tenant_id',true)::uuid OR current_setting('app.rls_bypass',true)='true');
GRANT SELECT,INSERT,UPDATE,DELETE ON catalog_supplier_prices TO erp_app;
GRANT ALL ON catalog_supplier_prices TO erp_system;
GRANT USAGE,SELECT ON SEQUENCE catalog_supplier_prices_id_seq TO erp_app,erp_system;
-- Existing POs and generic Catalog costs are preserved. Their historical prices
-- do not establish a current agreement with an arbitrary supplier.
COMMIT;

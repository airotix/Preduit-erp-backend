-- Tech packs use existing document links; file bytes go here when S3 is disabled.
BEGIN;
CREATE TABLE IF NOT EXISTS document_contents (
    document_id BIGINT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    data BYTEA NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_document_contents_tenant ON document_contents (tenant_id);
ALTER TABLE document_contents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_contents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON document_contents;
CREATE POLICY tenant_isolation ON document_contents
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
           OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR current_setting('app.rls_bypass', true) = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON document_contents TO erp_app;
GRANT ALL ON document_contents TO erp_system;
ALTER TABLE production_orders ALTER COLUMN stage SET DEFAULT 'Tech Pack';
COMMIT;

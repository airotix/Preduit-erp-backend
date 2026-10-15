-- Optional trade GTINs and approved, versioned Tech Pack documents.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_product_variants_tenant_barcode
    ON product_variants (tenant_id, barcode)
    WHERE barcode IS NOT NULL AND barcode <> '';

CREATE TABLE IF NOT EXISTS tech_pack_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    product_id BIGINT REFERENCES products(id),
    production_line_id BIGINT REFERENCES production_order_lines(id),
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id),
    revision INTEGER NOT NULL CHECK (revision > 0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('Draft', 'Approved', 'Superseded')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    approved_by VARCHAR(256),
    CHECK ((product_id IS NOT NULL) <> (production_line_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tech_pack_product_revision
    ON tech_pack_revisions (tenant_id, product_id, revision) WHERE product_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_tech_pack_line_revision
    ON tech_pack_revisions (tenant_id, production_line_id, revision) WHERE production_line_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_tech_pack_approved_product
    ON tech_pack_revisions (tenant_id, product_id) WHERE product_id IS NOT NULL AND status = 'Approved';
CREATE UNIQUE INDEX IF NOT EXISTS ux_tech_pack_approved_line
    ON tech_pack_revisions (tenant_id, production_line_id) WHERE production_line_id IS NOT NULL AND status = 'Approved';

-- The prior workflow treated the newest uploaded PDF as received. Preserve
-- that state by approving the newest legacy PDF; earlier files remain history.
WITH catalog_legacy AS (
    SELECT d.id AS document_id, d.tenant_id, p.id AS product_id,
           NULL::bigint AS production_line_id, d.created_at,
           row_number() OVER (PARTITION BY p.id ORDER BY d.id)::int AS revision,
           row_number() OVER (PARTITION BY p.id ORDER BY d.id DESC) AS newest
      FROM documents d JOIN products p ON p.public_id::text = d.entity_ref
       AND p.tenant_id = d.tenant_id
     WHERE d.module = 'catalog' AND d.entity_type = 'tech-pack'
       AND d.filename ILIKE '%.pdf' AND d.content_type = 'application/pdf'
       AND NOT d.is_deleted AND d.storage_path <> ''
), line_legacy AS (
    SELECT d.id AS document_id, d.tenant_id, NULL::bigint AS product_id,
           l.id AS production_line_id, d.created_at,
           row_number() OVER (PARTITION BY l.id ORDER BY d.id)::int AS revision,
           row_number() OVER (PARTITION BY l.id ORDER BY d.id DESC) AS newest
      FROM documents d JOIN production_order_lines l ON l.public_id::text = d.entity_ref
       AND l.tenant_id = d.tenant_id
     WHERE d.module = 'production' AND d.entity_type = 'tech-pack'
       AND d.filename ILIKE '%.pdf' AND d.content_type = 'application/pdf'
       AND NOT d.is_deleted AND d.storage_path <> ''
)
INSERT INTO tech_pack_revisions
    (tenant_id, product_id, production_line_id, document_id, revision, status,
     created_at, approved_at, approved_by)
SELECT tenant_id, product_id, production_line_id, document_id, revision,
       CASE WHEN newest = 1 THEN 'Approved' ELSE 'Superseded' END,
       created_at, CASE WHEN newest = 1 THEN created_at ELSE NULL END,
       CASE WHEN newest = 1 THEN 'Legacy migration' ELSE NULL END
  FROM (SELECT * FROM catalog_legacy UNION ALL SELECT * FROM line_legacy) old
ON CONFLICT (document_id) DO NOTHING;

ALTER TABLE tech_pack_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tech_pack_revisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tech_pack_revisions;
CREATE POLICY tenant_isolation ON tech_pack_revisions
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true')
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR current_setting('app.rls_bypass', true) = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON tech_pack_revisions TO erp_app;
GRANT ALL ON tech_pack_revisions TO erp_system;
GRANT USAGE, SELECT ON SEQUENCE tech_pack_revisions_id_seq TO erp_app, erp_system;
COMMIT;

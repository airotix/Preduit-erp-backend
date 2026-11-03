/* ============================================================================
 * Preduit ERP — V016__documents.sql
 * Reusable document store: file metadata for uploads attached to any record,
 * with a module-prefixed unique doc id (e.g. PRC-000042). Run after V015.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.documents (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_documents PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_doc_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_doc_tenant REFERENCES dbo.tenants(id),
    doc_id       NVARCHAR(40)     NOT NULL,      -- module-prefixed, e.g. PRC-000042
    module       NVARCHAR(40)     NOT NULL,      -- owning module
    entity_type  NVARCHAR(60)     NULL,          -- e.g. goodsreceipt, shipment
    entity_ref   NVARCHAR(80)     NULL,          -- the record it's attached to (ref/public id)
    filename     NVARCHAR(260)    NOT NULL,
    content_type NVARCHAR(120)    NULL,
    size_bytes   BIGINT           NOT NULL CONSTRAINT df_doc_size DEFAULT (0),
    storage_path NVARCHAR(400)    NOT NULL,
    uploaded_by  BIGINT           NULL,
    created_at   DATETIME2        NOT NULL CONSTRAINT df_doc_created DEFAULT SYSUTCDATETIME(),
    is_deleted   BIT              NOT NULL CONSTRAINT df_doc_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_doc_docid UNIQUE (tenant_id, doc_id),
    CONSTRAINT uq_doc_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_doc_lookup ON dbo.documents (tenant_id, module, entity_ref);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.documents,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.documents AFTER INSERT;
GO

/* ============================================================================
 * Preduit ERP — V034__po_invoices.sql
 * Commercial invoices generated against a purchase order (procurement Invoices
 * tab). The full, editable invoice document is stored as JSON in `data`; a few
 * scalar columns are kept for the list view. Run after V033.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.po_invoices (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_poinv PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_poinv_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_poinv_tenant REFERENCES dbo.tenants(id),
    invoice_no    NVARCHAR(40)     NULL,
    po_no         NVARCHAR(40)     NULL,
    supplier_name NVARCHAR(200)    NULL,
    currency_code CHAR(3)          NULL,
    total         DECIMAL(19,4)    NOT NULL CONSTRAINT df_poinv_total DEFAULT (0),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_poinv_status DEFAULT ('Draft'),
    data          NVARCHAR(MAX)    NOT NULL,
    created_at    DATETIME2        NOT NULL CONSTRAINT df_poinv_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_poinv_deleted DEFAULT (0),
    CONSTRAINT uq_poinv_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_poinv_list ON dbo.po_invoices (tenant_id, is_deleted, id DESC);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.po_invoices,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.po_invoices AFTER INSERT;
GO
PRINT 'po_invoices created.';
GO

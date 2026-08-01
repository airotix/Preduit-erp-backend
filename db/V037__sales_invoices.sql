/* ============================================================================
 * Preduit ERP — V037__sales_invoices.sql
 * Commercial / retail invoices generated against a sales order (Sales & Orders
 * "Invoices" tab). Mirrors V034 po_invoices: the full, editable invoice document
 * is stored as JSON in `data`; a few scalar columns are kept for the list view.
 * `invoice_type` records the template used — Retail | Online | Wholesale
 * (Retail and Online share the flat receipt layout; Wholesale uses the matrix
 * layout). Run after V036.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.sales_invoices (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_sinv PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_sinv_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sinv_tenant REFERENCES dbo.tenants(id),
    invoice_no    NVARCHAR(40)     NULL,
    order_no      NVARCHAR(40)     NULL,
    customer_name NVARCHAR(200)    NULL,
    invoice_type  NVARCHAR(20)     NOT NULL CONSTRAINT df_sinv_type DEFAULT ('Retail'),
    currency_code CHAR(3)          NULL,
    total         DECIMAL(19,4)    NOT NULL CONSTRAINT df_sinv_total DEFAULT (0),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_sinv_status DEFAULT ('Draft'),
    data          NVARCHAR(MAX)    NOT NULL,
    created_at    DATETIME2        NOT NULL CONSTRAINT df_sinv_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_sinv_deleted DEFAULT (0),
    CONSTRAINT uq_sinv_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_sinv_list ON dbo.sales_invoices (tenant_id, is_deleted, id DESC);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_invoices,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_invoices AFTER INSERT;
GO
PRINT 'sales_invoices created.';
GO

/* ============================================================================
 * Preduit ERP — V009__sales_detail_fields.sql
 * Fields the Sales DETAIL pages show: customer contact, and order/invoice line
 * items. Run in SSMS against Preduit-ERP after V008.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.customers ADD
    phone   NVARCHAR(40)  NULL,
    address NVARCHAR(300) NULL;
GO

CREATE TABLE dbo.sales_order_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_order_lines PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ol_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ol_tenant REFERENCES dbo.tenants(id),
    order_id    BIGINT           NOT NULL CONSTRAINT fk_ol_order REFERENCES dbo.sales_orders(id),
    sku         NVARCHAR(64)     NULL,
    name        NVARCHAR(200)    NOT NULL,
    qty         INT              NOT NULL CONSTRAINT df_ol_qty DEFAULT (0),
    price       DECIMAL(19,4)    NOT NULL CONSTRAINT df_ol_price DEFAULT (0),
    line_total  DECIMAL(19,4)    NOT NULL CONSTRAINT df_ol_total DEFAULT (0)
);
GO
CREATE INDEX ix_ol_order ON dbo.sales_order_lines (tenant_id, order_id);
GO

CREATE TABLE dbo.invoice_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_invoice_lines PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_il_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_il_tenant REFERENCES dbo.tenants(id),
    invoice_id  BIGINT           NOT NULL CONSTRAINT fk_il_invoice REFERENCES dbo.invoices(id),
    sku         NVARCHAR(64)     NULL,
    name        NVARCHAR(200)    NOT NULL,
    qty         INT              NOT NULL CONSTRAINT df_il_qty DEFAULT (0),
    price       DECIMAL(19,4)    NOT NULL CONSTRAINT df_il_price DEFAULT (0),
    line_total  DECIMAL(19,4)    NOT NULL CONSTRAINT df_il_total DEFAULT (0)
);
GO
CREATE INDEX ix_il_invoice ON dbo.invoice_lines (tenant_id, invoice_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_order_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_order_lines AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoice_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoice_lines AFTER INSERT;
GO

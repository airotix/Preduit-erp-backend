/* ============================================================================
 * Preduit ERP — V006__sales_invoices_returns.sql
 * Invoices and Returns (RMA). Run in SSMS against Preduit-ERP after V005.
 * ("sales_returns" avoids the reserved word RETURNS.)
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.invoices (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_invoices PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_inv_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_inv_tenant REFERENCES dbo.tenants(id),
    invoice_no    NVARCHAR(32)     NULL,
    customer_id   BIGINT           NULL CONSTRAINT fk_inv_customer REFERENCES dbo.customers(id),
    customer_name NVARCHAR(200)    NOT NULL,
    issued_date   DATE             NOT NULL CONSTRAINT df_inv_issued DEFAULT CAST(SYSUTCDATETIME() AS DATE),
    due_date      NVARCHAR(40)     NULL,
    amount        DECIMAL(19,4)    NOT NULL CONSTRAINT df_inv_amount DEFAULT (0),
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_inv_ccy DEFAULT ('EUR'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_inv_status DEFAULT ('Open'),
    created_at    DATETIME2        NOT NULL CONSTRAINT df_inv_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_inv_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_inv_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_inv_tenant ON dbo.invoices (tenant_id, issued_date DESC);
GO

CREATE TABLE dbo.sales_returns (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_returns PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ret_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ret_tenant REFERENCES dbo.tenants(id),
    rma_no        NVARCHAR(32)     NULL,
    order_ref     NVARCHAR(40)     NULL,
    customer_name NVARCHAR(200)    NOT NULL,
    reason        NVARCHAR(80)     NULL,
    refund        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ret_refund DEFAULT (0),
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_ret_ccy DEFAULT ('EUR'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_ret_status DEFAULT ('Inspecting'),
    created_at    DATETIME2        NOT NULL CONSTRAINT df_ret_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_ret_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_ret_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ret_tenant ON dbo.sales_returns (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoices,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.invoices AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_returns,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_returns AFTER INSERT;
GO

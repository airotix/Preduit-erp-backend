/* ============================================================================
 * Preduit ERP — V005__sales_orders.sql
 * Sales orders (summary level; line items follow with a dedicated order-entry UI).
 * Run in SSMS against Preduit-ERP after V004.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.sales_orders (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_orders PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ord_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ord_tenant REFERENCES dbo.tenants(id),
    order_no      NVARCHAR(32)     NULL,
    customer_id   BIGINT           NULL CONSTRAINT fk_ord_customer REFERENCES dbo.customers(id),
    customer_name NVARCHAR(200)    NOT NULL,
    channel       NVARCHAR(20)     NOT NULL CONSTRAINT df_ord_channel DEFAULT ('Online'),
    item_count    INT              NOT NULL CONSTRAINT df_ord_items DEFAULT (0),
    total         DECIMAL(19,4)    NOT NULL CONSTRAINT df_ord_total DEFAULT (0),
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_ord_ccy DEFAULT ('EUR'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_ord_status DEFAULT ('New'),
    order_date    DATE             NOT NULL CONSTRAINT df_ord_date DEFAULT CAST(SYSUTCDATETIME() AS DATE),
    created_at    DATETIME2        NOT NULL CONSTRAINT df_ord_created DEFAULT SYSUTCDATETIME(),
    is_deleted    BIT              NOT NULL CONSTRAINT df_ord_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_ord_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ord_tenant ON dbo.sales_orders (tenant_id, order_date DESC);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_orders,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.sales_orders AFTER INSERT;
GO

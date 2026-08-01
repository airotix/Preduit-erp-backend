/* ============================================================================
 * Preduit ERP — V004__sales.sql
 * Sales module — starting with Customers. (Orders/invoices follow later.)
 * Run in SSMS against Preduit-ERP after V001–V003.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.customers (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_customers PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_cust_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_cust_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(200)    NOT NULL,
    email       NVARCHAR(256)    NOT NULL,
    [type]      NVARCHAR(20)     NOT NULL CONSTRAINT df_cust_type DEFAULT ('Retail'), -- Wholesale|Retail
    region      NVARCHAR(80)     NULL,
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_cust_status DEFAULT ('Active'),
    created_at  DATETIME2        NOT NULL CONSTRAINT df_cust_created DEFAULT SYSUTCDATETIME(),
    created_by  BIGINT           NULL,
    updated_at  DATETIME2        NULL,
    updated_by  BIGINT           NULL,
    is_deleted  BIT              NOT NULL CONSTRAINT df_cust_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_cust_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_cust_tenant ON dbo.customers (tenant_id);
GO

/* Extend tenant isolation to the new table. */
ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.customers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.customers AFTER INSERT;
GO

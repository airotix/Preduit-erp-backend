/* ============================================================================
 * Preduit ERP — V015__finance_aging_sources.sql
 * Sources for LIVE aging: a real due date on invoices (AR) and a supplier-bills
 * / payables table (AP). AR/AP aging is now aggregated from these, not stored.
 * Run after V014.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.invoices ADD due_on DATE NULL;
GO

CREATE TABLE dbo.supplier_bills (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bills PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bill_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bill_tenant REFERENCES dbo.tenants(id),
    bill_no       NVARCHAR(32)     NULL,
    supplier_name NVARCHAR(200)    NOT NULL,
    po_ref        NVARCHAR(32)     NULL,
    amount        DECIMAL(19,4)    NOT NULL CONSTRAINT df_bill_amt DEFAULT (0),
    due_on        DATE             NULL,
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_bill_status DEFAULT ('Open'),  -- Open|Paid
    is_deleted    BIT              NOT NULL CONSTRAINT df_bill_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_bill_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bill_tenant ON dbo.supplier_bills (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.supplier_bills,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.supplier_bills AFTER INSERT;
GO

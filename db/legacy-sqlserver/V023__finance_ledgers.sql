/* ============================================================================
 * Preduit ERP — V023__finance_ledgers.sql
 * Customer & Supplier ledger support: party code / payment terms / opening
 * balance, plus a credit_notes table for customer credits. Run after V022.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.customers ADD
    code            NVARCHAR(20)  NULL,
    terms           NVARCHAR(20)  NULL,
    opening_balance DECIMAL(19,4) NOT NULL CONSTRAINT df_cust_ob DEFAULT (0);
GO

ALTER TABLE dbo.suppliers ADD
    code            NVARCHAR(20)  NULL,
    terms           NVARCHAR(20)  NULL,
    opening_balance DECIMAL(19,4) NOT NULL CONSTRAINT df_sup_ob DEFAULT (0);
GO

CREATE TABLE dbo.credit_notes (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_cn PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_cn_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_cn_tenant REFERENCES dbo.tenants(id),
    cn_no        NVARCHAR(32)     NULL,
    customer_id  BIGINT           NULL,
    customer_name NVARCHAR(200)   NOT NULL,
    cn_date      DATE             NULL,
    amount       DECIMAL(19,4)    NOT NULL CONSTRAINT df_cn_amt DEFAULT (0),
    reason       NVARCHAR(200)    NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_cn_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_cn_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_cn_tenant ON dbo.credit_notes (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.credit_notes,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.credit_notes AFTER INSERT;
GO
PRINT 'Finance ledger fields + credit_notes created.';
GO

/* ============================================================================
 * Preduit ERP — V028__bank_reconciliation.sql
 * Bank accounts and imported statement lines for reconciliation. Run after V027.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.bank_accounts (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bank PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bank_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bank_tenant REFERENCES dbo.tenants(id),
    name          NVARCHAR(120)    NOT NULL,
    account_no    NVARCHAR(40)     NULL,
    gl_code       NVARCHAR(20)     NULL,          -- chart_of_accounts.code of the cash account
    currency_code CHAR(3)          NOT NULL CONSTRAINT df_bank_ccy DEFAULT ('EUR'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_bank_deleted DEFAULT (0),
    CONSTRAINT uq_bank_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bank_tenant ON dbo.bank_accounts (tenant_id);
GO

CREATE TABLE dbo.bank_transactions (
    id                BIGINT IDENTITY  NOT NULL CONSTRAINT pk_banktxn PRIMARY KEY,
    public_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bt_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bt_tenant REFERENCES dbo.tenants(id),
    bank_account_id   BIGINT           NOT NULL,
    txn_date          DATE             NULL,
    description       NVARCHAR(200)    NULL,
    amount            DECIMAL(19,4)    NOT NULL CONSTRAINT df_bt_amt DEFAULT (0),  -- + deposit, - withdrawal
    matched_payment_id BIGINT          NULL,
    status            NVARCHAR(12)     NOT NULL CONSTRAINT df_bt_status DEFAULT ('Unmatched'), -- Unmatched|Matched|Reconciled
    is_deleted        BIT              NOT NULL CONSTRAINT df_bt_deleted DEFAULT (0),
    CONSTRAINT uq_bt_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bt_acct ON dbo.bank_transactions (tenant_id, bank_account_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_accounts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_accounts AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_transactions,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bank_transactions AFTER INSERT;
GO
PRINT 'Bank reconciliation tables created.';
GO

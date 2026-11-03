/* ============================================================================
 * Preduit ERP — V012__finance.sql
 * Finance: chart of accounts, journal entries + lines (double-entry),
 * payments, and AR/AP aging snapshots. Shapes match the frontend Finance tabs.
 * Run after V011.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.chart_of_accounts (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_coa PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_coa_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_coa_tenant REFERENCES dbo.tenants(id),
    code        NVARCHAR(20)     NOT NULL,
    name        NVARCHAR(200)    NOT NULL,
    acct_type   NVARCHAR(20)     NOT NULL,  -- Asset|Liability|Equity|Income|Expense
    balance     DECIMAL(19,4)    NOT NULL CONSTRAINT df_coa_bal DEFAULT (0),
    is_deleted  BIT              NOT NULL CONSTRAINT df_coa_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_coa_code UNIQUE (tenant_id, code)
);
GO
CREATE INDEX ix_coa_tenant ON dbo.chart_of_accounts (tenant_id);
GO

CREATE TABLE dbo.journal_entries (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_je PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_je_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_je_tenant REFERENCES dbo.tenants(id),
    entry_no     NVARCHAR(32)     NOT NULL,
    entry_date   NVARCHAR(40)     NULL,
    memo         NVARCHAR(300)    NULL,
    total_debit  DECIMAL(19,4)    NOT NULL CONSTRAINT df_je_dr DEFAULT (0),
    total_credit DECIMAL(19,4)    NOT NULL CONSTRAINT df_je_cr DEFAULT (0),
    status       NVARCHAR(20)     NOT NULL CONSTRAINT df_je_status DEFAULT ('Draft'),
    source_note  NVARCHAR(MAX)    NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_je_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_je_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_je_tenant ON dbo.journal_entries (tenant_id);
GO

CREATE TABLE dbo.journal_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_jl PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_jl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_jl_tenant REFERENCES dbo.tenants(id),
    entry_id    BIGINT           NOT NULL CONSTRAINT fk_jl_entry REFERENCES dbo.journal_entries(id),
    account     NVARCHAR(160)    NOT NULL,
    description NVARCHAR(200)    NULL,
    debit       DECIMAL(19,4)    NOT NULL CONSTRAINT df_jl_dr DEFAULT (0),
    credit      DECIMAL(19,4)    NOT NULL CONSTRAINT df_jl_cr DEFAULT (0)
);
GO
CREATE INDEX ix_jl_entry ON dbo.journal_lines (tenant_id, entry_id);
GO

CREATE TABLE dbo.payments (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pmt PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pmt_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pmt_tenant REFERENCES dbo.tenants(id),
    payment_no    NVARCHAR(32)     NULL,
    pay_date      NVARCHAR(40)     NULL,
    party         NVARCHAR(200)    NOT NULL,
    allocated_to  NVARCHAR(60)     NULL,
    amount        DECIMAL(19,4)    NOT NULL CONSTRAINT df_pmt_amt DEFAULT (0),
    pay_type      NVARCHAR(20)     NOT NULL CONSTRAINT df_pmt_type DEFAULT ('Receipt'),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_pmt_status DEFAULT ('Pending'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_pmt_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_pmt_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_pmt_tenant ON dbo.payments (tenant_id);
GO

CREATE TABLE dbo.ar_aging (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_arage PRIMARY KEY,
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_arage_tenant REFERENCES dbo.tenants(id),
    customer_name NVARCHAR(200)    NOT NULL,
    region        NVARCHAR(80)     NULL,
    current_amt   DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_cur DEFAULT (0),
    b1_30         DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_130 DEFAULT (0),
    b31_60        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_3160 DEFAULT (0),
    b61_90        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_6190 DEFAULT (0),
    b90_plus      DECIMAL(19,4)    NOT NULL CONSTRAINT df_ar_90 DEFAULT (0)
);
GO
CREATE INDEX ix_arage_tenant ON dbo.ar_aging (tenant_id);
GO

CREATE TABLE dbo.ap_aging (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_apage PRIMARY KEY,
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_apage_tenant REFERENCES dbo.tenants(id),
    supplier_name NVARCHAR(200)    NOT NULL,
    region        NVARCHAR(80)     NULL,
    current_amt   DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_cur DEFAULT (0),
    b1_30         DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_130 DEFAULT (0),
    b31_60        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_3160 DEFAULT (0),
    b61_90        DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_6190 DEFAULT (0),
    b90_plus      DECIMAL(19,4)    NOT NULL CONSTRAINT df_ap_90 DEFAULT (0)
);
GO
CREATE INDEX ix_apage_tenant ON dbo.ap_aging (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.chart_of_accounts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.chart_of_accounts AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_entries,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_entries AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.journal_lines AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.payments,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.payments AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ar_aging,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ar_aging AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ap_aging,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ap_aging AFTER INSERT;
GO

/* ============================================================================
 * Preduit ERP — V050__ledger_entries.sql
 *  1) ledger_entries: manual customer-ledger entries (a debit and/or credit with
 *     a description) created from the customer ledger "New entry" button.
 *  2) invoices.memo: editable description for auto/receivable invoice rows.
 * Idempotent. Run after V049.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* 1. Manual ledger entries. */
IF OBJECT_ID('dbo.ledger_entries', 'U') IS NULL
CREATE TABLE dbo.ledger_entries (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_le PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_le_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_le_tenant REFERENCES dbo.tenants(id),
    customer_id  BIGINT           NULL,
    entry_date   DATE             NULL,
    description  NVARCHAR(400)    NULL,
    debit        DECIMAL(19,4)    NOT NULL CONSTRAINT df_le_debit  DEFAULT (0),
    credit       DECIMAL(19,4)    NOT NULL CONSTRAINT df_le_credit DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_le_del     DEFAULT (0),
    CONSTRAINT uq_le_public UNIQUE (public_id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_le_customer'
               AND object_id = OBJECT_ID('dbo.ledger_entries'))
    CREATE INDEX ix_le_customer ON dbo.ledger_entries (tenant_id, customer_id);
GO

/* RLS predicates — add only if not already present on the table. */
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.ledger_entries'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ledger_entries,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.ledger_entries AFTER INSERT;
GO

/* 2. Editable description for invoice (receivable) rows. */
IF COL_LENGTH('dbo.invoices', 'memo') IS NULL
    ALTER TABLE dbo.invoices ADD memo NVARCHAR(400) NULL;
GO

PRINT 'V050: ledger_entries created; invoices.memo added.';
GO

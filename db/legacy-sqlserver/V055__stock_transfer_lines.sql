/* ============================================================================
 * Preduit ERP — V055__stock_transfer_lines.sql
 * Line items for a stock transfer (article + colour + size + qty), so a
 * transfer captures what's moving — mirroring sales order lines. Idempotent.
 * Run after V054.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF OBJECT_ID('dbo.stock_transfer_lines', 'U') IS NULL
CREATE TABLE dbo.stock_transfer_lines (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_stl PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_stl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_stl_tenant REFERENCES dbo.tenants(id),
    transfer_id  BIGINT           NOT NULL CONSTRAINT fk_stl_transfer REFERENCES dbo.stock_transfers(id),
    name         NVARCHAR(200)    NOT NULL,
    color        NVARCHAR(60)     NULL,
    size         NVARCHAR(60)     NULL,
    sku          NVARCHAR(64)     NULL,
    qty          INT              NOT NULL CONSTRAINT df_stl_qty DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_stl_del DEFAULT (0),
    CONSTRAINT uq_stl_public UNIQUE (public_id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_stl_transfer'
               AND object_id = OBJECT_ID('dbo.stock_transfer_lines'))
    CREATE INDEX ix_stl_transfer ON dbo.stock_transfer_lines (tenant_id, transfer_id);
GO

IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.stock_transfer_lines'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfer_lines,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfer_lines AFTER INSERT;
GO

PRINT 'V055: stock_transfer_lines created.';
GO

/* ============================================================================
 * Preduit ERP — V047__production_order_lines.sql
 * Production order lines (per-style items within a manufacturing order) and
 * re-parent production_stages to lines so each item gets its own timeline.
 *
 * Self-healing / idempotent: step 0 cleans up any partial or failed prior run
 * (orphaned pol_* constraints, a half-created table, stray line_id values) so
 * the file can be re-run safely to a clean, correct end state. Run after V046.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* ---------------------------------------------------------------------------
 * 0. Recovery: undo any partial prior apply so creation can proceed cleanly.
 * --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_ps_line')
    ALTER TABLE dbo.production_stages DROP CONSTRAINT fk_ps_line;
GO

IF OBJECT_ID('dbo.production_order_lines', 'U') IS NOT NULL
    DROP TABLE dbo.production_order_lines;
GO

/* Any line_id set before is now orphaned (its line row is gone) — reset it so
   the backfill below can re-attach stages correctly. */
IF COL_LENGTH('dbo.production_stages', 'line_id') IS NOT NULL
    UPDATE dbo.production_stages SET line_id = NULL WHERE line_id IS NOT NULL;
GO

/* Drop any orphaned pol_* named constraints left behind by a failed CREATE
   (this is what caused the "object named df_pol_pub already exists" error). */
DECLARE @name SYSNAME, @tbl NVARCHAR(300), @sql NVARCHAR(MAX);
DECLARE c CURSOR LOCAL FAST_FORWARD FOR
    SELECT o.name, QUOTENAME(SCHEMA_NAME(t.schema_id)) + N'.' + QUOTENAME(t.name)
    FROM sys.objects o
    JOIN sys.tables t ON t.object_id = o.parent_object_id
    WHERE o.name IN ('pk_pol', 'df_pol_pub', 'fk_pol_tenant', 'fk_pol_order',
                     'fk_pol_sol', 'df_pol_qty', 'df_pol_deleted', 'uq_pol_public');
OPEN c;
FETCH NEXT FROM c INTO @name, @tbl;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'ALTER TABLE ' + @tbl + N' DROP CONSTRAINT ' + QUOTENAME(@name) + N';';
    EXEC sp_executesql @sql;
    FETCH NEXT FROM c INTO @name, @tbl;
END
CLOSE c;
DEALLOCATE c;
GO

/* ---------------------------------------------------------------------------
 * 1. Production order lines — one per style/item within a production order
 * --------------------------------------------------------------------------- */
CREATE TABLE dbo.production_order_lines (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pol PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pol_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pol_tenant REFERENCES dbo.tenants(id),
    order_id         BIGINT           NOT NULL CONSTRAINT fk_pol_order REFERENCES dbo.production_orders(id),
    sales_order_line_id BIGINT        NULL CONSTRAINT fk_pol_sol REFERENCES dbo.sales_order_lines(id),
    sku              NVARCHAR(64)     NULL,
    name             NVARCHAR(200)    NOT NULL,            -- style / product name
    color            NVARCHAR(60)     NULL,
    size             NVARCHAR(60)     NULL,
    qty              INT              NOT NULL CONSTRAINT df_pol_qty DEFAULT (0),
    is_deleted       BIT              NOT NULL CONSTRAINT df_pol_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_pol_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_pol_order ON dbo.production_order_lines (tenant_id, order_id);
GO
CREATE INDEX ix_pol_sol ON dbo.production_order_lines (tenant_id, sales_order_line_id);
GO

/* RLS predicates — add only if not already present on the table. */
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.production_order_lines'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_order_lines,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_order_lines AFTER INSERT;
GO

/* ---------------------------------------------------------------------------
 * 2. Re-parent production_stages: line_id + FK + indexes
 * --------------------------------------------------------------------------- */
IF COL_LENGTH('dbo.production_stages', 'line_id') IS NULL
    ALTER TABLE dbo.production_stages ADD line_id BIGINT NULL;
GO

ALTER TABLE dbo.production_stages
    ADD CONSTRAINT fk_ps_line FOREIGN KEY (line_id) REFERENCES dbo.production_order_lines(id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_ps_line'
               AND object_id = OBJECT_ID('dbo.production_stages'))
    CREATE INDEX ix_ps_line ON dbo.production_stages (tenant_id, line_id, seq);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'uq_ps_line_seq'
               AND object_id = OBJECT_ID('dbo.production_stages'))
    CREATE UNIQUE INDEX uq_ps_line_seq ON dbo.production_stages (line_id, seq)
        WHERE line_id IS NOT NULL;
GO

/* Stages now belong to a line — make order_id nullable (safe to re-run). */
ALTER TABLE dbo.production_stages ALTER COLUMN order_id BIGINT NULL;
GO

/* ---------------------------------------------------------------------------
 * 3. Backfill (set-based, idempotent): one line per existing order, then
 *    attach that order's stray stages to it.
 * --------------------------------------------------------------------------- */
INSERT INTO dbo.production_order_lines (tenant_id, order_id, name, qty)
SELECT po.tenant_id, po.id, po.style, po.qty
FROM dbo.production_orders po
WHERE po.is_deleted = 0
  AND NOT EXISTS (SELECT 1 FROM dbo.production_order_lines l WHERE l.order_id = po.id);
GO

UPDATE ps
SET line_id = x.line_id
FROM dbo.production_stages ps
CROSS APPLY (
    SELECT TOP 1 l.id AS line_id
    FROM dbo.production_order_lines l
    WHERE l.order_id = ps.order_id
    ORDER BY l.id
) x
WHERE ps.line_id IS NULL;
GO

PRINT 'production_order_lines created and production_stages re-parented (self-healing).';
GO

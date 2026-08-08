/* ============================================================================
 * Preduit ERP — V047__production_order_lines.sql
 * Production order lines (per-style items within a manufacturing order) and
 * re-parent production_stages to lines so each item gets its own timeline.
 * Run after V046.
 * ==========================================================================*/
SET XACT_ABORT ON;
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
    name             NVARCHAR(200)    NOT NULL,            -- style / product name (e.g. "Merino Crew Knit")
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

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_order_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_order_lines AFTER INSERT;
GO

/* ---------------------------------------------------------------------------
 * 2. Re-parent production_stages: add line_id, make order_id nullable,
 *    and create a unique constraint per line+seq.
 *    Existing stages remain linked to order_id (for backwards compat); new
 *    stages will link to line_id.
 * --------------------------------------------------------------------------- */
ALTER TABLE dbo.production_stages
    ADD line_id BIGINT NULL;
GO

-- FK to the new lines table
ALTER TABLE dbo.production_stages
    ADD CONSTRAINT fk_ps_line FOREIGN KEY (line_id) REFERENCES dbo.production_order_lines(id);
GO

-- Index for per-line stage queries
CREATE INDEX ix_ps_line ON dbo.production_stages (tenant_id, line_id, seq);
GO

-- Unique sequence per line (allows same stage names across lines)
-- Only enforce when line_id is not null (new stages)
CREATE UNIQUE INDEX uq_ps_line_seq ON dbo.production_stages (line_id, seq)
    WHERE line_id IS NOT NULL;
GO

/* ---------------------------------------------------------------------------
 * 3. Data migration: for existing production orders, create one line per order
 *    (preserving the current style/qty) and move their stages to that line.
 * --------------------------------------------------------------------------- */
DECLARE @order_id BIGINT, @line_id BIGINT, @tenant_id UNIQUEIDENTIFIER;
DECLARE cur CURSOR FOR SELECT id, tenant_id FROM dbo.production_orders WHERE is_deleted = 0;
OPEN cur;
FETCH NEXT FROM cur INTO @order_id, @tenant_id;
WHILE @@FETCH_STATUS = 0
BEGIN
    -- Create a line mirroring the parent order's style/qty
    INSERT INTO dbo.production_order_lines (tenant_id, order_id, name, qty)
    SELECT tenant_id, id, style, qty FROM dbo.production_orders WHERE id = @order_id;
    SET @line_id = SCOPE_IDENTITY();

    -- Move existing stages to the new line
    UPDATE dbo.production_stages
    SET line_id = @line_id
    WHERE order_id = @order_id AND line_id IS NULL;

    FETCH NEXT FROM cur INTO @order_id, @tenant_id;
END
CLOSE cur;
DEALLOCATE cur;
GO

/* ---------------------------------------------------------------------------
 * 4. Make order_id nullable (stages now belong to a line)
 * --------------------------------------------------------------------------- */
ALTER TABLE dbo.production_stages
    ALTER COLUMN order_id BIGINT NULL;
GO

/* ---------------------------------------------------------------------------
 * 5. Update the security policy for production_stages to also filter by line_id
 *    (tenant_id is sufficient since line_id → order_id → tenant_id, but we
 *    keep it simple: tenant_id predicate already covers it via the line table).
 *    No change needed — the existing predicate on tenant_id is enough because
 *    stages are joined to lines/orders which are tenant-scoped.
 * --------------------------------------------------------------------------- */
PRINT 'production_order_lines created and production_stages re-parented.';
GO
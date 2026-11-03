/* ============================================================================
 * Preduit ERP — V021__purchase_order_lines.sql
 * Line items for purchase orders (item, color, qty, price) so the New PO form
 * captures a real basket and the PO drill-down can show its lines. Run after V020.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.purchase_order_lines (
    id         BIGINT IDENTITY  NOT NULL CONSTRAINT pk_po_lines PRIMARY KEY,
    public_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_pol_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_pol_tenant REFERENCES dbo.tenants(id),
    po_id      BIGINT           NOT NULL CONSTRAINT fk_pol_po REFERENCES dbo.purchase_orders(id),
    name       NVARCHAR(200)    NOT NULL,
    color      NVARCHAR(60)     NULL,
    sku        NVARCHAR(64)     NULL,
    qty        INT              NOT NULL CONSTRAINT df_pol_qty DEFAULT (0),
    price      DECIMAL(19,4)    NOT NULL CONSTRAINT df_pol_price DEFAULT (0),
    line_total DECIMAL(19,4)    NOT NULL CONSTRAINT df_pol_total DEFAULT (0)
);
GO
CREATE INDEX ix_pol_po ON dbo.purchase_order_lines (tenant_id, po_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_order_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_order_lines AFTER INSERT;
GO
PRINT 'purchase_order_lines created.';
GO

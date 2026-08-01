/* ============================================================================
 * Preduit ERP — V017__production.sql
 * Production: manufacturing orders (with stage/progress) + bill of materials.
 * Run after V016.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.production_orders (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_porders PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_po2_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_po2_tenant REFERENCES dbo.tenants(id),
    order_no    NVARCHAR(32)     NULL,
    style       NVARCHAR(200)    NOT NULL,
    factory     NVARCHAR(120)    NULL,
    qty         INT              NOT NULL CONSTRAINT df_po2_qty DEFAULT (0),
    stage       NVARCHAR(20)     NOT NULL CONSTRAINT df_po2_stage DEFAULT ('Cutting'),
    progress    INT              NOT NULL CONSTRAINT df_po2_prog DEFAULT (0),
    is_deleted  BIT              NOT NULL CONSTRAINT df_po2_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_po2_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_po2_tenant ON dbo.production_orders (tenant_id);
GO

CREATE TABLE dbo.bill_of_materials (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_bom PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_bom_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_bom_tenant REFERENCES dbo.tenants(id),
    component    NVARCHAR(200)    NOT NULL,
    style        NVARCHAR(200)    NULL,
    material     NVARCHAR(80)     NULL,
    qty_per_unit NVARCHAR(40)     NULL,
    cost         DECIMAL(19,4)    NOT NULL CONSTRAINT df_bom_cost DEFAULT (0),
    is_deleted   BIT              NOT NULL CONSTRAINT df_bom_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_bom_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_bom_tenant ON dbo.bill_of_materials (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_orders,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_orders AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bill_of_materials,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.bill_of_materials AFTER INSERT;
GO

/* ============================================================================
 * Preduit ERP — V008__inventory.sql
 * Inventory module: locations, per-variant/per-location stock, transfers,
 * reorder alerts. Shapes match the frontend Inventory tabs.
 * Run in SSMS against Preduit-ERP after V007.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.locations (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_locations PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_loc_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_loc_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    code        NVARCHAR(40)     NULL,
    [type]      NVARCHAR(20)     NOT NULL CONSTRAINT df_loc_type DEFAULT ('Warehouse'), -- Warehouse|Retail
    region      NVARCHAR(80)     NULL,
    capacity    INT              NULL,
    is_deleted  BIT              NOT NULL CONSTRAINT df_loc_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_loc_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_loc_tenant ON dbo.locations (tenant_id);
GO

CREATE TABLE dbo.stock_levels (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_stock PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_stk_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_stk_tenant REFERENCES dbo.tenants(id),
    variant_id  BIGINT           NOT NULL CONSTRAINT fk_stk_variant REFERENCES dbo.product_variants(id),
    location_id BIGINT           NOT NULL CONSTRAINT fk_stk_location REFERENCES dbo.locations(id),
    on_hand     INT              NOT NULL CONSTRAINT df_stk_onhand DEFAULT (0),
    reserved    INT              NOT NULL CONSTRAINT df_stk_reserved DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_stock UNIQUE (tenant_id, variant_id, location_id)
);
GO
CREATE INDEX ix_stk_tenant ON dbo.stock_levels (tenant_id);
GO

CREATE TABLE dbo.stock_transfers (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_transfers PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_trf_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_trf_tenant REFERENCES dbo.tenants(id),
    transfer_no      NVARCHAR(32)     NULL,
    from_location_id BIGINT           NULL CONSTRAINT fk_trf_from REFERENCES dbo.locations(id),
    to_location_id   BIGINT           NULL CONSTRAINT fk_trf_to   REFERENCES dbo.locations(id),
    units            INT              NOT NULL CONSTRAINT df_trf_units DEFAULT (0),
    status           NVARCHAR(20)     NOT NULL CONSTRAINT df_trf_status DEFAULT ('Draft'),
    eta              NVARCHAR(40)     NULL,
    is_deleted       BIT              NOT NULL CONSTRAINT df_trf_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_trf_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_trf_tenant ON dbo.stock_transfers (tenant_id);
GO

CREATE TABLE dbo.reorder_alerts (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_alerts PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_alr_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_alr_tenant REFERENCES dbo.tenants(id),
    variant_id    BIGINT           NULL CONSTRAINT fk_alr_variant REFERENCES dbo.product_variants(id),
    sku           NVARCHAR(64)     NOT NULL,
    available     INT              NOT NULL CONSTRAINT df_alr_avail DEFAULT (0),
    reorder_point INT              NOT NULL CONSTRAINT df_alr_rop DEFAULT (0),
    suggested     INT              NOT NULL CONSTRAINT df_alr_sugg DEFAULT (0),
    supplier      NVARCHAR(200)    NULL,
    severity      NVARCHAR(20)     NOT NULL CONSTRAINT df_alr_sev DEFAULT ('Low'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_alr_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_alr_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_alr_tenant ON dbo.reorder_alerts (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.locations,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.locations AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_levels,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_levels AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.stock_transfers AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.reorder_alerts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.reorder_alerts AFTER INSERT;
GO

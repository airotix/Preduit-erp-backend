/* ============================================================================
 * Preduit ERP — V019__shipments.sql
 * Shipments + carriers + shipment contents (line items). Run after V018.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.shipments (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_ship PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ship_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ship_tenant REFERENCES dbo.tenants(id),
    shipment_no NVARCHAR(32)     NULL,
    order_ref   NVARCHAR(40)     NULL,
    carrier     NVARCHAR(120)    NULL,
    destination NVARCHAR(160)    NULL,
    status      NVARCHAR(24)     NOT NULL CONSTRAINT df_ship_status DEFAULT ('Label created'),
    eta         NVARCHAR(40)     NULL,
    is_deleted  BIT              NOT NULL CONSTRAINT df_ship_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_ship_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ship_tenant ON dbo.shipments (tenant_id);
GO

CREATE TABLE dbo.carriers (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_carrier PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_car_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_car_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    service     NVARCHAR(80)     NULL,
    avg_transit NVARCHAR(40)     NULL,
    on_time_pct INT              NOT NULL CONSTRAINT df_car_ontime DEFAULT (0),
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_car_status DEFAULT ('Active'),
    is_deleted  BIT              NOT NULL CONSTRAINT df_car_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_car_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_car_tenant ON dbo.carriers (tenant_id);
GO

CREATE TABLE dbo.shipment_lines (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_shipline PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_sl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sl_tenant REFERENCES dbo.tenants(id),
    shipment_id BIGINT           NOT NULL CONSTRAINT fk_sl_ship REFERENCES dbo.shipments(id),
    sku         NVARCHAR(64)     NULL,
    description NVARCHAR(200)    NOT NULL,
    qty         INT              NOT NULL CONSTRAINT df_sl_qty DEFAULT (0)
);
GO
CREATE INDEX ix_sl_ship ON dbo.shipment_lines (tenant_id, shipment_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipments,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipments AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.carriers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.carriers AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipment_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.shipment_lines AFTER INSERT;
GO

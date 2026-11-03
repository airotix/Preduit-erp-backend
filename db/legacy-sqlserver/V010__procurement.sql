/* ============================================================================
 * Preduit ERP — V010__procurement.sql
 * Procurement: suppliers (with scorecard metrics), purchase orders, goods
 * receipts. Shapes match the frontend Procurement tabs. Run after V009.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.suppliers (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_suppliers PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_sup_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_sup_tenant REFERENCES dbo.tenants(id),
    name          NVARCHAR(200)    NOT NULL,
    region        NVARCHAR(80)     NULL,
    category      NVARCHAR(120)    NULL,      -- e.g. "Knitwear · Yarn"
    country_code  NVARCHAR(4)      NULL,      -- CN / PK / TR / PT
    lead_time     NVARCHAR(40)     NULL,      -- "45 days"
    on_time_pct   INT              NOT NULL CONSTRAINT df_sup_ontime DEFAULT (0),
    defect_rate   DECIMAL(5,2)     NULL,
    price_rating  DECIMAL(3,1)     NULL,
    score         DECIMAL(3,1)     NULL,
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_sup_status DEFAULT ('New'),
    is_deleted    BIT              NOT NULL CONSTRAINT df_sup_deleted DEFAULT (0),
    row_version   ROWVERSION,
    CONSTRAINT uq_sup_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_sup_tenant ON dbo.suppliers (tenant_id);
GO

CREATE TABLE dbo.purchase_orders (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pos PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_po_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_po_tenant REFERENCES dbo.tenants(id),
    po_no            NVARCHAR(32)     NULL,
    supplier_id      BIGINT           NULL CONSTRAINT fk_po_supplier REFERENCES dbo.suppliers(id),
    supplier_name    NVARCHAR(200)    NOT NULL,
    supplier_country NVARCHAR(4)      NULL,
    item_count       INT              NOT NULL CONSTRAINT df_po_items DEFAULT (0),
    total            DECIMAL(19,4)    NOT NULL CONSTRAINT df_po_total DEFAULT (0),
    currency_code    CHAR(3)          NOT NULL CONSTRAINT df_po_ccy DEFAULT ('EUR'),
    expected         NVARCHAR(40)     NULL,
    status           NVARCHAR(24)     NOT NULL CONSTRAINT df_po_status DEFAULT ('Pending approval'),
    is_deleted       BIT              NOT NULL CONSTRAINT df_po_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_po_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_po_tenant ON dbo.purchase_orders (tenant_id);
GO

CREATE TABLE dbo.goods_receipts (
    id               BIGINT IDENTITY  NOT NULL CONSTRAINT pk_grn PRIMARY KEY,
    public_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_grn_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_grn_tenant REFERENCES dbo.tenants(id),
    grn_no           NVARCHAR(32)     NULL,
    po_ref           NVARCHAR(32)     NULL,
    supplier_name    NVARCHAR(200)    NOT NULL,
    supplier_country NVARCHAR(4)      NULL,
    line_count       INT              NOT NULL CONSTRAINT df_grn_lines DEFAULT (0),
    received_count   INT              NOT NULL CONSTRAINT df_grn_recv DEFAULT (0),
    status           NVARCHAR(20)     NOT NULL CONSTRAINT df_grn_status DEFAULT ('Expected'),
    is_deleted       BIT              NOT NULL CONSTRAINT df_grn_deleted DEFAULT (0),
    row_version      ROWVERSION,
    CONSTRAINT uq_grn_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_grn_tenant ON dbo.goods_receipts (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.suppliers,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.suppliers AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_orders,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.purchase_orders AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipts,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipts AFTER INSERT;
GO

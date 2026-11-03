/* ============================================================================
 * Preduit ERP — V003__catalog.sql
 * Phase 1 preview: the Catalog module tables, used by the backend's exemplar
 * vertical slice. Demonstrates the "add tables → extend the RLS policy" pattern.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.categories (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_categories PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_cat_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_cat_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    parent_id   BIGINT           NULL CONSTRAINT fk_cat_parent REFERENCES dbo.categories(id),
    is_active   BIT              NOT NULL CONSTRAINT df_cat_active DEFAULT (1),
    row_version ROWVERSION,
    CONSTRAINT uq_cat_name UNIQUE (tenant_id, name)
);
GO
CREATE INDEX ix_cat_tenant ON dbo.categories (tenant_id);
GO

CREATE TABLE dbo.attribute_values (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_attrval PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_attr_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_attr_tenant REFERENCES dbo.tenants(id),
    attr_type   NVARCHAR(20)     NOT NULL,  -- 'Color' | 'Size'
    value       NVARCHAR(60)     NOT NULL,
    code        NVARCHAR(20)     NOT NULL,
    row_version ROWVERSION,
    CONSTRAINT uq_attr UNIQUE (tenant_id, attr_type, code)
);
GO
CREATE INDEX ix_attr_tenant ON dbo.attribute_values (tenant_id);
GO

CREATE TABLE dbo.products (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_products PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_prod_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_prod_tenant REFERENCES dbo.tenants(id),
    title        NVARCHAR(200)    NOT NULL,
    category_id  BIGINT           NULL CONSTRAINT fk_prod_cat REFERENCES dbo.categories(id),
    season       NVARCHAR(40)     NULL,
    status       NVARCHAR(20)     NOT NULL CONSTRAINT df_prod_status DEFAULT ('Draft'), -- Active|Draft|Discontinued
    created_at   DATETIME2        NOT NULL CONSTRAINT df_prod_created DEFAULT SYSUTCDATETIME(),
    created_by   BIGINT           NULL,
    updated_at   DATETIME2        NULL,
    updated_by   BIGINT           NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_prod_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_prod_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_prod_tenant ON dbo.products (tenant_id, status);
GO

CREATE TABLE dbo.product_variants (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_variants PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_var_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_var_tenant REFERENCES dbo.tenants(id),
    product_id    BIGINT           NOT NULL CONSTRAINT fk_var_product REFERENCES dbo.products(id),
    sku           NVARCHAR(64)     NOT NULL,
    color_id      BIGINT           NULL CONSTRAINT fk_var_color REFERENCES dbo.attribute_values(id),
    size_id       BIGINT           NULL CONSTRAINT fk_var_size  REFERENCES dbo.attribute_values(id),
    barcode       NVARCHAR(64)     NULL,
    price         DECIMAL(19,4)    NOT NULL,
    currency_code CHAR(3)          NOT NULL CONSTRAINT fk_var_ccy REFERENCES dbo.currencies(code),
    status        NVARCHAR(20)     NOT NULL CONSTRAINT df_var_status DEFAULT ('Active'),
    row_version   ROWVERSION,
    CONSTRAINT uq_var_sku UNIQUE (tenant_id, sku),
    CONSTRAINT uq_var_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_var_product ON dbo.product_variants (tenant_id, product_id);
GO

/* Extend tenant isolation to the new tables. */
ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.categories,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.categories AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.attribute_values,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.attribute_values AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.products,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.products AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.product_variants,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.product_variants AFTER INSERT;
GO

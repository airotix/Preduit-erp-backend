/* ============================================================================
 * Preduit ERP — V018__quality.sql
 * Quality: inspections + defect types. Run after V017.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.inspections (
    id           BIGINT IDENTITY  NOT NULL CONSTRAINT pk_insp PRIMARY KEY,
    public_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_insp_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_insp_tenant REFERENCES dbo.tenants(id),
    inspection_no NVARCHAR(32)    NULL,
    order_ref    NVARCHAR(40)     NULL,
    stage        NVARCHAR(20)     NOT NULL CONSTRAINT df_insp_stage DEFAULT ('Final'),  -- Inline|Final
    aql          NVARCHAR(10)     NULL,
    defect_count INT              NOT NULL CONSTRAINT df_insp_def DEFAULT (0),
    result       NVARCHAR(20)     NOT NULL CONSTRAINT df_insp_res DEFAULT ('Pending'),  -- Pending|Pass|Fail
    inspector    NVARCHAR(120)    NULL,
    is_deleted   BIT              NOT NULL CONSTRAINT df_insp_deleted DEFAULT (0),
    row_version  ROWVERSION,
    CONSTRAINT uq_insp_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_insp_tenant ON dbo.inspections (tenant_id);
GO

CREATE TABLE dbo.defect_types (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_deftype PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_deft_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_deft_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(120)    NOT NULL,
    category    NVARCHAR(40)     NULL,   -- Stitching|Fabric|Trim
    severity    NVARCHAR(20)     NULL,   -- Major|Minor
    frequency   INT              NOT NULL CONSTRAINT df_deft_freq DEFAULT (0),  -- percent
    is_deleted  BIT              NOT NULL CONSTRAINT df_deft_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_deft_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_deft_tenant ON dbo.defect_types (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspections,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspections AFTER INSERT,
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.defect_types,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.defect_types AFTER INSERT;
GO

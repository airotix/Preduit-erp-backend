/* ============================================================================
 * Preduit ERP — V020__admin.sql
 * Admin fields: user role/department, role scope, and an approval-rules table.
 * (Document Library reads the documents table; Audit Log reads audit_log.)
 * Run after V019.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.users ADD
    role        NVARCHAR(60)  NULL,
    department  NVARCHAR(120) NULL,
    last_active NVARCHAR(40)  NULL;
GO

ALTER TABLE dbo.roles ADD scope NVARCHAR(200) NULL;
GO

CREATE TABLE dbo.approval_rules (
    id          BIGINT IDENTITY  NOT NULL CONSTRAINT pk_apprules PRIMARY KEY,
    public_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ar_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id   UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ar_tenant REFERENCES dbo.tenants(id),
    name        NVARCHAR(200)    NOT NULL,
    [condition] NVARCHAR(300)    NULL,
    approver    NVARCHAR(200)    NULL,
    status      NVARCHAR(20)     NOT NULL CONSTRAINT df_ar_status DEFAULT ('Active'),
    is_deleted  BIT              NOT NULL CONSTRAINT df_ar_deleted DEFAULT (0),
    row_version ROWVERSION,
    CONSTRAINT uq_ar_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ar_tenant ON dbo.approval_rules (tenant_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.approval_rules,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.approval_rules AFTER INSERT;
GO

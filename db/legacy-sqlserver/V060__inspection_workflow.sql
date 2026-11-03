/* ============================================================================
 * Preduit ERP — V060__inspection_workflow.sql
 * Phase 1 of the Quality Inspection workflow upgrade. Extends the inspections
 * table with the full inspection header + AQL sampling + disposition + linkage
 * fields, and adds the per-inspection checklist and defect line tables.
 * Idempotent. Preserves all existing columns/data. Run after V059.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* ---- inspections: enriched header, AQL, disposition, linkage ---- */
IF COL_LENGTH('dbo.inspections', 'sku') IS NULL
    ALTER TABLE dbo.inspections ADD sku NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'product') IS NULL
    ALTER TABLE dbo.inspections ADD product NVARCHAR(200) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'batch_lot') IS NULL
    ALTER TABLE dbo.inspections ADD batch_lot NVARCHAR(60) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'inspection_type') IS NULL
    ALTER TABLE dbo.inspections ADD inspection_type NVARCHAR(40) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'prod_qty') IS NULL
    ALTER TABLE dbo.inspections ADD prod_qty INT NULL;
GO
IF COL_LENGTH('dbo.inspections', 'sample_size') IS NULL
    ALTER TABLE dbo.inspections ADD sample_size INT NULL;
GO
IF COL_LENGTH('dbo.inspections', 'max_defects') IS NULL
    ALTER TABLE dbo.inspections ADD max_defects INT NULL;
GO
IF COL_LENGTH('dbo.inspections', 'inspection_date') IS NULL
    ALTER TABLE dbo.inspections ADD inspection_date DATE NULL
        CONSTRAINT df_inspections_date DEFAULT CAST(SYSUTCDATETIME() AS DATE);
GO
IF COL_LENGTH('dbo.inspections', 'started_at') IS NULL
    ALTER TABLE dbo.inspections ADD started_at DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.inspections', 'finalized_at') IS NULL
    ALTER TABLE dbo.inspections ADD finalized_at DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.inspections', 'disposition') IS NULL
    ALTER TABLE dbo.inspections ADD disposition NVARCHAR(24) NULL;   -- Rework|Hold|Scrap|RTV|ReInspection
GO
IF COL_LENGTH('dbo.inspections', 'disposition_notes') IS NULL
    ALTER TABLE dbo.inspections ADD disposition_notes NVARCHAR(400) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'assigned_to') IS NULL
    ALTER TABLE dbo.inspections ADD assigned_to NVARCHAR(120) NULL;
GO
IF COL_LENGTH('dbo.inspections', 'due_date') IS NULL
    ALTER TABLE dbo.inspections ADD due_date DATE NULL;
GO
IF COL_LENGTH('dbo.inspections', 'parent_inspection_id') IS NULL
    ALTER TABLE dbo.inspections ADD parent_inspection_id BIGINT NULL;  -- re-inspection → original
GO
IF COL_LENGTH('dbo.inspections', 'shipment_ref') IS NULL
    ALTER TABLE dbo.inspections ADD shipment_ref NVARCHAR(32) NULL;    -- linked shipment on pass
GO

/* ---- checklist lines (one row per inspection criterion) ---- */
IF OBJECT_ID('dbo.inspection_checks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.inspection_checks (
        id            BIGINT IDENTITY(1,1) PRIMARY KEY,
        public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_inschk_pub DEFAULT NEWSEQUENTIALID(),
        tenant_id     UNIQUEIDENTIFIER NOT NULL,
        inspection_id BIGINT NOT NULL,
        seq           INT NOT NULL CONSTRAINT df_inschk_seq DEFAULT 0,
        criterion     NVARCHAR(120) NOT NULL,
        requirement   NVARCHAR(200) NULL,
        target_value  DECIMAL(19,4) NULL,   -- numeric spec centre, when measurable
        tolerance     DECIMAL(19,4) NULL,   -- ± tolerance, when measurable
        actual        NVARCHAR(120) NULL,   -- free text OR numeric string
        result        NVARCHAR(12) NOT NULL CONSTRAINT df_inschk_res DEFAULT 'Pending', -- Pass|Fail|Warning|NA|Pending
        notes         NVARCHAR(400) NULL,
        is_deleted    BIT NOT NULL CONSTRAINT df_inschk_del DEFAULT 0,
        CONSTRAINT fk_inschk_ins FOREIGN KEY (inspection_id) REFERENCES dbo.inspections(id)
    );
    CREATE INDEX ix_inschk_ins ON dbo.inspection_checks (inspection_id, seq);
END
GO

/* ---- defect lines (link to defect_types catalog) ---- */
IF OBJECT_ID('dbo.inspection_defects', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.inspection_defects (
        id             BIGINT IDENTITY(1,1) PRIMARY KEY,
        public_id      UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_insdef_pub DEFAULT NEWSEQUENTIALID(),
        tenant_id      UNIQUEIDENTIFIER NOT NULL,
        inspection_id  BIGINT NOT NULL,
        defect_no      NVARCHAR(32) NULL,
        defect_type_id BIGINT NULL,          -- FK into defect_types (nullable → free text fallback)
        defect_name    NVARCHAR(120) NULL,   -- snapshot of the catalog name
        category       NVARCHAR(40) NULL,    -- snapshot
        severity       NVARCHAR(20) NULL,    -- snapshot (Critical|Major|Minor)
        qty_affected   INT NOT NULL CONSTRAINT df_insdef_qty DEFAULT 1,
        location       NVARCHAR(120) NULL,
        description    NVARCHAR(400) NULL,
        corrective     NVARCHAR(400) NULL,
        is_deleted     BIT NOT NULL CONSTRAINT df_insdef_del DEFAULT 0,
        CONSTRAINT fk_insdef_ins FOREIGN KEY (inspection_id) REFERENCES dbo.inspections(id)
    );
    CREATE INDEX ix_insdef_ins ON dbo.inspection_defects (inspection_id);
END
GO

/* ---- register the two new tables with tenant Row-Level Security ---- */
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.inspection_checks'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_checks,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_checks AFTER INSERT;
GO
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
   AND NOT EXISTS (
        SELECT 1 FROM sys.security_predicates sp
        JOIN sys.security_policies pol ON pol.object_id = sp.object_id
        WHERE pol.name = 'TenantSecurityPolicy'
          AND sp.target_object_id = OBJECT_ID('dbo.inspection_defects'))
    ALTER SECURITY POLICY dbo.TenantSecurityPolicy
        ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_defects,
        ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.inspection_defects AFTER INSERT;
GO

PRINT 'V060: inspections enriched + inspection_checks + inspection_defects created (RLS applied).';
GO

/* ============================================================================
 * Preduit ERP — V031__production_stages.sql
 * Per-order production stage timeline (Trims → Lining → Cutting → Sewing →
 * Finishing → Packed). Created when production is started. Run after V030.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

CREATE TABLE dbo.production_stages (
    id            BIGINT IDENTITY  NOT NULL CONSTRAINT pk_pstages PRIMARY KEY,
    public_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_ps_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_ps_tenant REFERENCES dbo.tenants(id),
    order_id      BIGINT           NOT NULL CONSTRAINT fk_ps_order REFERENCES dbo.production_orders(id),
    seq           INT              NOT NULL,
    name          NVARCHAR(40)     NOT NULL,
    duration_days INT              NOT NULL CONSTRAINT df_ps_dur DEFAULT (0),
    status        NVARCHAR(16)     NOT NULL CONSTRAINT df_ps_status DEFAULT ('Pending'),  -- Pending|In Progress|Completed
    start_on      DATE             NULL,
    end_on        DATE             NULL,
    worker        NVARCHAR(120)    NULL,
    notes         NVARCHAR(400)    NULL,
    is_deleted    BIT              NOT NULL CONSTRAINT df_ps_deleted DEFAULT (0),
    CONSTRAINT uq_ps_public UNIQUE (public_id)
);
GO
CREATE INDEX ix_ps_order ON dbo.production_stages (tenant_id, order_id, seq);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_stages,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.production_stages AFTER INSERT;
GO
PRINT 'production_stages created.';
GO

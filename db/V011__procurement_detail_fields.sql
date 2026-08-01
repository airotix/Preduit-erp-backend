/* ============================================================================
 * Preduit ERP — V011__procurement_detail_fields.sql
 * Fields the Procurement DETAIL pages show: supplier contact, goods-receipt
 * header info, and receipt line items. Run after V010.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.suppliers ADD
    email   NVARCHAR(256) NULL,
    phone   NVARCHAR(40)  NULL,
    address NVARCHAR(300) NULL;
GO

ALTER TABLE dbo.goods_receipts ADD
    received_date NVARCHAR(40)  NULL,
    location      NVARCHAR(120) NULL;
GO

CREATE TABLE dbo.goods_receipt_lines (
    id         BIGINT IDENTITY  NOT NULL CONSTRAINT pk_grn_lines PRIMARY KEY,
    public_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT df_grnl_pub DEFAULT NEWSEQUENTIALID(),
    tenant_id  UNIQUEIDENTIFIER NOT NULL CONSTRAINT fk_grnl_tenant REFERENCES dbo.tenants(id),
    grn_id     BIGINT           NOT NULL CONSTRAINT fk_grnl_grn REFERENCES dbo.goods_receipts(id),
    name       NVARCHAR(200)    NOT NULL,
    sku        NVARCHAR(64)     NULL,
    ordered    INT              NOT NULL CONSTRAINT df_grnl_ord DEFAULT (0),
    received   INT              NOT NULL CONSTRAINT df_grnl_recv DEFAULT (0)
);
GO
CREATE INDEX ix_grnl_grn ON dbo.goods_receipt_lines (tenant_id, grn_id);
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipt_lines,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.goods_receipt_lines AFTER INSERT;
GO

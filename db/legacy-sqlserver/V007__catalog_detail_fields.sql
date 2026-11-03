/* ============================================================================
 * Preduit ERP — V007__catalog_detail_fields.sql
 * Adds the fields the product DETAIL page shows, so the backend can feed the
 * frontend's original shape with real data:
 *   - product specs (composition, gauge, care, origin, HS code, weight)
 *   - per-variant on-hand quantity (drives the variant matrix cells)
 *   - color swatch hex + size sort order (attribute_values)
 * Run in SSMS against Preduit-ERP after V006.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.products ADD
    composition NVARCHAR(120) NULL,
    gauge       NVARCHAR(40)  NULL,
    care        NVARCHAR(120) NULL,
    origin      NVARCHAR(80)  NULL,
    hs_code     NVARCHAR(20)  NULL,
    weight      NVARCHAR(20)  NULL;
GO

ALTER TABLE dbo.product_variants
    ADD qty_on_hand INT NOT NULL CONSTRAINT df_var_qty DEFAULT (0);
GO

ALTER TABLE dbo.attribute_values ADD
    hex        NVARCHAR(9) NULL,
    sort_order INT NOT NULL CONSTRAINT df_attr_sort DEFAULT (0);
GO

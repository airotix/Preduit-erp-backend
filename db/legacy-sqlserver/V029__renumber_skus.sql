/* ============================================================================
 * Preduit ERP — V029__renumber_skus.sql
 * Renumber existing product variant SKUs to the uniform 6-digit format
 * SKU-000001 … per tenant (ordered by id). New SKUs continue the sequence.
 * Run after V028. (Order/receipt line SKUs are historical snapshots and are
 * intentionally left as-is; reorder alerts are re-pointed to the new SKUs.)
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

;WITH v AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY id) AS rn
      FROM dbo.product_variants
)
UPDATE pv
   SET sku = 'SKU-' + RIGHT('000000' + CAST(v.rn AS VARCHAR(6)), 6)
  FROM dbo.product_variants pv
  JOIN v ON v.id = pv.id;
GO

/* Keep reorder alerts pointed at their variant's new SKU. */
UPDATE ra
   SET sku = pv.sku
  FROM dbo.reorder_alerts ra
  JOIN dbo.product_variants pv ON pv.id = ra.variant_id;
GO

PRINT 'Variant SKUs renumbered to SKU-000000 format.';
GO

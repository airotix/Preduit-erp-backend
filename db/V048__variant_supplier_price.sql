/* ============================================================================
 * Preduit ERP — V048__variant_supplier_price.sql
 * Add a fourth price type per SKU: supplier price (the cost paid to suppliers,
 * used throughout procurement — PO creation through to the supplier invoice).
 * Backfilled from the existing base price. Idempotent. Run after V047.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.product_variants', 'supplier_price') IS NULL
    ALTER TABLE dbo.product_variants ADD supplier_price DECIMAL(19,4) NULL;
GO

/* Backfill from the existing single price so PO pricing has a sensible default. */
UPDATE dbo.product_variants
   SET supplier_price = COALESCE(supplier_price, price)
 WHERE supplier_price IS NULL;
GO

PRINT 'Variant supplier price added.';
GO

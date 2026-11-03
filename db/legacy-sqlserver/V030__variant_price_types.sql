/* ============================================================================
 * Preduit ERP — V030__variant_price_types.sql
 * Three price types per SKU: retail, wholesale, online. Existing `price` is
 * kept as the base (= retail) for back-compat. Run after V029.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.product_variants ADD
    retail_price    DECIMAL(19,4) NULL,
    wholesale_price DECIMAL(19,4) NULL,
    online_price    DECIMAL(19,4) NULL;
GO

/* Backfill all three from the existing single price. */
UPDATE dbo.product_variants
   SET retail_price    = COALESCE(retail_price, price),
       wholesale_price = COALESCE(wholesale_price, price),
       online_price    = COALESCE(online_price, price)
 WHERE retail_price IS NULL OR wholesale_price IS NULL OR online_price IS NULL;
GO

PRINT 'Variant price types (retail/wholesale/online) added.';
GO

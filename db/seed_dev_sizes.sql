/* ============================================================================
 * Preduit ERP — seed_dev_sizes.sql  (DEV ONLY)
 * Seeds the full apparel size scale (XS … 7XL) into the Size attribute catalog
 * so the stock color × size matrix shows every size column (0 where unstocked).
 * Idempotent: inserts missing sizes, fixes sort_order on existing ones.
 * Run any time; re-runnable. (erp_system is RLS-exempt for provisioning.)
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

;WITH scale(value, code, ord) AS (
    SELECT 'XS','XS',1  UNION ALL
    SELECT 'S','S',2    UNION ALL
    SELECT 'M','M',3    UNION ALL
    SELECT 'L','L',4    UNION ALL
    SELECT 'XL','XL',5  UNION ALL
    SELECT '2XL','2XL',6 UNION ALL
    SELECT '3XL','3XL',7 UNION ALL
    SELECT '4XL','4XL',8 UNION ALL
    SELECT '5XL','5XL',9 UNION ALL
    SELECT '6XL','6XL',10 UNION ALL
    SELECT '7XL','7XL',11
)
MERGE dbo.attribute_values AS tgt
USING (SELECT @t AS tenant_id, value, code, ord FROM scale) AS src
   ON  tgt.tenant_id = src.tenant_id
   AND tgt.attr_type = 'Size'
   AND tgt.code = src.code
WHEN MATCHED THEN
    UPDATE SET sort_order = src.ord
WHEN NOT MATCHED THEN
    INSERT (tenant_id, attr_type, value, code, sort_order)
    VALUES (src.tenant_id, 'Size', src.value, src.code, src.ord);

REVERT;
GO
PRINT 'Full size scale (XS…7XL) seeded.';
GO

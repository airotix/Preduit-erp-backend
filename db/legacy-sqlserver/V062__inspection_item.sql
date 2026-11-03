/* ============================================================================
 * Preduit ERP — V062__inspection_item.sql
 * Per-item inspections: a production order with several items (production lines)
 * now gets one inspection per item, all sharing the order reference. This adds
 * the item label so multiple inspections can coexist under one order.
 * Idempotent. Run after V061.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.inspections', 'item') IS NULL
    ALTER TABLE dbo.inspections ADD item NVARCHAR(200) NULL;
GO

PRINT 'V062: inspections.item added.';
GO

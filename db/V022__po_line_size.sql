/* ============================================================================
 * Preduit ERP — V022__po_line_size.sql
 * Adds a per-size breakdown to purchase order lines. Each (item, color, size)
 * with a quantity becomes its own line. Run after V021.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.purchase_order_lines ADD size NVARCHAR(60) NULL;
GO
PRINT 'purchase_order_lines.size added.';
GO

/* ============================================================================
 * Preduit ERP — V038__sales_order_line_variants.sql
 * Adds colour + per-size breakdown to sales order lines, mirroring
 * purchase_order_lines. Each (item, colour, size) with a quantity becomes its
 * own line — the New Order form now captures articles the same way the New PO
 * form does. Run after V037.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.sales_order_lines ADD color NVARCHAR(60) NULL;
GO
ALTER TABLE dbo.sales_order_lines ADD size NVARCHAR(60) NULL;
GO
PRINT 'sales_order_lines.color + size added.';
GO

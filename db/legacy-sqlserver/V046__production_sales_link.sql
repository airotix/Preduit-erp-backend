/* ============================================================================
 * Preduit ERP — V046__production_sales_link.sql
 * Link a production (work) order back to the sales order that spawned it, so a
 * single work order can represent the whole order and its detail can show the
 * order's line items. Run after V045.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.production_orders ADD sales_order_id BIGINT NULL;
GO
PRINT 'production_orders.sales_order_id added.';
GO

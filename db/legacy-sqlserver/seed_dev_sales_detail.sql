/* ============================================================================
 * Preduit ERP — seed_dev_sales_detail.sql  (DEV ONLY)
 * Customer contact details + a few orders/invoices WITH line items so the
 * Sales detail pages are populated. Run after V009. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* Customer contact info */
UPDATE dbo.customers SET phone='+33 4 72 00 11 22',
       address='14 Rue de la République, 69002 Lyon, France'
 WHERE tenant_id=@t AND name='Maison Lyon';
UPDATE dbo.customers SET phone='+46 8 123 4567',
       address='Kungsgatan 12, 111 43 Stockholm, Sweden'
 WHERE tenant_id=@t AND name='Nordic Retail Group';
UPDATE dbo.customers SET phone='+65 6123 4567', address='1 Orchard Road, Singapore'
 WHERE tenant_id=@t AND name='Amelia Chen';

/* Orders with line items */
IF NOT EXISTS (SELECT 1 FROM dbo.sales_orders WHERE tenant_id=@t)
BEGIN
    DECLARE @ml BIGINT = (SELECT id FROM dbo.customers WHERE tenant_id=@t AND name='Maison Lyon');
    DECLARE @nr BIGINT = (SELECT id FROM dbo.customers WHERE tenant_id=@t AND name='Nordic Retail Group');

    INSERT INTO dbo.sales_orders (tenant_id, order_no, customer_id, customer_name, channel, item_count, total, status)
    VALUES (@t, 'SO-12001', @ml, 'Maison Lyon',         'Wholesale', 12, 4820, 'Picking'),
           (@t, 'SO-12002', @nr, 'Nordic Retail Group', 'Wholesale', 46, 18240, 'New');

    DECLARE @o1 BIGINT = (SELECT id FROM dbo.sales_orders WHERE tenant_id=@t AND order_no='SO-12001');
    INSERT INTO dbo.sales_order_lines (tenant_id, order_id, sku, name, qty, price, line_total) VALUES
      (@t, @o1, 'KNT-NVY-M', 'Merino Crew Knit · Navy · M', 4, 129, 516),
      (@t, @o1, 'OXF-CRM-M', 'Oxford Shirt · Cream · M',    3, 69,  207),
      (@t, @o1, 'CHN-NVY-M', 'Tailored Chino · Navy · M',   3, 89,  267);
END

/* Invoices with line items */
IF NOT EXISTS (SELECT 1 FROM dbo.invoices WHERE tenant_id=@t)
BEGIN
    DECLARE @ml2 BIGINT = (SELECT id FROM dbo.customers WHERE tenant_id=@t AND name='Maison Lyon');
    INSERT INTO dbo.invoices (tenant_id, invoice_no, customer_id, customer_name, due_date, amount, status)
    VALUES (@t, 'INV-8841', @ml2, 'Maison Lyon', '14 Jul 2026', 4820, 'Open');

    DECLARE @i1 BIGINT = (SELECT id FROM dbo.invoices WHERE tenant_id=@t AND invoice_no='INV-8841');
    INSERT INTO dbo.invoice_lines (tenant_id, invoice_id, sku, name, qty, price, line_total) VALUES
      (@t, @i1, 'KNT-NVY-M', 'Merino Crew Knit · Navy · M', 4, 129, 516),
      (@t, @i1, 'OXF-CRM-M', 'Oxford Shirt · Cream · M',    3, 69,  207);
END

REVERT;
GO
PRINT 'Sales detail demo data seeded.';
GO

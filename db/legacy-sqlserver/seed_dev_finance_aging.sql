/* ============================================================================
 * Preduit ERP — seed_dev_finance_aging.sql  (DEV ONLY)
 * Real due dates on invoices (AR) + supplier bills (AP), spread across age
 * buckets so the aging reports show a realistic picture. Run after V015.
 * Dates are around late-July 2026 (the demo "today").
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* AR: give the seeded invoice a real due date, and add two more unpaid ones. */
UPDATE dbo.invoices SET due_on = '2026-07-14' WHERE tenant_id=@t AND invoice_no='INV-8841';

IF NOT EXISTS (SELECT 1 FROM dbo.invoices WHERE tenant_id=@t AND invoice_no='INV-8842')
BEGIN
    DECLARE @nr BIGINT = (SELECT id FROM dbo.customers WHERE tenant_id=@t AND name='Nordic Retail Group');
    DECLARE @sn BIGINT = (SELECT id FROM dbo.customers WHERE tenant_id=@t AND name='Amelia Chen');
    INSERT INTO dbo.invoices (tenant_id, invoice_no, customer_id, customer_name, due_date, due_on, amount, status) VALUES
      (@t, 'INV-8842', @nr, 'Nordic Retail Group', '10 Aug 2026', '2026-08-10', 8100,  'Open'),
      (@t, 'INV-8843', @sn, 'Amelia Chen',          '20 May 2026', '2026-05-20', 11900, 'Open');
END

/* AP: supplier bills (payables), spread across buckets. */
IF NOT EXISTS (SELECT 1 FROM dbo.supplier_bills WHERE tenant_id=@t)
    INSERT INTO dbo.supplier_bills (tenant_id, bill_no, supplier_name, po_ref, amount, due_on, status) VALUES
      (@t, 'BILL-1201', 'Anhui Knit Mills',   'PO-5582', 42800, '2026-08-05', 'Open'),
      (@t, 'BILL-1202', 'Bursa Denim A.S.',   'PO-5584', 19400, '2026-05-30', 'Open'),
      (@t, 'BILL-1203', 'Lahore Textile Co.', 'PO-5581', 12000, '2026-07-20', 'Open');

REVERT;
GO
PRINT 'Finance aging sources seeded.';
GO

/* ============================================================================
 * Preduit ERP — seed_dev_finance_ledgers.sql  (DEV ONLY)
 * Customer/supplier codes + terms, and demo receipts / credit note / payment
 * that attach BY ID to a party which actually has invoices/bills — so ledger
 * balances stay positive and correct. Run after V027. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

UPDATE dbo.customers
   SET code = 'CUS-' + CAST(1000 + id AS NVARCHAR(10)), terms = COALESCE(terms, 'Net 30')
 WHERE tenant_id = @t AND code IS NULL;
UPDATE dbo.suppliers
   SET code = 'SUP-' + CAST(2000 + id AS NVARCHAR(10)), terms = COALESCE(terms, 'Net 60')
 WHERE tenant_id = @t AND code IS NULL;

/* Remove the earlier mismatched demo rows (attached to parties with no debits). */
DELETE FROM dbo.payments     WHERE tenant_id = @t AND payment_no IN ('RCPT-9001', 'PAY-9001');
DELETE FROM dbo.credit_notes WHERE tenant_id = @t AND cn_no = 'CN-2209';

/* ---- Customer: pick one that actually has invoices ---- */
DECLARE @cust_id BIGINT = (
    SELECT TOP 1 customer_id FROM dbo.invoices
     WHERE tenant_id = @t AND customer_id IS NOT NULL AND is_deleted = 0
     GROUP BY customer_id ORDER BY COUNT(*) DESC);
DECLARE @cust NVARCHAR(200) = (SELECT name FROM dbo.customers WHERE id = @cust_id);
DECLARE @inv_total DECIMAL(19,4) = (
    SELECT COALESCE(SUM(amount), 0) FROM dbo.invoices
     WHERE tenant_id = @t AND customer_id = @cust_id AND is_deleted = 0);

IF @cust_id IS NOT NULL AND @inv_total > 0
BEGIN
    INSERT INTO dbo.payments (tenant_id, payment_no, pay_date, party, party_type, party_id, amount, pay_type, status)
      VALUES (@t, 'RCPT-9001', '10 Jul', @cust, 'customer', @cust_id, ROUND(@inv_total * 0.40, 0), 'Receipt', 'Cleared');
    INSERT INTO dbo.credit_notes (tenant_id, cn_no, customer_id, customer_name, cn_date, amount, reason)
      VALUES (@t, 'CN-2209', @cust_id, @cust, '2026-07-14', ROUND(@inv_total * 0.05, 0), 'Credit note — service adjustment');
END

/* ---- Supplier: pick one that actually has bills ---- */
DECLARE @sup_id BIGINT = (
    SELECT TOP 1 supplier_id FROM dbo.supplier_bills
     WHERE tenant_id = @t AND supplier_id IS NOT NULL AND is_deleted = 0
     GROUP BY supplier_id ORDER BY COUNT(*) DESC);
DECLARE @sup NVARCHAR(200) = (SELECT name FROM dbo.suppliers WHERE id = @sup_id);
DECLARE @bill_total DECIMAL(19,4) = (
    SELECT COALESCE(SUM(amount), 0) FROM dbo.supplier_bills
     WHERE tenant_id = @t AND supplier_id = @sup_id AND is_deleted = 0);

IF @sup_id IS NOT NULL AND @bill_total > 0
    INSERT INTO dbo.payments (tenant_id, payment_no, pay_date, party, party_type, party_id, amount, pay_type, status)
      VALUES (@t, 'PAY-9001', '12 Jul', @sup, 'supplier', @sup_id, ROUND(@bill_total * 0.40, 0), 'Disbursement', 'Cleared');

REVERT;
GO
PRINT 'Finance ledger demo data reseeded (linked by id).';
GO

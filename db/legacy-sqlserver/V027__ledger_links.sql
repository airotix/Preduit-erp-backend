/* ============================================================================
 * Preduit ERP — V027__ledger_links.sql
 * Link ledger documents to their party by ID (not name): supplier_id on bills,
 * party_type/party_id on payments, and backfill invoice/bill/CN/payment links
 * from the existing name matches. Run after V026.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.supplier_bills ADD supplier_id BIGINT NULL;
GO
ALTER TABLE dbo.payments ADD
    party_type NVARCHAR(20) NULL,   -- 'customer' | 'supplier'
    party_id   BIGINT       NULL;
GO

/* Backfill links from names. */
UPDATE inv SET customer_id = c.id
  FROM dbo.invoices inv
  JOIN dbo.customers c ON c.tenant_id = inv.tenant_id AND c.name = inv.customer_name
 WHERE inv.customer_id IS NULL;
GO

UPDATE b SET supplier_id = s.id
  FROM dbo.supplier_bills b
  JOIN dbo.suppliers s ON s.tenant_id = b.tenant_id AND s.name = b.supplier_name
 WHERE b.supplier_id IS NULL;
GO

UPDATE cn SET customer_id = c.id
  FROM dbo.credit_notes cn
  JOIN dbo.customers c ON c.tenant_id = cn.tenant_id AND c.name = cn.customer_name
 WHERE cn.customer_id IS NULL;
GO

UPDATE p SET party_type = 'customer', party_id = c.id
  FROM dbo.payments p
  JOIN dbo.customers c ON c.tenant_id = p.tenant_id AND c.name = p.party
 WHERE p.pay_type = 'Receipt' AND p.party_id IS NULL;
GO

UPDATE p SET party_type = 'supplier', party_id = s.id
  FROM dbo.payments p
  JOIN dbo.suppliers s ON s.tenant_id = p.tenant_id AND s.name = p.party
 WHERE p.pay_type <> 'Receipt' AND p.party_id IS NULL;
GO

PRINT 'Ledger party links added and backfilled.';
GO

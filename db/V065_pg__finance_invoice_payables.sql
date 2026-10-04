BEGIN;
-- Previous supplier opening balances used positive=payable. Convert once,
-- preserving their meaning under positive=advance / negative=payable.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'po_invoices' AND column_name = 'supplier_bill_id') THEN
    UPDATE suppliers SET opening_balance = -opening_balance WHERE opening_balance <> 0;
  END IF;
END $$;
ALTER TABLE po_invoices ADD COLUMN IF NOT EXISTS supplier_bill_id BIGINT REFERENCES supplier_bills(id);
CREATE INDEX IF NOT EXISTS ix_po_invoice_bill ON po_invoices (tenant_id, supplier_bill_id);
-- The supplier is shown in the invoice's buyer box; older summaries incorrectly
-- persisted the company's exporter name. Use the actual PO supplier.
UPDATE po_invoices i SET supplier_name = p.supplier_name
FROM purchase_orders p
WHERE i.tenant_id = p.tenant_id AND i.po_no = p.po_no
  AND NOT i.is_deleted AND NOT p.is_deleted;
-- Link only unambiguous existing payables; leave their values and payments intact.
UPDATE po_invoices i SET supplier_bill_id = b.id
FROM (
  SELECT tenant_id, po_ref, min(id) AS id FROM supplier_bills
  WHERE NOT is_deleted AND po_ref IS NOT NULL
  GROUP BY tenant_id, po_ref HAVING count(*) = 1
) b
WHERE i.tenant_id = b.tenant_id AND i.po_no = b.po_ref
  AND NOT i.is_deleted AND i.supplier_bill_id IS NULL;
COMMIT;

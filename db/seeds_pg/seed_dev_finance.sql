/* PostgreSQL port of ../seed_dev_finance.sql, seed_dev_finance_aging.sql and
 * seed_dev_finance_ledgers.sql (DEV ONLY), in that order.
 *
 * Schema adaptations (the finance model changed after these seeds were written):
 *   - Chart of accounts is only seeded when the tenant has none; the app bootstraps its
 *     own chart (Cash/Bank/AR/Inventory/AP/Revenue/COGS) on first use, and the old
 *     codes conflict with it. Balances are no longer hard-coded: documents are inserted
 *     UNPOSTED and the app's own post_unposted step builds the journals (run_seeds.py).
 *   - JE-4471 (hand-written journal for an order that is not in the data) is not seeded;
 *     the draft payroll accrual JE-4468 is.
 *   - Payments carry method/payment_type 'bank' so they post to the Bank account.
 *   - Supplier bills are linked by supplier_id (V051) so the party ledgers and the
 *     PAY-9001 allocation below can find them. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid;
        nr bigint; sn bigint; cust_id bigint; cust text; inv_total numeric;
        sup_id bigint; sup text; bill_total numeric;
BEGIN
  -- ---- seed_dev_finance ----
  IF NOT EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id=t) THEN
    INSERT INTO chart_of_accounts (tenant_id, code, name, acct_type) VALUES
      (t,'1000','Cash','Asset'),(t,'1100','Bank','Asset'),(t,'1200','Accounts receivable','Asset'),
      (t,'1300','Inventory','Asset'),(t,'2000','Accounts payable','Liability'),
      (t,'4000','Revenue','Income'),(t,'5000','Cost of goods sold','Expense');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM journal_entries WHERE tenant_id=t AND entry_no='JE-4468') THEN
    INSERT INTO journal_entries (tenant_id, entry_no, entry_date, memo, total_debit, total_credit, status)
    VALUES (t,'JE-4468','24 Jun','Payroll accrual · June',84200,84200,'Draft');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM payments WHERE tenant_id=t AND payment_no IN ('PMT-2231','PMT-2230','PMT-2229')) THEN
    INSERT INTO payments (tenant_id, payment_no, pay_date, party, allocated_to, amount, pay_type, status, method, payment_type) VALUES
      (t,'PMT-2231','25 Jun','Boutique Atlas',    'INV-8839', 9460,'Receipt',     'Cleared','Bank','bank'),
      (t,'PMT-2230','24 Jun','Lahore Textile Co.','BILL-1182',28400,'Disbursement','Cleared','Bank','bank'),
      (t,'PMT-2229','23 Jun','Maison Lyon',       'INV-8841', 2400,'Receipt',     'Pending','Bank','bank');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM ar_aging WHERE tenant_id=t) THEN
    INSERT INTO ar_aging (tenant_id, customer_name, region, current_amt, b1_30, b31_60, b61_90, b90_plus) VALUES
      (t,'Maison Lyon','France',2420,2400,0,0,0),
      (t,'Nordic Retail Group','Sweden',8100,0,0,0,0),
      (t,'Studio Norte','Portugal',0,0,11900,0,0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM ap_aging WHERE tenant_id=t) THEN
    INSERT INTO ap_aging (tenant_id, supplier_name, region, current_amt, b1_30, b31_60, b61_90, b90_plus) VALUES
      (t,'Anhui Knit Mills','China',42800,0,0,0,0),
      (t,'Bursa Denim A.S.','Turkey',0,0,19400,0,0);
  END IF;

  -- ---- seed_dev_finance_aging ----
  UPDATE invoices SET due_on='2026-07-14' WHERE tenant_id=t AND invoice_no='INV-8841';
  IF NOT EXISTS (SELECT 1 FROM invoices WHERE tenant_id=t AND invoice_no='INV-8842') THEN
    SELECT id INTO nr FROM customers WHERE tenant_id=t AND name='Nordic Retail Group';
    SELECT id INTO sn FROM customers WHERE tenant_id=t AND name='Amelia Chen';
    INSERT INTO invoices (tenant_id, invoice_no, customer_id, customer_name, due_date, due_on, amount, status, order_no) VALUES
      (t,'INV-8842',nr,'Nordic Retail Group','10 Aug 2026','2026-08-10', 8100,'Open','SO-12002'),
      (t,'INV-8843',sn,'Amelia Chen',        '20 May 2026','2026-05-20',11900,'Open',NULL);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM supplier_bills WHERE tenant_id=t) THEN
    INSERT INTO supplier_bills (tenant_id, bill_no, supplier_name, supplier_id, po_ref, amount, due_on, status)
    SELECT t, x.bill_no, x.sup, (SELECT id FROM suppliers WHERE tenant_id=t AND name=x.sup), x.po, x.amt, x.due::date, 'Open'
      FROM (VALUES ('BILL-1201','Anhui Knit Mills','PO-5582',42800,'2026-08-05'),
                   ('BILL-1202','Bursa Denim A.S.','PO-5584',19400,'2026-05-30'),
                   ('BILL-1203','Lahore Textile Co.','PO-5581',12000,'2026-07-20')) x(bill_no,sup,po,amt,due);
  END IF;

  -- ---- seed_dev_finance_ledgers ----
  UPDATE customers SET code='CUS-'||(1000+id)::text, terms=COALESCE(terms,'Net 30') WHERE tenant_id=t AND code IS NULL;
  UPDATE suppliers SET code='SUP-'||(2000+id)::text, terms=COALESCE(terms,'Net 60') WHERE tenant_id=t AND code IS NULL;
  -- (the original deleted and re-created RCPT-9001 / PAY-9001 / CN-2209; those rows are now
  --  posted to the GL, so they are inserted once and left alone on re-runs)

  SELECT customer_id INTO cust_id FROM invoices
   WHERE tenant_id=t AND customer_id IS NOT NULL AND NOT is_deleted
   GROUP BY customer_id ORDER BY COUNT(*) DESC, customer_id LIMIT 1;
  SELECT name INTO cust FROM customers WHERE id=cust_id;
  SELECT COALESCE(SUM(amount),0) INTO inv_total FROM invoices WHERE tenant_id=t AND customer_id=cust_id AND NOT is_deleted;
  IF cust_id IS NOT NULL AND inv_total > 0 AND NOT EXISTS (SELECT 1 FROM payments WHERE tenant_id=t AND payment_no='RCPT-9001') THEN
    INSERT INTO payments (tenant_id, payment_no, pay_date, party, party_type, party_id, amount, pay_type, status, method, payment_type)
    VALUES (t,'RCPT-9001','10 Jul',cust,'customer',cust_id,ROUND(inv_total*0.40,0),'Receipt','Cleared','Bank','bank');
    IF NOT EXISTS (SELECT 1 FROM credit_notes WHERE tenant_id=t AND cn_no='CN-2209') THEN
      INSERT INTO credit_notes (tenant_id, cn_no, customer_id, customer_name, cn_date, amount, reason)
      VALUES (t,'CN-2209',cust_id,cust,'2026-07-14',ROUND(inv_total*0.05,0),'Credit note — service adjustment');
    END IF;
  END IF;

  SELECT supplier_id INTO sup_id FROM supplier_bills
   WHERE tenant_id=t AND supplier_id IS NOT NULL AND NOT is_deleted
   GROUP BY supplier_id ORDER BY COUNT(*) DESC, supplier_id LIMIT 1;
  SELECT name INTO sup FROM suppliers WHERE id=sup_id;
  SELECT COALESCE(SUM(amount),0) INTO bill_total FROM supplier_bills WHERE tenant_id=t AND supplier_id=sup_id AND NOT is_deleted;
  IF sup_id IS NOT NULL AND bill_total > 0 AND NOT EXISTS (SELECT 1 FROM payments WHERE tenant_id=t AND payment_no='PAY-9001') THEN
    INSERT INTO payments (tenant_id, payment_no, pay_date, party, party_type, party_id, amount, pay_type, status, method, payment_type)
    VALUES (t,'PAY-9001','12 Jul',sup,'supplier',sup_id,ROUND(bill_total*0.40,0),'Disbursement','Cleared','Bank','bank');
  END IF;
END $$;

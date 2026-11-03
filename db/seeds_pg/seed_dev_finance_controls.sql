/* PostgreSQL port of ../seed_dev_fx.sql, seed_dev_gl.sql, seed_dev_bank.sql and
 * seed_dev_controls.sql (DEV ONLY), in that order.
 * Schema adaptations: the opening-balance carry-over and equity plug from seed_dev_gl
 * are omitted (balances now come from posted journals, so copying a stored balance would
 * double count); only normal_side and a zero-balance Retained earnings account are set.
 * The demo bank account maps to GL code 1100 (Bank) in the app's chart, not 1000. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid; ba bigint; d date := '2026-01-01';
BEGIN
  -- ---- fx ----
  IF NOT EXISTS (SELECT 1 FROM exchange_rates WHERE tenant_id=t) THEN
    INSERT INTO exchange_rates (tenant_id, from_ccy, to_ccy, rate, valid_from, source) VALUES
      (t,'USD','EUR',0.92590000,d,'seed'),(t,'USD','PKR',285.71000000,d,'seed'),(t,'USD','AED',3.67200000,d,'seed'),
      (t,'EUR','USD',1.08000000,d,'seed'),(t,'EUR','PKR',308.57000000,d,'seed'),(t,'EUR','AED',3.96600000,d,'seed'),
      (t,'PKR','USD',0.00350000,d,'seed'),(t,'PKR','EUR',0.00324100,d,'seed'),(t,'PKR','AED',0.01285400,d,'seed'),
      (t,'AED','USD',0.27230000,d,'seed'),(t,'AED','EUR',0.25213000,d,'seed'),(t,'AED','PKR',77.80000000,d,'seed');
  END IF;

  -- ---- gl ----
  UPDATE chart_of_accounts SET normal_side = CASE WHEN acct_type IN ('Asset','Expense') THEN 'D' ELSE 'C' END
   WHERE tenant_id=t AND normal_side IS NULL;
  IF NOT EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id=t AND acct_type='Equity') THEN
    INSERT INTO chart_of_accounts (tenant_id, code, name, acct_type, currency_code, opening_balance, balance, normal_side, is_active)
    VALUES (t,'3000','Retained earnings b/f','Equity','EUR',0,0,'C',true);
  END IF;

  -- ---- bank ----
  IF NOT EXISTS (SELECT 1 FROM bank_accounts WHERE tenant_id=t) THEN
    INSERT INTO bank_accounts (tenant_id, name, account_no, gl_code, currency_code)
    VALUES (t,'Main current account','ACC-4821','1100','EUR');
  END IF;
  SELECT id INTO ba FROM bank_accounts WHERE tenant_id=t ORDER BY id LIMIT 1;
  IF ba IS NOT NULL AND NOT EXISTS (SELECT 1 FROM bank_transactions WHERE tenant_id=t) THEN
    INSERT INTO bank_transactions (tenant_id, bank_account_id, txn_date, description, amount, status) VALUES
      (t,ba,'2026-07-05','Customer receipt',    9460,'Unmatched'),
      (t,ba,'2026-07-08','Supplier payment',  -28400,'Unmatched'),
      (t,ba,'2026-07-12','Customer receipt',    2400,'Unmatched'),
      (t,ba,'2026-07-15','Bank service charge', -150,'Unmatched');
  END IF;

  -- ---- controls ----
  IF NOT EXISTS (SELECT 1 FROM fiscal_periods WHERE tenant_id=t) THEN
    INSERT INTO fiscal_periods (tenant_id, name, start_date, end_date, status) VALUES
      (t,'Jun 2026','2026-06-01','2026-06-30','Closed'),
      (t,'Jul 2026','2026-07-01','2026-07-31','Open'),
      (t,'Aug 2026','2026-08-01','2026-08-31','Open');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM budget_lines WHERE tenant_id=t) THEN
    INSERT INTO budget_lines (tenant_id, fiscal_year, account_code, account_name, amount) VALUES
      (t,2026,'4000','Sales revenue',5000000),(t,2026,'5000','Cost of goods sold',2800000);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM fixed_assets WHERE tenant_id=t) THEN
    INSERT INTO fixed_assets (tenant_id, asset_no, name, category, cost, salvage, life_months, in_service_date, accumulated, status) VALUES
      (t,'FA-1001','Cutting machine','Equipment', 360000,     0,36,'2026-01-01',0,'Active'),
      (t,'FA-1002','Delivery van',   'Vehicles', 1200000,120000,60,'2026-03-01',0,'Active');
  END IF;
END $$;

# Finance balances and invoice payables

The Finance dashboard shows its standard KPIs, charts and recent journals. The
explanatory balance guide was removed from the dashboard at the user's request.
The overview API still provides the ledger summary alongside its standard data.

## Ledger rules

| Ledger | Debit | Credit | Running balance |
| --- | --- | --- | --- |
| Customer | Sales invoice | Cleared receipt or credit note | Opening + Debit - Credit |
| Supplier | Cleared supplier payment | Supplier invoice/payable | Opening + Debit - Credit |
| Bank / Cash | Cleared money paid out | Cleared money received | Opening + Credit - Debit |

A positive customer balance is receivable; a negative balance is customer credit.
A negative supplier balance is payable; a positive balance is an advance paid.
Zero means settled. General ledger journals retain their normal accounting signs;
the Bank/Cash screen uses the requested money-in Credit / money-out Debit display.

## Invoice and payment flow

- Every procurement invoice creates or links its finance payable at generation,
  including invoices from sales-linked POs. The journal uses the invoice issue date,
  not the later payment due date. QC or inventory receipt does not add another bill.
- Existing PO bills are reused. Multiple active bills against one PO require
  reconciliation; the application does not guess or erase financial records.
- The Generate missing invoices action also repairs missing invoice/payable links.
  PO locking serializes automatic generation in PostgreSQL.
- Linked invoices/payables reject financial edits that would silently change their
  supplier, PO or amount independently. Nonfinancial invoice details remain editable.
- Only Cleared payments affect party balances, outstanding documents and Bank/Cash.
  Partial allocated payments reduce the remaining amount in dashboard AR/AP and aging.
  Full payment settles the document; changing a payment to Failed reopens it.
- Leave allocation blank for an advance. Edit that payment later to allocate it to
  the matching party's invoice/bill. Allocation cannot exceed that document's unpaid
  amount; any excess remains a separate unallocated advance.
- Changes to posted payment amounts, parties, methods, dates or clearing status
  reverse the old journal and post the valid replacement. Inline Bank/Cash changes
  also update the journal account. Legacy posted Pending/Failed payments are reversed
  once during posting reconciliation.
- Unpaid invoices, bills and noncash credit notes do not appear as Bank/Cash movements.
  Manual ledger entries remain explicit adjustments. Reports use posted GL journals;
  standalone manual subledger adjustments do not automatically create GL journals.

## Upgrade an existing Docker database

Apply V063 and V064 first if needed, then apply V065 before restarting the backend:

```powershell
Get-Content -Raw "C:\Users\fbc\Desktop\perp\backend\db\V065_pg__finance_invoice_payables.sql" |
  docker exec -i preduit-db psql -U erp_admin -d preduit -v ON_ERROR_STOP=1
```

V065 adds the invoice/payable link, links unambiguous existing PO bills, repairs
PO invoice supplier names, and converts supplier opening balances once to preserve
their previous payable/advance meaning under the new sign convention. It leaves
historical bill values and payments intact. Re-running it does not repeat the opening
balance conversion. Fresh databases include the link in the consolidated schema;
do not use the consolidated schema as an upgrade script.

## Verification

Regression tests exercise real posting and ledgers using isolated SQLite fixtures:
partial/full payments, advances, allocation validation, payment failures and reversal
idempotency, bank/cash method changes, opening balances, supplier name correctness,
invoice/payable backfill and duplicate prevention, invoice issue-date posting, protected
financial edits, aging and dashboard totals. PostgreSQL migration execution and live
Docker/browser verification must be performed in the running deployment.

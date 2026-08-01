# Finance — Plan to reach full-ERP parity

Goal: turn today's **subledger + reporting layer** into a real **general-ledger accounting system** like NetSuite/Odoo — where every transaction auto-posts a balanced double-entry journal, and the statutory statements are generated *from* those postings.

Guiding principle: **the General Ledger (journal_entries + journal_lines) becomes the single source of truth.** Everything posts into it; every report reads out of it.

## What we already have (reuse)
Chart of accounts (with acct_type Asset/Liability/Equity/Income/Expense), journal_entries + journal_lines, invoices, supplier_bills, payments, credit_notes, customers/suppliers with opening balances, exchange_rates, audit_log, and the 4 redesigned screens (Overview, Customer/Supplier ledger, Profitability) + currency toggle.

## The core gap
1. Journals are not enforced to **balance** and aren't the source of account balances.
2. Invoices/bills/payments **don't auto-post** to the GL.
3. No statutory **statements** (Trial Balance, P&L, Balance Sheet, Cash Flow).
4. `journal_entries.entry_date` is a free-text string → no real period reporting.
5. No bank reconciliation, period close, tax engine, budgeting, fixed assets, or transactional FX.

---

## Phase A — GL foundation & posting engine  *(backbone, do first)*
Make the ledger real and balanced.

- **Migrate `journal_entries.entry_date` → real `DATE`** (+ `posted_at`, `period_id`, `reversed_of_id`). This unblocks all period reporting.
- **`journal_lines` → link to account by `account_id` (FK)** (keep display label), numeric debit/credit.
- **Posting service** `post_journal(entry, lines)`: validates Σdebit = Σcredit, rejects unbalanced entries, sets status Draft→Posted, stamps date/user, writes audit. Add **reversing entries** (create a mirror JE).
- **Account balances derived from posted lines** (not the ad-hoc `balance` column): balance = opening + Σ(debit − credit) respecting each account's normal side. Add a `normal_side` (Dr/Cr) per acct_type.
- **Frontend**: replace single-line "New entry" with a **multi-line journal editor** (N lines, account picker, live "balanced ✓ / out by X" indicator).

## Phase B — Auto-post subledgers into the GL  *(backbone)*
Every business document writes one balanced JE, once (idempotent).

- Add `gl_journal_id` + `posted` flag to invoices, supplier_bills, payments, credit_notes.
- **Posting rules** (the standard entries):
  - Invoice posted → Dr Accounts Receivable · Cr Sales Revenue · Cr Output VAT.
  - Supplier bill → Dr Inventory/Expense · Dr Input VAT · Cr Accounts Payable.
  - Customer receipt → Dr Cash/Bank · Cr Accounts Receivable.
  - Supplier payment → Dr Accounts Payable · Cr Cash/Bank.
  - Credit note → Dr Sales Returns · Dr Output VAT · Cr Accounts Receivable.
- Result: the Customer/Supplier ledgers and the Overview feed all reconcile to the GL automatically, and account balances become real.

## Phase C — Financial statements from the GL  *(backbone)*
Query services with a period (date range) parameter, all reading posted lines:

- **Trial Balance** — every account's Dr/Cr balance; must net to zero (proves the books balance).
- **Profit & Loss** — income − expense over a period; ties to the Overview margin waterfall.
- **Balance Sheet** — assets = liabilities + equity at a date; equity includes retained earnings (cumulative prior P&L) + current-year result.
- **Cash Flow** — movement on cash/bank accounts (indirect method from P&L + working-capital changes).
- **Frontend**: a **Reports** area (new tabs or under Overview "View all") rendering these four statements with period + currency selectors.

## Phase D — Bank & cash management + reconciliation
- `bank_accounts`, `bank_statement_lines` (imported/CSV).
- Matching UI: match statement lines to payments/journals; mark reconciled; flag unmatched.

## Phase E — Fiscal periods & close controls
- `fiscal_periods` table (open/closed); **block posting to a closed period**; month-end close checklist.
- Segregation of duties via the existing roles/approval-rules; extend audit trail on posting/close.

## Phase F — Tax engine
- `tax_codes` (name, rate, type, output/input account), tax per invoice/bill **line**, and a **VAT return** report (output − input = net payable) for a period.

## Phase G — Budgeting & forecasting
- `budgets` + `budget_lines` (account × period). **Actual vs budget** variance columns on P&L.

## Phase H — Fixed assets & depreciation
- `fixed_assets` (cost, life, method) + `depreciation_schedule`; a monthly job that **auto-posts depreciation JEs**.

## Phase I — Transactional multi-currency
- Currency per document; period-end **revaluation** of open AR/AP; post **realized/unrealized FX gain/loss**. (Today currency is display-only.)

## Phase J — Multi-entity consolidation  *(optional, last)*
- Multiple legal entities, intercompany elimination, consolidated statements across entities.

---

## New / changed data model (summary)
- Change: `journal_entries` (date→DATE, +posted_at, period_id, reversed_of_id); `journal_lines` (+account_id FK); `chart_of_accounts` (+normal_side); documents (+gl_journal_id, +posted).
- New tables: `fiscal_periods`, `tax_codes`, `bank_accounts`, `bank_statement_lines`, `budgets`, `budget_lines`, `fixed_assets`, `depreciation_schedule` (and optionally `posting_rules`, `entities`).

## Recommended build order
**A → B → C first** — they deliver a real, self-proving ledger and the three core statements (this is 80% of "ERP-grade"). Then **E (close) + F (tax) + D (bank rec)** for compliance and control. Then **G, H, I** as maturity features, and **J** only if multi-entity is needed.

## Effort & risk notes
- Biggest unlock, lowest glamour: the `entry_date`→DATE migration and the posting engine (A). Everything else stands on them.
- Backfill: existing invoices/bills/payments should be posted to the GL via a one-off backfill so opening statements aren't empty.
- Keep the redesigned 4 screens as the "hero" UX; statements live in a Reports section so we don't clutter them.
- Verification per phase: Trial Balance nets to zero; ledger tile totals = GL account balances; P&L net = Balance Sheet retained-earnings movement.

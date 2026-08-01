# Finance Module — Redesign Plan

Reinvents the Finance module to match the new standalone design. Everything is wired to the **real backend/database** (extend existing finance/sales/procurement tables; add only what's missing). No mock data in the final result.

## 1. What changes

The module collapses from six list-style tabs (Chart of Accounts, Journals, Payments, AR Aging, AP Aging, Supplier Bills) to **four purpose-built screens**:

| New tab | Purpose | Replaces |
|---|---|---|
| **Overview** | Consolidated position: cash, AR, AP, revenue + margin visuals + recent journal feed | dashboards + journals list |
| **Customer ledger** | Per-customer running statement (receivables) | AR aging + invoices |
| **Supplier ledger** | Per-supplier running statement (payables) | AP aging + supplier bills |
| **Profitability** | Gross/net margin by business segment | (new) |

A global **currency toggle** (PKR / USD / EUR / AED) in the top bar converts all displayed finance amounts. Every screen has an **Export** button and one primary action (New entry / New invoice / New bill / New report).

The underlying data (accounts, journals + lines, invoices, supplier bills, payments, customers, suppliers, exchange rates) is **reused**; the old tab screens are removed from navigation but their endpoints stay available internally.

## 2. Screen specs

### 2.1 Overview
- Eyebrow "FINANCE", title "Finance overview", subtitle "Consolidated position across ledgers, margin and cash". Actions: Export, **+ New entry** (opens a journal-entry form).
- **4 KPI cards** with icon + period-over-period trend badge:
  - Cash & bank — sum of cash/bank account balances; sub "Across N accounts"; trend vs prior month.
  - Accounts receivable — sum of open invoice balances; sub "N open invoices".
  - Accounts payable — sum of open supplier-bill balances; sub "N open bills".
  - Revenue (YTD) — YTD sales revenue; sub "vs Rs X last yr"; YoY trend.
- **Revenue vs COGS** grouped bar chart, trailing 6 months (Revenue = orange, COGS = purple).
- **Margin waterfall** — horizontal bars: Revenue → Gross profit → Operating profit → Net profit, with footer Net-profit-margin % and Effective tax %.
- **Recent journal entries** table (Date, Entry #, Account badge, Memo, Debit, Credit) with "View all".

### 2.2 Customer ledger
- Title "Customer ledger", subtitle "Receivables by customer with running balance". Action: **+ New invoice**.
- **Left rail**: searchable customer list — avatar, name, code (CUS-####), current receivable (color-coded).
- **Right panel** for the selected customer:
  - Header: avatar, name, "CODE · Net terms · email", standing badge (In good standing / Payment due / Overdue).
  - 4 stat tiles: Opening, Total debit, Total credit, Receivable.
  - **Statement table**: Date, Reference (INV / RCPT / CN), Description, Debit, Credit, running Balance; Opening-balance row on top; Totals row at bottom.

### 2.3 Supplier ledger
- Same layout as customer ledger, mirrored for payables. Action: **+ New bill**.
- References: BILL (credit, ↑ payable), PAY (debit, ↓ payable). Tiles: Opening, Total debit, Total credit, Payable. Standing: Payment due / Current.

### 2.4 Profitability
- Title "Profitability report", subtitle "Gross and net margin by business segment". Actions: Export, **+ New report**.
- **4 KPI cards**: Total revenue (YTD), Gross profit (+ gross-margin %), Net profit (+ after-tax note), Net margin % (vs target).
- **Profitability by segment** table: Segment, Revenue, COGS, Gross profit, GM % (badge — green healthy / amber low), Opex, Tax, Net profit, NM %; highlighted Totals row. Segment filter top-right.

## 3. Data mapping (figure → source → status)

**exists** = already in DB · **compute** = derive from existing rows · **new** = needs schema/endpoint.

Overview
- Cash & bank → sum of journal-line balances for accounts of type Cash/Bank (chart_of_accounts + journal_lines) — **compute**
- AR / AP → open invoices / open supplier_bills balances — **exists** (already computed for aging)
- Revenue YTD + YoY → sales-revenue postings by year — **compute**
- Trend badges → current vs prior period — **compute**
- Revenue vs COGS (6 mo) → monthly sums of revenue & COGS accounts — **compute (new endpoint)**
- Margin waterfall → Revenue, COGS, Opex (expense accts), Tax (tax accts) → GP/OP/NP — **compute**
- Recent journal entries → journals + journal_lines — **exists**

Customer / Supplier ledger
- Party list + balances → customers / suppliers + running balance — **compute**
- Statement rows → invoices (debit) · receipts=payments (credit) · credit notes (credit) for customers; bills (credit) · payments (debit) for suppliers — **exists for invoices/bills; new for receipts/credit-notes linkage**
- Opening balance, code (CUS/SUP-####), payment terms (Net N), standing — **new columns**

Profitability
- Revenue & COGS by segment → order/invoice lines → product → segment — **new linkage** (see §6 decision)
- Opex / Tax by segment → allocation rule — **compute (documented approximation)**

## 4. New / changed backend endpoints
- `GET /finance/overview` → KPI cards + trends + margin waterfall figures.
- `GET /finance/revenue-cogs?months=6` → monthly revenue/COGS series.
- `GET /finance/journal-entries/recent?limit=N` → recent JE feed (reuse journals repo).
- `GET /finance/customer-ledger` → customer list w/ receivable; `GET /finance/customer-ledger/{public_id}` → header + tiles + statement rows.
- `GET /finance/supplier-ledger` and `/finance/supplier-ledger/{public_id}` → payables equivalent.
- `GET /finance/profitability?period=YTD&segment=all` → KPI cards + per-segment rows + totals.
- `POST /finance/credit-notes`, `POST /finance/journals` (New entry), plus existing invoice/bill creates power the primary buttons.
- All read endpoints accept `?currency=PKR|USD|EUR|AED` and convert via `exchange_rates` (base = tenant base currency).

## 5. New schema / migrations
- **V0XX customers/suppliers ledger fields**: `code`, `payment_terms` (e.g. "Net 30"), `opening_balance`, `email` (suppliers) — for the header + opening-balance row.
- **V0XX credit_notes** table (customer credits: date, ref CN-####, customer_id, amount, reason) — feeds the credit entries.
- **payments**: ensure `party_type` (customer/supplier) + `party_id` + `reference` (RCPT/PAY) so one table feeds both ledgers. Add columns if missing.
- **Profitability segments** (see §6): either `segments` table + `segment_id` on products, or a computed mapping from categories.
- Seed files to backfill demo codes/terms/opening balances and a few credit notes so the ledgers render immediately.

## 6. Key decisions (confirm before build)

1. **Segment dimension for Profitability** — recommended: map "segment" to **product Category** (Knitwear, Bottoms, Shirts…), computing Revenue/COGS per segment from order/invoice lines joined to product→category; allocate Opex pro-rata by revenue and Tax = pre-tax × rate. Fully per-segment opex/tax would need a segment tag on expense postings (future). *Alternative:* a standalone `segments` table with seeded figures (simpler, less "real").
2. **Receipts & credit notes** — model customer receipts as `payments` rows (party_type=customer) and add a small `credit_notes` table; or fold both into a generic `ledger_entries` table. Recommended: reuse `payments` + add `credit_notes` (least new surface).
3. **Opening balances** — store an `opening_balance` column per party (simple, matches the design's "Opening" row) vs. computing from pre-period history. Recommended: stored column.
4. **Currency conversion** — backend converts using `exchange_rates` given a `?currency=` param (single source of truth) vs. frontend converting with a rates payload. Recommended: backend converts.

## 7. Frontend architecture
- **New screen kind `ledger`**: backend returns `{kind:"ledger", parties:[…]}`; a `LedgerView` client component renders the left search list, fetches `/…-ledger/{id}` on selection, and draws the header card + stat tiles + running-balance statement. Used by both Customer and Supplier ledger tabs.
- **`FinanceOverviewView`**: KPI cards + Revenue/COGS bar chart (reuse the dashboard chart lib) + margin-waterfall bars + recent-entries table.
- **`ProfitabilityView`**: KPI cards + segment table with GM%/NM% badges and totals row.
- **Currency toggle**: a small top-bar control writing to a React context; finance fetches include the selected currency; amounts formatted via a shared `formatMoney(amount, currency)` helper (Rs / $ / € / د.إ, M/K abbreviation).
- **Navigation**: replace the finance `tabs` with `overview`, `customerledger`, `supplierledger`, `profitability`. Remove old finance tabs from the rail.

## 8. Build phases
1. Navigation + 4 tab shells + currency toggle scaffold.
2. Overview (mostly from existing journals/invoices/bills/accounts + 1–2 new aggregation endpoints).
3. Ledger view + customer & supplier ledger endpoints + schema additions (codes, terms, opening balance, credit notes, payment party linkage) + seeds.
4. Profitability (segment model + compute + report) once §6.1 is chosen.
5. Multi-currency conversion across all finance endpoints.
6. Verification pass (numbers reconcile: ledger totals = tiles; waterfall ties to profitability; AR/AP match ledgers).

## 9. Out of scope / preserved
- The general ledger, journals, payments, and bills data all remain; only their standalone tab UIs are retired. If you still want a raw journals/CoA view, we can keep it behind the "View all" link on Overview.

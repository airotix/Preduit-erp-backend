"""Finance business logic → frontend ScreenConfig."""
import datetime
import uuid
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.modules.finance import repository as repo
from app.modules.finance.dto import (
    AccountCreate, AccountUpdate, BillCreate, BillUpdate, JournalEntryCreate,
    JournalEntryUpdate, PaymentCreate, PaymentUpdate,
)
from app.presenters.screen import initials, list_config, text_cell

_BILL_TONE = {"Open": "amber", "Scheduled": "navy", "Paid": "green"}


def _parse_date(s: str | None) -> datetime.date | None:
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None

_ACCT_TONE = {"Asset": "navy", "Liability": "amber", "Equity": "neutral", "Income": "green", "Expense": "red"}
_JE_TONE = {"Posted": "green", "Draft": "neutral", "Void": "red"}
_PMT_TONE = {"Cleared": "green", "Pending": "amber", "Failed": "red"}


# Numeric fields that are monetary (scaled on currency conversion). Percent
# fields (pct/gmPct/nmPct/netMarginPct/effectiveTaxPct/taxRatePct) are excluded.
_MONEY_KEYS = {
    "value", "revenue", "cogs", "gross", "opex", "tax", "net", "debit", "credit",
    "balance", "opening", "closing", "totalDebit", "totalCredit",
    "customerDue", "customerCredit", "supplierDue", "supplierAdvance", "bankBalance", "cashBalance",
    # statement fields (Phase C)
    "amount", "totalRevenue", "totalExpense", "netProfit", "grossProfit",
    "operatingProfit", "totalAssets", "totalLiabilities", "totalEquity",
    "totalEquityRE", "retained", "endingCash", "openingCash", "netChange",
    # controls fields (Phase D–H)
    "budget", "actual", "variance", "totalBudget", "totalActual", "totalVariance",
    "cost", "monthly", "accumulated", "nbv", "output", "input",
    "revenueBase", "costBase", "statementBalance",
}


def _scale_money(obj, f: float):
    if isinstance(obj, dict):
        return {
            k: (round(v * f, 2) if k in _MONEY_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool)
                else _scale_money(v, f))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scale_money(x, f) for x in obj]
    return obj


def convert(session: Session, payload: dict, *, currency: str) -> dict:
    """Convert all monetary values in a finance payload to `currency`."""
    factor = repo.fx_factor(session, target=currency)
    return _scale_money(payload, factor) if factor != 1.0 else payload


def _account_label(raw: str) -> str:
    """'1100 · Accounts receivable' → 'Accounts receivable'."""
    return raw.split("·")[-1].strip() if raw else raw


def _account_tone(name: str) -> str:
    n = (name or "").lower()
    if "revenue" in n or "sales" in n:
        return "green"
    if "receivable" in n:
        return "navy"
    if "payable" in n:
        return "amber"
    if "cash" in n or "bank" in n:
        return "neutral"
    if "cost of goods" in n or "cogs" in n:
        return "red"
    if "vat" in n or "tax" in n:
        return "accent"
    return "neutral"


def accounts_min(session: Session) -> list[dict]:
    return repo.list_accounts_min(session)


def create_full_journal(session: Session, *, tenant_id, payload):
    """Validate a balanced multi-line entry and post it to the GL."""
    from fastapi import HTTPException, status as _st
    lines = [{"account": l.account, "description": l.description,
              "debit": l.debit, "credit": l.credit} for l in payload.lines]
    total_debit = sum(float(l["debit"] or 0) for l in lines)
    total_credit = sum(float(l["credit"] or 0) for l in lines)
    if round(total_debit, 2) != round(total_credit, 2):
        raise HTTPException(_st.HTTP_400_BAD_REQUEST,
                            "Entry is not balanced — total debits must equal total credits.")
    if total_debit == 0:
        raise HTTPException(_st.HTTP_400_BAD_REQUEST, "Entry has no amounts.")
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    entry_on = _parse_date(payload.date)
    if repo.is_closed_date(session, entry_on):
        raise HTTPException(_st.HTTP_400_BAD_REQUEST,
                            "That date falls in a closed accounting period — reopen it to post.")
    display = entry_on.strftime("%d %b") if entry_on else (payload.date or "")
    return repo.create_full_journal(session, tenant_id=tid, reference=payload.reference,
                                    entry_on=entry_on, entry_display=display,
                                    memo=payload.memo, lines=lines)


def reverse_journal(session: Session, *, public_id: str):
    return repo.reverse_journal(session, public_id=public_id)


# ---------- Auto-posting: subledger documents → GL (Phase B) ----------

def _label(acct) -> str:
    return f"{acct.code} · {acct.name}"


def _post_lines(session, tid, *, entry_on, memo, lines: list[dict], doc):
    je = repo.create_full_journal(session, tenant_id=tid, reference=None,
                                  entry_on=entry_on, entry_display=(entry_on.strftime("%d %b") if entry_on else ""),
                                  memo=memo, lines=lines)
    doc.gl_journal_id = je.id
    je.source_note = f"{doc.__tablename__}:{doc.public_id}"
    doc.posted = True
    return je


def _ledger_clearing_account(session, tid):
    """Keep unclassified movements out of revenue, expenses and supplier debt."""
    account = repo.find_account(session, name_like="Ledger clearing")
    if account:
        return account
    code, suffix = "1099", 0
    while repo.find_account_by_code(session, code=code):
        suffix += 1
        code = f"1099-{suffix}"
    return repo.get_or_create_account(session, tenant_id=tid, code=code,
        name="Ledger clearing", acct_type="Asset", normal_side="D")


def _ensure_initial_chart(session, tid):
    """Bootstrap an empty workspace without changing a configured chart."""
    from app.models.finance import Account
    existing = select(Account.id).where(Account.tenant_id == tid,
        Account.is_deleted.is_(False)).limit(1)
    if session.scalar(existing) is not None:
        return
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": f"initial-chart:{tid}"})
    if session.scalar(existing) is not None:
        return
    currency = repo.base_currency(session) or "EUR"
    used_codes = set(session.scalars(select(Account.code).where(Account.tenant_id == tid)).all())
    for code, name, kind, side in [
        ("1000", "Cash", "Asset", "D"), ("1100", "Bank", "Asset", "D"),
        ("1200", "Accounts receivable", "Asset", "D"), ("1300", "Inventory", "Asset", "D"),
        ("2000", "Accounts payable", "Liability", "C"), ("4000", "Revenue", "Income", "C"),
        ("5000", "Cost of goods sold", "Expense", "D"),
    ]:
        suffix, available = 0, code
        while available in used_codes:
            suffix += 1
            available = f"{code}-{suffix}"
        session.add(Account(tenant_id=tid, code=available, name=name, acct_type=kind,
            normal_side=side, currency_code=currency, opening_balance=0, balance=0))
        used_codes.add(available)
    session.flush()


def _sync_manual_ledger_entries(session, tid):
    """Post manual movements once and reverse/repost when their channel changes.

    Bank/cash display credits are inflows; the corresponding asset GL line is
    a debit. Party ledger columns retain their receivable/payable meaning.
    Existing journal source notes provide the link without rewriting old rows.
    """
    _ensure_initial_chart(session, tid)
    from app.models.finance import LedgerEntry, JournalEntry, JournalLine
    entries = session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == tid).with_for_update()).all()
    count = 0
    for entry in entries:
        source = f"ledger-entry:{entry.public_id}"
        originals = session.scalars(select(JournalEntry).where(
            JournalEntry.tenant_id == tid, JournalEntry.source_note == source,
            JournalEntry.is_deleted.is_(False), JournalEntry.status == "Posted")).all()
        reversed_ids = set(session.scalars(select(JournalEntry.reversed_of_id).where(
            JournalEntry.tenant_id == tid, JournalEntry.is_deleted.is_(False),
            JournalEntry.status == "Posted", JournalEntry.reversed_of_id.isnot(None))).all())
        active = [journal for journal in originals if journal.id not in reversed_ids]
        debit, credit = Decimal(entry.debit or 0), Decimal(entry.credit or 0)
        lines = []
        if not entry.is_deleted and debit != credit:
            party_account = (repo.find_account(session, name_like="%receivable%") if entry.customer_id
                             else repo.find_account(session, name_like="%payable%") if entry.supplier_id else None)
            money_account = repo.find_account(session, acct_type="Asset",
                name_like=f"%{entry.payment_type}%") if entry.payment_type in ("bank", "cash") else None
            if entry.payment_type and money_account is None:
                from fastapi import HTTPException
                raise HTTPException(409, f"Create an asset account for {entry.payment_type} before recording a movement.")
            if (entry.customer_id or entry.supplier_id) and party_account is None:
                from fastapi import HTTPException
                raise HTTPException(409, "Create the receivable/payable account before recording a party entry.")
            if party_account:
                first, second = party_account, money_account or _ledger_clearing_account(session, tid)
                net = debit - credit
            elif money_account:
                first, second = money_account, _ledger_clearing_account(session, tid)
                net = credit - debit
            else:
                first = second = None
                net = Decimal(0)
            if first:
                lines = [dict(account=_label(first), debit=max(net, 0), credit=max(-net, 0)),
                         dict(account=_label(second), debit=max(-net, 0), credit=max(net, 0))]
        wanted = sorted((line["account"], Decimal(line["debit"]), Decimal(line["credit"])) for line in lines)
        if len(active) == 1:
            current = session.scalars(select(JournalLine).where(JournalLine.entry_id == active[0].id)).all()
            if sorted((line.account, line.debit, line.credit) for line in current) == wanted:
                continue
        for journal in active:
            repo.reverse_journal(session, public_id=journal.public_id)
        if lines:
            journal = repo.create_full_journal(session, tenant_id=tid, reference=None,
                entry_on=entry.entry_date, entry_display=_fmt_date(entry.entry_date),
                memo=(entry.description or "Manual ledger entry")[:300], lines=lines)
            journal.source_note = source
            session.flush()
            count += 1
    return count


def post_unposted(session: Session, *, tenant_id) -> int:
    """Post every not-yet-posted invoice, bill, payment and credit note into the
    GL as a balanced journal. Idempotent (skips already-posted docs)."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    _ensure_initial_chart(session, tid)
    # Repair older Pending/Failed payments that were posted before clearing.
    from app.models.finance import Payment, JournalEntry
    for stale in session.scalars(select(Payment).where(Payment.tenant_id == tid,
        Payment.status != "Cleared", Payment.posted.is_(True))).all():
        journal = session.get(JournalEntry, stale.gl_journal_id) if stale.gl_journal_id else None
        if journal:
            repo.reverse_journal(session, public_id=journal.public_id)
        stale.posted, stale.gl_journal_id = False, None
    session.flush()
    ar = repo.find_account(session, name_like="%receivable%")
    ap = repo.find_account(session, name_like="%payable%")
    revenue = repo.find_account(session, acct_type="Income") or repo.find_account(session, name_like="%revenue%")
    dr_bill = (repo.find_account(session, name_like="%inventory%")
               or repo.find_account(session, name_like="%cost of goods%")
               or repo.find_account(session, acct_type="Expense"))
    count = 0

    if ar and revenue:
        for inv in repo.unposted_invoices(session):
            amt = float(inv.amount or 0)
            if amt <= 0:
                continue
            _post_lines(session, tid, entry_on=inv.issued_date,
                        memo=f"Invoice {inv.invoice_no or ''}".strip(),
                        lines=[{"account": _label(ar), "debit": amt, "credit": 0},
                               {"account": _label(revenue), "debit": 0, "credit": amt}], doc=inv)
            count += 1

    if ap and dr_bill:
        for b in repo.unposted_bills(session):
            amt = float(b.amount or 0)
            if amt <= 0:
                continue
            _post_lines(session, tid, entry_on=b.issued_date or datetime.date.today(),
                        memo=f"Bill {b.bill_no or ''}".strip(),
                        lines=[{"account": _label(dr_bill), "debit": amt, "credit": 0},
                               {"account": _label(ap), "debit": 0, "credit": amt}], doc=b)
            count += 1

    for p in repo.unposted_payments(session):
        amt = float(p.amount or 0)
        if amt <= 0:
            continue
        eon = repo._parse_dm(p.pay_date)
        channel = p.payment_type or ("cash" if (p.method or "").strip().lower() == "cash" else "bank")
        payment_account = repo.find_account(session, acct_type="Asset", name_like=f"%{channel}%")
        if payment_account is None:
            from fastapi import HTTPException
            raise HTTPException(409, "Create the selected Cash or Bank asset account before recording a payment.")
        counterpart = (ar if p.pay_type == "Receipt" and p.party_type == "customer"
                       else ap if p.pay_type != "Receipt" and p.party_type == "supplier" else None)
        counterpart = counterpart or _ledger_clearing_account(session, tid)
        if p.pay_type == "Receipt":
            lines = [{"account": _label(payment_account), "debit": amt, "credit": 0},
                     {"account": _label(counterpart), "debit": 0, "credit": amt}]
        else:
            lines = [{"account": _label(counterpart), "debit": amt, "credit": 0},
                     {"account": _label(payment_account), "debit": 0, "credit": amt}]
        _post_lines(session, tid, entry_on=eon,
                    memo=f"Payment {p.payment_no or ''}".strip(), lines=lines, doc=p)
        count += 1

    if ar and revenue:
        for cn in repo.unposted_credit_notes(session):
            amt = float(cn.amount or 0)
            if amt <= 0:
                continue
            _post_lines(session, tid, entry_on=cn.cn_date,
                        memo=f"Credit note {cn.cn_no or ''}".strip(),
                        lines=[{"account": _label(revenue), "debit": amt, "credit": 0},
                               {"account": _label(ar), "debit": 0, "credit": amt}], doc=cn)
            count += 1

    count += _sync_manual_ledger_entries(session, tid)
    session.flush()
    return count


# ---------- Financial statements (Phase C) ----------

def statements(session: Session, *, tenant_id) -> dict:
    """Trial Balance, P&L, Balance Sheet and Cash Flow, all derived from posted
    GL lines. Auto-syncs subledgers first so the statements are current."""
    post_unposted(session, tenant_id=tenant_id)
    tb = repo.trial_balance(session)
    for r in tb:
        r["amount"] = r["net"] if r["normal_side"] == "D" else -r["net"]

    def rows(kind):
        return [{"code": r["code"], "name": r["name"], "amount": round(r["amount"], 2)}
                for r in tb if r["acct_type"] == kind]

    income, expense = rows("Income"), rows("Expense")
    assets, liabilities, equity = rows("Asset"), rows("Liability"), rows("Equity")

    total_rev = sum(r["amount"] for r in income)
    total_exp = sum(r["amount"] for r in expense)
    cogs = sum(r["amount"] for r in expense if "cost of goods" in r["name"].lower())
    opex = total_exp - cogs
    gross = total_rev - cogs
    net_profit = total_rev - total_exp

    total_assets = sum(r["amount"] for r in assets)
    total_liab = sum(r["amount"] for r in liabilities)
    total_equity = sum(r["amount"] for r in equity)
    total_eq_re = total_equity + net_profit

    cash_amt = sum(r["amount"] for r in assets
                   if "cash" in r["name"].lower() or "bank" in r["name"].lower())

    tb_debit = sum(r["debit"] for r in tb)
    tb_credit = sum(r["credit"] for r in tb)

    return {
        "trialBalance": {
            "rows": [{"code": r["code"], "name": r["name"],
                      "debit": round(r["debit"], 2), "credit": round(r["credit"], 2)} for r in tb],
            "totalDebit": round(tb_debit, 2), "totalCredit": round(tb_credit, 2),
            "balanced": round(tb_debit - tb_credit, 2) == 0,
        },
        "pnl": {
            "revenue": income, "totalRevenue": total_rev,
            "cogs": cogs, "grossProfit": gross,
            "expenses": [r for r in expense if "cost of goods" not in r["name"].lower()],
            "opex": opex, "totalExpense": total_exp, "netProfit": net_profit,
            "netMarginPct": round((net_profit / total_rev * 100), 1) if total_rev else 0.0,
        },
        "balanceSheet": {
            "assets": assets, "totalAssets": total_assets,
            "liabilities": liabilities, "totalLiabilities": total_liab,
            "equity": equity, "retained": net_profit, "totalEquityRE": total_eq_re,
            "balanced": round(total_assets - (total_liab + total_eq_re), 2) == 0,
        },
        "cashFlow": {"netProfit": net_profit, "endingCash": cash_amt},
    }


def overview_screen(session: Session, *, tenant_id=None) -> dict:
    """Finance Overview payload: KPIs, revenue/COGS series, margin waterfall, recent JEs.
    Auto-posts unposted subledger docs first (like statements) so the numbers are
    current and consistent with the Reports screen."""
    if tenant_id is not None:
        post_unposted(session, tenant_id=tenant_id)
    m = repo.overview_metrics(session)
    revenue, cogs, opex, tax = m["revenue"], m["cogs"], m["opex"], m["tax"]
    gross = revenue - cogs
    operating = gross - opex
    net = operating - tax
    pct = lambda v: round((v / revenue) * 100, 1) if revenue else 0.0

    ratio = (cogs / revenue) if revenue else 0.0
    series = repo.revenue_by_month(session, months=6)
    for row in series:
        row["cogs"] = round(row["revenue"] * ratio, 2)

    recent = [
        {"date": r["date"] or "—", "entry": r["entry"],
         "account": _account_label(r["account"]),
         "accountTone": _account_tone(r["account"]),
         "memo": r["memo"] or "—",
         "debit": r["debit"] or None, "credit": r["credit"] or None}
        for r in repo.recent_journal_lines(session, limit=6, tenant_id=tenant_id)
    ]

    def s(n: int) -> str:
        return "" if n == 1 else "s"

    return {
        "ledgerSummary": {
            "customerDue": sum(max(p["balance"], 0) for p in repo.customer_parties(session)),
            "customerCredit": sum(max(-p["balance"], 0) for p in repo.customer_parties(session)),
            "supplierDue": sum(max(-p["balance"], 0) for p in repo.supplier_parties(session)),
            "supplierAdvance": sum(max(p["balance"], 0) for p in repo.supplier_parties(session)),
            "bankBalance": cash_bank_ledger(session, kind="bank")["closing"],
            "cashBalance": cash_bank_ledger(session, kind="cash")["closing"],
        },
        "kpis": {
            "cashBank": {"value": m["cash_total"], "sub": f"Across {m['cash_count']} account{s(m['cash_count'])}"},
            "ar": {"value": m["ar_total"], "sub": f"{m['ar_count']} open invoice{s(m['ar_count'])}"},
            "ap": {"value": m["ap_total"], "sub": f"{m['ap_count']} open bill{s(m['ap_count'])}"},
            "revenue": {"value": revenue, "sub": "Year to date"},
        },
        "revCogs": series,
        "waterfall": [
            {"label": "Revenue", "value": revenue, "pct": 100, "tone": "orange"},
            {"label": "Gross profit", "value": gross, "pct": pct(gross), "tone": "purple"},
            {"label": "Operating profit", "value": operating, "pct": pct(operating), "tone": "green"},
            {"label": "Net profit", "value": net, "pct": pct(net), "tone": "green"},
        ],
        "netMarginPct": pct(net),
        "effectiveTaxPct": round((tax / operating) * 100, 1) if operating else 0.0,
        "recent": recent,
    }


# ---------- Ledgers ----------

def _fmt_date(d) -> str:
    return d.strftime("%d %b") if hasattr(d, "strftime") else "—"


def customer_ledger_screen(session: Session) -> dict:
    return {
        "variant": "customer",
        "parties": [
            {"public_id": p["public_id"], "name": p["name"], "code": p["code"] or "—",
             "initials": initials(p["name"]), "balance": p["balance"],
             "balanceTone": "orange" if p["balance"] > 0 else "green"}
            for p in repo.customer_parties(session)
        ],
    }


def supplier_ledger_screen(session: Session) -> dict:
    return {
        "variant": "supplier",
        "parties": [
            {"public_id": p["public_id"], "name": p["name"], "code": p["code"] or "—",
             "initials": initials(p["name"]), "balance": p["balance"],
             "balanceTone": "accent" if p["balance"] < 0 else "green"}
            for p in repo.supplier_parties(session)
        ],
    }


def _gm_tone(gm: float) -> str:
    if gm >= 30:
        return "green"
    if gm >= 15:
        return "amber"
    return "red"


def profitability_screen(session: Session) -> dict:
    """Gross/net margin by business segment (= product category). Revenue is real
    (from sales order lines); COGS uses the ledger COGS ratio, opex is allocated
    pro-rata by revenue, and tax applies the standard rate."""
    m = repo.overview_metrics(session)
    ratio = (m["cogs"] / m["revenue"]) if m["revenue"] else 0.55
    opex_total = m["opex"]
    tax_rate = 0.17

    segs = repo.category_revenue(session)
    total_rev = sum(s["revenue"] for s in segs)

    seg_rows = []
    tot = {"revenue": 0.0, "cogs": 0.0, "gross": 0.0, "opex": 0.0, "tax": 0.0, "net": 0.0}
    for s in segs:
        rev = s["revenue"]
        cogs = rev * ratio
        gross = rev - cogs
        opex = opex_total * (rev / total_rev) if total_rev else 0.0
        pretax = gross - opex
        tax = max(pretax, 0.0) * tax_rate
        net = pretax - tax
        gm = (gross / rev * 100) if rev else 0.0
        nm = (net / rev * 100) if rev else 0.0
        for k, v in (("revenue", rev), ("cogs", cogs), ("gross", gross),
                     ("opex", opex), ("tax", tax), ("net", net)):
            tot[k] += v
        seg_rows.append({
            "segment": s["segment"], "revenue": rev, "cogs": cogs, "gross": gross,
            "gmPct": round(gm, 1), "gmTone": _gm_tone(gm), "opex": opex, "tax": tax,
            "net": net, "nmPct": round(nm, 1),
        })

    tot_gm = (tot["gross"] / tot["revenue"] * 100) if tot["revenue"] else 0.0
    tot_nm = (tot["net"] / tot["revenue"] * 100) if tot["revenue"] else 0.0

    return {
        "taxRatePct": round(tax_rate * 100, 1),
        "kpis": {
            "revenue": {"value": tot["revenue"], "sub": "All segments"},
            "gross": {"value": tot["gross"], "sub": f"{round(tot_gm, 1)}% gross margin"},
            "net": {"value": tot["net"], "sub": f"After {round(tax_rate * 100, 1)}% tax"},
            "margin": {"pct": round(tot_nm, 1), "sub": "Net profit margin"},
        },
        "rows": seg_rows,
        "totals": {
            "revenue": tot["revenue"], "cogs": tot["cogs"], "gross": tot["gross"],
            "gmPct": round(tot_gm, 1), "opex": tot["opex"], "tax": tot["tax"],
            "net": tot["net"], "nmPct": round(tot_nm, 1),
        },
    }


# ---------- Period close (Phase E) ----------

def periods_screen(session: Session) -> dict:
    return {"rows": [
        {"public_id": str(p.public_id), "name": p.name,
         "start": p.start_date.isoformat(), "end": p.end_date.isoformat(), "status": p.status}
        for p in repo.list_periods(session)
    ]}


def create_period(session: Session, *, tenant_id, payload):
    return repo.create_period(session, tenant_id=_uuid(tenant_id), name=payload.name,
                              start_date=_parse_date(payload.start_date),
                              end_date=_parse_date(payload.end_date))


def set_period(session: Session, *, public_id, status):
    return repo.set_period_status(session, public_id=public_id, status=status)


# ---------- Budgets (Phase G) ----------

def budget_actual(session: Session, *, fiscal_year: int) -> dict:
    tb = {r["code"]: (r["net"] if r["normal_side"] == "D" else -r["net"])
          for r in repo.trial_balance(session)}
    rows, tot_b, tot_a = [], 0.0, 0.0
    for bl in repo.list_budget_lines(session, fiscal_year=fiscal_year):
        actual = float(tb.get(bl.account_code, 0.0))
        budget = float(bl.amount or 0)
        rows.append({"account": bl.account_name or bl.account_code, "code": bl.account_code,
                     "budget": budget, "actual": actual, "variance": actual - budget})
        tot_b += budget
        tot_a += actual
    return {"fiscalYear": fiscal_year, "rows": rows, "totalBudget": tot_b,
            "totalActual": tot_a, "totalVariance": tot_a - tot_b}


def create_budget_line(session: Session, *, tenant_id, payload):
    return repo.create_budget_line(session, tenant_id=_uuid(tenant_id),
                                   fiscal_year=payload.fiscal_year, account_code=payload.account_code,
                                   account_name=payload.account_name, amount=payload.amount)


# ---------- Fixed assets (Phase H) ----------

def fixed_assets_screen(session: Session) -> dict:
    rows = []
    for fa in repo.list_fixed_assets(session):
        monthly = float(fa.cost - fa.salvage) / fa.life_months if fa.life_months else 0.0
        rows.append({
            "public_id": str(fa.public_id), "asset_no": fa.asset_no, "name": fa.name,
            "category": fa.category or "—", "cost": float(fa.cost),
            "monthly": round(monthly, 2), "accumulated": float(fa.accumulated),
            "nbv": round(float(fa.cost) - float(fa.accumulated), 2), "status": fa.status,
        })
    return {"rows": rows}


def create_fixed_asset(session: Session, *, tenant_id, payload):
    return repo.create_fixed_asset(
        session, tenant_id=_uuid(tenant_id), name=payload.name, category=payload.category,
        cost=payload.cost, salvage=payload.salvage, life_months=payload.life_months,
        in_service_date=_parse_date(payload.in_service_date),
    )


def depreciate_asset(session: Session, *, tenant_id, public_id):
    fa = repo.get_fixed_asset(session, public_id=public_id)
    if fa is None:
        return None
    depreciable = float(fa.cost) - float(fa.salvage)
    monthly = depreciable / fa.life_months if fa.life_months else 0.0
    remaining = depreciable - float(fa.accumulated)
    dep = min(monthly, remaining)
    if dep <= 0:
        return fa
    tid = _uuid(tenant_id)
    exp = repo.get_or_create_account(session, tenant_id=tid, code="6000",
                                     name="Depreciation expense", acct_type="Expense", normal_side="D")
    acc = repo.get_or_create_account(session, tenant_id=tid, code="1900",
                                     name="Accumulated depreciation", acct_type="Asset", normal_side="C")
    fa.accumulated = float(fa.accumulated) + dep
    today = datetime.date.today()
    repo.create_full_journal(
        session, tenant_id=tid, reference=None, entry_on=today,
        entry_display=today.strftime("%d %b"), memo=f"Depreciation — {fa.name}",
        lines=[{"account": f"{exp.code} · {exp.name}", "debit": dep, "credit": 0},
               {"account": f"{acc.code} · {acc.name}", "debit": 0, "credit": dep}],
    )
    session.flush()
    return fa


# ---------- VAT return (Phase F, computed at standard rate) ----------

def vat_return(session: Session, *, tenant_id, rate: float = 0.17) -> dict:
    st = statements(session, tenant_id=tenant_id)
    rev = st["pnl"]["totalRevenue"]
    costs = st["pnl"]["cogs"] + st["pnl"]["opex"]
    output = rev * rate
    inp = costs * rate
    return {"ratePct": round(rate * 100, 1), "revenueBase": rev, "costBase": costs,
            "output": output, "input": inp, "net": output - inp}


def _uuid(t):
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


# ---------- Bank reconciliation (Phase D) ----------

def bank_screen(session: Session) -> dict:
    return {"accounts": [
        {"public_id": str(a.public_id), "name": a.name,
         "account_no": a.account_no or "—", "currency": a.currency_code}
        for a in repo.list_bank_accounts(session)
    ]}


def bank_account_detail(session: Session, *, public_id: str) -> dict | None:
    a = repo.get_bank_account(session, public_id=public_id)
    if a is None:
        return None
    txns = repo.list_bank_transactions(session, bank_account_id=a.id)
    matched = reconciled = unmatched = 0
    gl_account = repo.find_account_by_code(session, code=a.gl_code) if a.gl_code else None
    opening = Decimal(gl_account.opening_balance or gl_account.balance or 0) if gl_account else Decimal(0)
    stmt_bal = opening
    total_debit = total_credit = Decimal(0)
    rows = []
    for t in txns:
        amount = Decimal(t.amount or 0)
        debit, credit = max(-amount, 0), max(amount, 0)
        stmt_bal += amount
        total_debit += debit
        total_credit += credit
        if t.status == "Matched":
            matched += 1
        elif t.status == "Reconciled":
            reconciled += 1
        else:
            unmatched += 1
        rows.append({
            "public_id": str(t.public_id),
            "date": t.txn_date.strftime("%d %b") if t.txn_date else "—",
            "description": t.description or "—", "amount": float(t.amount or 0),
            "debit": float(debit), "credit": float(credit), "balance": float(round(stmt_bal, 2)),
            "status": t.status, "matchedRef": repo.payment_no_by_id(session, t.matched_payment_id),
        })
    return {"name": a.name, "account_no": a.account_no or "—", "currency": a.currency_code,
            "opening": float(opening), "totalDebit": float(total_debit), "totalCredit": float(total_credit),
            "statementBalance": float(round(stmt_bal, 2)), "matched": matched, "reconciled": reconciled,
            "unmatched": unmatched, "rows": rows}


def create_bank_account(session: Session, *, tenant_id, payload):
    return repo.create_bank_account(session, tenant_id=_uuid(tenant_id), name=payload.name,
                                    account_no=payload.account_no, gl_code=payload.gl_code,
                                    currency_code=payload.currency)


def add_bank_transaction(session: Session, *, tenant_id, public_id, payload):
    a = repo.get_bank_account(session, public_id=public_id)
    if a is None:
        return None
    return repo.create_bank_transaction(
        session, tenant_id=_uuid(tenant_id), bank_account_id=a.id,
        txn_date=_parse_date(payload.txn_date), description=payload.description,
        amount=payload.amount)


def auto_match_bank(session: Session, *, public_id):
    a = repo.get_bank_account(session, public_id=public_id)
    if a is None:
        return None
    return repo.auto_match(session, bank_account_id=a.id)


def reconcile_bank(session: Session, *, public_id):
    a = repo.get_bank_account(session, public_id=public_id)
    if a is None:
        return None
    return repo.reconcile_matched(session, bank_account_id=a.id)


def _build_customer_statement(data: dict) -> dict:
    """Running-balance customer statement. Invoices show debits and every
    payment shows a separate credit; manual entries and credit notes also have
    their own lines. Every
    row carries an editType/editId so its description is inline-editable."""
    party, opening = data["party"], data["opening"]
    items = [{**r, "kind": "invoice"} for r in data["invoices"]]
    items += [{**e, "kind": "extra"} for e in data["extras"]]
    items.sort(key=lambda x: x.get("date") or datetime.date.max)

    rows = []
    running = opening
    total_debit = total_credit = 0.0
    for it in items:
        debit = float(it.get("debit") or 0)
        credit = float(it.get("credit") or 0)
        running += debit - credit
        total_debit += debit
        total_credit += credit
        open_inv = (it["kind"] == "invoice" and not it.get("paid")
                    and float(it.get("remaining") or 0) > 0)
        rows.append({
            "date": _fmt_date(it.get("date")), "ref": it["ref"], "desc": it["desc"],
            "debit": debit or None, "credit": credit or None, "balance": round(running, 2),
            "editType": it.get("editType"), "editId": it.get("editId"),
            "paymentType": it.get("paymentType"),
            # Record-payment handle (unpaid invoices) targets the remaining balance.
            "invoicePublicId": it["public_id"] if open_inv else None,
            "baseAmount": round(float(it.get("remaining") or 0), 2) if open_inv else None,
        })
    closing = round(running, 2)
    if data.get("overdue"):
        standing_label, standing_tone = "Payment due", "amber"
    else:
        standing_label, standing_tone = "In good standing", "green"
    return {
        "name": party.name, "code": party.code or "—", "terms": party.terms or "—",
        "email": getattr(party, "email", "") or "", "initials": initials(party.name),
        "standingLabel": standing_label, "standingTone": standing_tone,
        "opening": opening, "totalDebit": total_debit, "totalCredit": total_credit,
        "closing": closing, "balanceLabel": "Receivable" if closing > 0 else "Customer credit" if closing < 0 else "Settled", "balanceTone": "orange",
        "rows": rows,
    }


def customer_statement(session: Session, *, public_id: str) -> dict | None:
    data = repo.customer_statement(session, public_id=public_id)
    return _build_customer_statement(data) if data else None


def create_ledger_entry(session: Session, *, tenant_id, customer_public_id: str,
                        description: str, debit, credit, payment_type: str | None = None):
    from fastapi import HTTPException
    _validate_manual_amounts(debit, credit)
    c = repo.customer_by_public(session, public_id=customer_public_id)
    if c is None or c.tenant_id != _uuid(tenant_id):
        raise HTTPException(404, "Customer not found")
    entry = repo.create_ledger_entry(session, tenant_id=_uuid(tenant_id),
                                    customer_id=c.id,
                                    description=description, debit=debit, credit=credit,
                                    payment_type=payment_type)
    _sync_manual_ledger_entries(session, _uuid(tenant_id))
    return entry


def _validate_manual_amounts(debit, credit):
    from fastapi import HTTPException
    debit, credit = Decimal(str(debit or 0)), Decimal(str(credit or 0))
    if (not debit.is_finite() or not credit.is_finite() or debit < 0 or credit < 0
            or (debit > 0) == (credit > 0)):
        raise HTTPException(422, "Enter a positive amount in either Debit or Credit, not both.")


def create_cash_bank_entry(session: Session, *, tenant_id, kind: str,
                           description: str, debit, credit):
    """A manual line straight onto the Cash or Bank ledger — no customer/supplier
    party — tagged with the ledger's payment type so it shows in that ledger."""
    _validate_manual_amounts(debit, credit)
    entry = repo.create_ledger_entry(session, tenant_id=_uuid(tenant_id),
                                    customer_id=None, supplier_id=None,
                                    description=description, debit=debit, credit=credit,
                                    payment_type=kind)
    _sync_manual_ledger_entries(session, _uuid(tenant_id))
    return entry


def update_ledger_description(session: Session, *, edit_type: str, public_id: str,
                              description: str) -> bool:
    return repo.update_ledger_description(session, edit_type=edit_type,
                                          public_id=public_id, description=description)


def update_ledger_payment_type(session: Session, *, edit_type: str, public_id: str,
                               payment_type: str | None) -> bool:
    if edit_type == "payment" and payment_type is None:
        from fastapi import HTTPException
        raise HTTPException(422, "Select Cash or Bank for a recorded payment.")
    if edit_type == "manual" and payment_type is None:
        from app.models.finance import LedgerEntry
        from fastapi import HTTPException
        entry = session.scalar(select(LedgerEntry).where(LedgerEntry.public_id == UUID(str(public_id))))
        if entry and not entry.customer_id and not entry.supplier_id:
            raise HTTPException(422, "Select Cash or Bank for a money movement.")
    if edit_type == "payment":
        from app.models.finance import Payment
        payment = session.scalar(select(Payment).where(Payment.public_id == UUID(str(public_id)),
            Payment.is_deleted.is_(False)))
        if payment is None:
            return False
        update_payment(session, public_id=public_id, payload=PaymentUpdate(
            party=payment.party, amount=payment.amount, type=payment.pay_type,
            method="Cash" if payment_type == "cash" else "Bank",
            allocatedTo=payment.allocated_to, date=payment.pay_date or "",
            status=payment.status, reference=payment.reference, notes=payment.notes))
        return True
    updated = repo.update_ledger_payment_type(session, edit_type=edit_type,
                                             public_id=public_id, payment_type=payment_type)
    if updated and edit_type == "manual":
        from app.models.finance import LedgerEntry
        entry = session.scalar(select(LedgerEntry).where(LedgerEntry.public_id == UUID(str(public_id))))
        _sync_manual_ledger_entries(session, entry.tenant_id)
    return updated


def _build_cash_bank_ledger(rows: list[dict], *, kind: str, opening: float = 0) -> dict:
    """Running-balance ledger consolidated across every customer and supplier —
    same shape/behaviour as the customer/supplier ledger tables (inline-editable
    description and payment type), just filtered to one payment type."""
    out_rows = []
    running = Decimal(str(opening))
    total_debit = total_credit = Decimal(0)
    for r in rows:
        debit = Decimal(str(r.get("debit") or 0))
        credit = Decimal(str(r.get("credit") or 0))
        running += credit - debit
        total_debit += debit
        total_credit += credit
        out_rows.append({
            "date": _fmt_date(r.get("date")), "ref": r["ref"],
            "party": r.get("party") or "—", "partyType": r.get("partyType"),
            "source": r.get("source"), "desc": r["desc"],
            "debit": float(debit) if debit else None, "credit": float(credit) if credit else None, "balance": float(round(running, 2)),
            "editType": r.get("editType"), "editId": r.get("editId"),
            "paymentType": kind,
        })
    return {
        "kind": kind, "label": "Cash ledger" if kind == "cash" else "Bank ledger",
        "opening": opening, "totalDebit": float(round(total_debit, 2)), "totalCredit": float(round(total_credit, 2)),
        "closing": float(round(running, 2)), "rows": out_rows,
    }


def cash_bank_ledger(session: Session, *, kind: str) -> dict:
    rows = repo.cash_bank_ledger(session, payment_type=kind)
    from app.models.finance import Account
    accounts = session.scalars(select(Account).where(Account.is_deleted.is_(False),
        Account.acct_type == "Asset", Account.name.ilike(f"%{kind}%"))).all()
    opening = sum((Decimal(account.opening_balance or account.balance or 0) for account in accounts), Decimal(0))
    return _build_cash_bank_ledger(rows, kind=kind, opening=float(opening or 0))


def _build_supplier_statement(data: dict) -> dict:
    """Running-balance supplier statement (payable). Bill rows show the credit we
    owe; each disbursement is its own debit line (dated when recorded, referencing
    its bill); manual entries are their own lines. Descriptions inline-editable."""
    party, opening = data["party"], data["opening"]
    items = [{**r, "kind": "bill"} for r in data["bills"]]
    items += [{**e, "kind": "extra"} for e in data["extras"]]
    items.sort(key=lambda x: x.get("date") or datetime.date.max)

    rows = []
    running = opening
    total_debit = total_credit = 0.0
    for it in items:
        debit = float(it.get("debit") or 0)
        credit = float(it.get("credit") or 0)
        running += debit - credit  # invoice = credit, supplier payment = debit
        total_debit += debit
        total_credit += credit
        open_bill = (it["kind"] == "bill" and not it.get("paid")
                     and float(it.get("remaining") or 0) > 0)
        rows.append({
            "date": _fmt_date(it.get("date")), "ref": it["ref"], "desc": it["desc"],
            "debit": debit or None, "credit": credit or None, "balance": round(running, 2),
            "editType": it.get("editType"), "editId": it.get("editId"),
            "paymentType": it.get("paymentType"),
            # Record payment handle (unpaid bills) → disbursement for the remaining.
            "invoicePublicId": it["public_id"] if open_bill else None,
            "baseAmount": round(float(it.get("remaining") or 0), 2) if open_bill else None,
        })
    closing = round(running, 2)
    standing_label, standing_tone = (("Payment due", "accent") if closing < 0
                                     else ("Current", "green"))
    return {
        "name": party.name, "code": party.code or "—", "terms": party.terms or "—",
        "email": getattr(party, "email", "") or "", "initials": initials(party.name),
        "standingLabel": standing_label, "standingTone": standing_tone,
        "opening": opening, "totalDebit": total_debit, "totalCredit": total_credit,
        "closing": closing, "balanceLabel": "Supplier advance" if closing > 0 else "Payable" if closing < 0 else "Settled", "balanceTone": "accent",
        "rows": rows,
    }


def supplier_statement(session: Session, *, public_id: str) -> dict | None:
    data = repo.supplier_statement(session, public_id=public_id)
    return _build_supplier_statement(data) if data else None


def create_supplier_ledger_entry(session: Session, *, tenant_id, supplier_public_id: str,
                                 description: str, debit, credit, payment_type: str | None = None):
    from fastapi import HTTPException
    _validate_manual_amounts(debit, credit)
    s = repo.supplier_by_public(session, public_id=supplier_public_id)
    if s is None or s.tenant_id != _uuid(tenant_id):
        raise HTTPException(404, "Supplier not found")
    entry = repo.create_ledger_entry(session, tenant_id=_uuid(tenant_id), customer_id=None,
                                    supplier_id=s.id,
                                    description=description, debit=debit, credit=credit,
                                    payment_type=payment_type)
    _sync_manual_ledger_entries(session, _uuid(tenant_id))
    return entry


def record_bill_payment(session: Session, *, tenant_id, public_id: str, amount_paid, paid: bool):
    """Settle (all or part of) a payable: record a supplier disbursement so it
    shows as a Debit on the supplier ledger and reduces the payable. Marks the
    bill Paid when fully settled. Posts to the GL."""
    tid = _uuid(tenant_id)
    bill = repo.bill_by_public(session, public_id=public_id)
    if bill is None:
        return None
    bill_amt = float(bill.amount or 0)
    already = repo.disbursements_applied_to_bill(
        session, bill_no=bill.bill_no, supplier_id=bill.supplier_id,
        supplier_name=bill.supplier_name)
    remaining = max(bill_amt - already, 0.0)
    amt = float(amount_paid or 0)
    if paid and amt <= 0:
        amt = remaining
    amt = min(amt, remaining) if remaining > 0 else 0.0
    if amt > 0:
        today = datetime.date.today()
        repo.create_payment(
            session, tenant_id=tid, party=bill.supplier_name, amount=amt,
            pay_type="Disbursement", method="Cash" if bill.payment_type == "cash" else "Bank", reference=bill.bill_no,
            allocated_to=bill.bill_no, pay_date=today.isoformat(),
            status="Cleared", notes=f"Payment for {bill.po_ref or bill.bill_no or 'bill'}")
        if already + amt >= bill_amt and bill_amt > 0:
            bill.status = "Paid"
        session.flush()
        post_unposted(session, tenant_id=tid)
    return bill


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def _eur(v) -> str:
    return f"€{v:,.0f}" if v is not None else "—"


def _eur_compact(v) -> str:
    n = float(v or 0)
    if abs(n) >= 1_000_000:
        return f"€{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"€{n / 1_000:.0f}K"
    return f"€{n:,.0f}"


# ---------- Chart of accounts ----------

def _account_record(r: dict) -> dict:
    """Raw editable values keyed to the frontend form field names."""
    return {
        "code": r["code"], "name": r["name"], "type": r["acct_type"],
        "subtype": r["subtype"], "currency": r["currency_code"],
        "openingBalance": float(r["opening_balance"]) if r["opening_balance"] is not None else None,
        "taxRate": float(r["tax_rate"]) if r["tax_rate"] is not None else None,
        "parent": r["parent_code"], "description": r["description"],
        "active": bool(r["is_active"]),
    }


def coa_screen(session: Session, *, limit: int = 100, offset: int = 0) -> dict:
    rows, total = repo.list_accounts(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["code"], mono=True, strong=True),
            r["name"],
            text_cell(r["acct_type"], badge=_ACCT_TONE.get(r["acct_type"], "neutral")),
            text_cell(_eur_compact(r["balance"]), align="right", mono=True, strong=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Code"}, {"label": "Account"}, {"label": "Type"},
                 {"label": "Balance", "align": "right"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[_account_record(r) for r in rows],
        search="Search accounts…", action="New account", filters=["Type"],
    )


def _account_fields(p) -> dict:
    return {"code": p.code, "name": p.name, "acct_type": p.type, "subtype": p.subtype,
            "currency": p.currency, "opening_balance": p.openingBalance, "tax_rate": p.taxRate,
            "parent": p.parent, "description": p.description, "active": p.active}


def create_account(session, *, tenant_id, payload: AccountCreate):
    return repo.create_account(session, tenant_id=_tid(tenant_id), **_account_fields(payload))


def update_account(session, *, public_id: str, payload: AccountUpdate):
    return repo.update_account(session, public_id=public_id, **_account_fields(payload))


# ---------- Journal entries ----------

def journals_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_journals(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["entry_no"], mono=True, strong=True),
            r["entry_date"] or "—",
            r["memo"] or "—",
            text_cell(_eur(r["total_debit"]), align="right", mono=True),
            text_cell(_eur(r["total_credit"]), align="right", mono=True),
            text_cell(r["status"], badge=_JE_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Reference"}, {"label": "Date"}, {"label": "Memo"},
                 {"label": "Debit", "align": "right"}, {"label": "Credit", "align": "right"},
                 {"label": "Status"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{
            "reference": r["entry_no"], "memo": r["memo"],
            "debit": float(r["total_debit"]) if r["total_debit"] is not None else None,
            "credit": float(r["total_credit"]) if r["total_credit"] is not None else None,
            "status": r["status"], "date": r["entry_date"],
        } for r in rows],
        search="Search journal entries…", action="New entry", filters=["Status"],
    )


def _journal_fields(p) -> dict:
    return {"reference": p.reference, "memo": p.memo, "debit": p.debit,
            "credit": p.credit, "status": p.status, "date": p.date}


def create_journal(session, *, tenant_id, payload: JournalEntryCreate):
    return repo.create_journal(session, tenant_id=_tid(tenant_id), **_journal_fields(payload))


def update_journal(session, *, public_id: str, payload: JournalEntryUpdate):
    return repo.update_journal(session, public_id=public_id, **_journal_fields(payload))


def journal_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_journal_detail(session, public_id=public_id)
    if data is None:
        return None
    je, lines = data["entry"], data["lines"]
    ledger = [
        {"acct": l["account"], "desc": l["description"] or "",
         "debit": _eur(l["debit"]) if l["debit"] else "—",
         "credit": _eur(l["credit"]) if l["credit"] else "—"}
        for l in lines
    ]
    return {
        "variant": "journal",
        "ref": je.entry_no,
        "title": je.memo or je.entry_no,
        "statusLabel": je.status,
        "statusTone": _JE_TONE.get(je.status, "neutral"),
        "meta": [
            {"k": "Date", "v": je.entry_date or "—"},
            {"k": "Debit", "v": _eur(je.total_debit)},
            {"k": "Credit", "v": _eur(je.total_credit)},
            {"k": "Status", "v": je.status},
        ],
        "tabs": ["Lines", "Source", "Activity"],
        "journal": {
            "ledger": ledger,
            "ledgerDebit": _eur(je.total_debit),
            "ledgerCredit": _eur(je.total_credit),
            "sourceNote": je.source_note or "No source note recorded for this entry.",
        },
    }


# ---------- Payments ----------

def payments_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_payments(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        amt = f"−€{r['amount']:,.0f}" if r["pay_type"] == "Disbursement" else f"€{r['amount']:,.0f}"
        grid.append([
            text_cell(r["payment_no"] or "—", mono=True, strong=True),
            r["pay_date"] or "—",
            text_cell(r["party"], avatar=True, sub=r["pay_type"]),
            r["allocated_to"] or "—",
            text_cell(amt, align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_PMT_TONE.get(r["status"], "neutral")),
        ])
    return list_config(
        columns=[{"label": "Payment"}, {"label": "Date"}, {"label": "Party"},
                 {"label": "Allocated to"}, {"label": "Amount", "align": "right"},
                 {"label": "Status"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{
            "party": r["party"],
            "amount": float(r["amount"]) if r["amount"] is not None else None,
            "type": r["pay_type"], "method": r["method"], "reference": r["reference"],
            "allocatedTo": r["allocated_to"], "date": r["pay_date"],
            "status": r["status"], "notes": r["notes"],
        } for r in rows],
        search="Search payments…", action="Record payment", filters=["Type", "Status"],
    )


def _payment_fields(p) -> dict:
    return {"party": p.party, "amount": p.amount, "pay_type": p.type, "method": p.method,
            "reference": p.reference, "allocated_to": (p.allocatedTo or "").strip() or None, "pay_date": p.date,
            "status": p.status, "notes": p.notes}


def _allocation_document(session, *, tenant_id, pay_type, party, allocated_to):
    from app.models.sales import Invoice
    from app.models.finance import SupplierBill
    from fastapi import HTTPException
    if not (allocated_to or "").strip():
        return None
    model = Invoice if pay_type == "Receipt" else SupplierBill
    number = model.invoice_no if pay_type == "Receipt" else model.bill_no
    name = model.customer_name if pay_type == "Receipt" else model.supplier_name
    doc = session.scalar(select(model).where(model.tenant_id == _tid(tenant_id),
        number == allocated_to.strip(), name == party, model.is_deleted.is_(False)).with_for_update())
    if doc is None:
        raise HTTPException(409, "Allocation must reference an invoice or payable belonging to this party.")
    return doc


def _sync_payment_document(session, doc, pay_type):
    if doc is None:
        return
    if pay_type == "Receipt":
        applied = repo.receipts_applied_to_invoice(session, invoice_no=doc.invoice_no,
            customer_id=doc.customer_id, customer_name=doc.customer_name)
    else:
        applied = repo.disbursements_applied_to_bill(session, bill_no=doc.bill_no,
            supplier_id=doc.supplier_id, supplier_name=doc.supplier_name)
    doc.status = "Paid" if applied >= float(doc.amount) else "Open"
    session.flush()


def _validate_allocation(session, doc, payload, exclude_id=None):
    from app.models.finance import Payment
    from sqlalchemy import func
    from fastapi import HTTPException
    if doc is None or payload.status != "Cleared":
        return
    applied = session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.tenant_id == doc.tenant_id, Payment.party == payload.party, Payment.pay_type == payload.type,
        Payment.allocated_to == (doc.invoice_no if payload.type == "Receipt" else doc.bill_no),
        Payment.status == "Cleared", Payment.is_deleted.is_(False),
        Payment.id != exclude_id if exclude_id else Payment.id.isnot(None)))
    if payload.amount + applied > doc.amount:
        raise HTTPException(409, "Payment exceeds this document's outstanding amount. Record any excess as an unallocated advance.")


def create_payment(session, *, tenant_id, payload: PaymentCreate):
    doc = _allocation_document(session, tenant_id=tenant_id, pay_type=payload.type,
        party=payload.party, allocated_to=payload.allocatedTo)
    _validate_allocation(session, doc, payload)
    p = repo.create_payment(session, tenant_id=_tid(tenant_id), **_payment_fields(payload))
    _sync_payment_document(session, doc, p.pay_type)
    post_unposted(session, tenant_id=tenant_id)  # post to the GL at transaction time
    return p


def update_payment(session, *, public_id: str, payload: PaymentUpdate):
    from app.models.finance import Payment, JournalEntry, BankTransaction
    p = session.scalar(select(Payment).where(Payment.public_id == UUID(str(public_id)),
        Payment.is_deleted.is_(False)).with_for_update())
    if p is None:
        return None
    old_type = p.pay_type
    old_doc = _allocation_document(session, tenant_id=p.tenant_id, pay_type=p.pay_type,
        party=p.party, allocated_to=p.allocated_to)
    doc = _allocation_document(session, tenant_id=p.tenant_id, pay_type=payload.type,
        party=payload.party, allocated_to=payload.allocatedTo)
    _validate_allocation(session, doc, payload, exclude_id=p.id)
    changed = any([p.amount != payload.amount, p.party != payload.party, p.pay_type != payload.type,
        p.method != payload.method, p.status != payload.status, p.pay_date != payload.date])
    if changed:
        for transaction in session.scalars(select(BankTransaction).where(
                BankTransaction.tenant_id == p.tenant_id, BankTransaction.matched_payment_id == p.id,
                BankTransaction.is_deleted.is_(False))).all():
            transaction.matched_payment_id, transaction.status = None, "Unmatched"
    if changed and p.posted and p.gl_journal_id:
        old_journal = session.get(JournalEntry, p.gl_journal_id)
        if old_journal:
            repo.reverse_journal(session, public_id=old_journal.public_id)
        p.posted, p.gl_journal_id = False, None
    p = repo.update_payment(session, public_id=p.public_id, **_payment_fields(payload))
    p.party_id, p.party_type = None, None
    from app.models.sales import Customer
    from app.models.procurement import Supplier
    party_model = Customer if p.pay_type == "Receipt" else Supplier
    party_id = session.scalar(select(party_model.id).where(party_model.tenant_id == p.tenant_id,
        party_model.name == p.party, party_model.is_deleted.is_(False)).limit(1))
    if party_id:
        p.party_id, p.party_type = party_id, "customer" if p.pay_type == "Receipt" else "supplier"
    session.flush()
    _sync_payment_document(session, old_doc, old_type)
    _sync_payment_document(session, doc, p.pay_type)
    post_unposted(session, tenant_id=p.tenant_id)
    return p


# ---------- Aging ----------

def _aging_screen(rows: list[dict], entity_label: str, name_key: str, action: str) -> dict:
    grid = []
    for r in rows:
        buckets = [r["current_amt"], r["b1_30"], r["b31_60"], r["b61_90"], r["b90_plus"]]
        total = sum((b or 0) for b in buckets)
        grid.append([
            text_cell(r[name_key], avatar=True, sub=r["region"] or ""),
            *[text_cell(_eur(b), align="right", mono=True) for b in buckets],
            text_cell(_eur(total), align="right", mono=True, strong=True),
        ])
    return list_config(
        columns=[{"label": entity_label},
                 {"label": "Current", "align": "right"}, {"label": "1–30", "align": "right"},
                 {"label": "31–60", "align": "right"}, {"label": "61–90", "align": "right"},
                 {"label": "90+", "align": "right"}, {"label": "Total", "align": "right"}],
        rows=grid, total=len(rows),
        # "Region" is only shown as the entity cell's sub-text — surfaced here
        # so the Region filter has real data to filter on.
        records=[{"region": r["region"]} for r in rows],
        search=f"Search {entity_label.lower()}s…", action=action, filters=["Region"],
    )


def araging_screen(session: Session) -> dict:
    return _aging_screen(repo.list_ar_aging(session), "Customer", "customer_name", "Send reminders")


def apaging_screen(session: Session) -> dict:
    return _aging_screen(repo.list_ap_aging(session), "Supplier", "supplier_name", "Schedule payments")


def _overdue(r: dict) -> float:
    return float(r["b1_30"] + r["b31_60"] + r["b61_90"] + r["b90_plus"])


def _outstanding(r: dict) -> float:
    return float(r["current_amt"]) + _overdue(r)


def send_ar_reminders(session: Session, *, tenant_id) -> dict:
    """Record a payment reminder for every customer with an overdue balance."""
    tid = _tid(tenant_id)
    count = 0
    total = 0.0
    for r in repo.list_ar_aging(session):
        overdue = _overdue(r)
        if overdue > 0:
            write_audit(session, tenant_id=tid, action="REMINDER",
                        entity_type="ar_reminder", entity_id=r["customer_name"],
                        detail=f"Payment reminder sent to {r['customer_name']} (€{overdue:,.0f} overdue)")
            count += 1
            total += overdue
    return {"count": count, "total": total}


def schedule_ap_payments(session: Session, *, tenant_id) -> dict:
    """Flip every open supplier bill to Scheduled and log it."""
    tid = _tid(tenant_id)
    count = 0
    total = 0.0
    for b in repo.list_open_bills(session):
        b.status = "Scheduled"
        write_audit(session, tenant_id=tid, action="SCHEDULE", entity_type="ap_schedule",
                    entity_id=b.bill_no or b.supplier_name,
                    detail=f"Payment scheduled for {b.supplier_name} ({b.bill_no}) €{b.amount:,.0f}")
        count += 1
        total += float(b.amount or 0)
    return {"count": count, "total": total}


# ---------- Supplier bills (payables) ----------

def bills_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_bills(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["bill_no"] or "—", mono=True, strong=True),
            r["supplier_name"],
            r["po_ref"] or "—",
            text_cell(_eur(r["amount"]), align="right", mono=True, strong=True),
            r["due_on"].strftime("%d %b %Y") if r["due_on"] else "—",
            text_cell(r["status"], badge=_BILL_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Bill"}, {"label": "Supplier"}, {"label": "PO"},
                 {"label": "Amount", "align": "right"}, {"label": "Due"}, {"label": "Status"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{
            "supplier": r["supplier_name"], "poRef": r["po_ref"],
            "amount": float(r["amount"]) if r["amount"] is not None else None,
            "dueDate": r["due_on"].isoformat() if r["due_on"] else None,
            "status": r["status"],
        } for r in rows],
        search="Search bills…", action="New bill", filters=["Status", "Supplier"],
    )


def _bill_fields(p) -> dict:
    return {"supplier_name": p.supplier, "po_ref": p.poRef, "amount": p.amount,
            "due_on": _parse_date(p.dueDate), "status": p.status}


def create_bill(session, *, tenant_id, payload: BillCreate):
    bill = repo.create_bill(session, tenant_id=_tid(tenant_id), **_bill_fields(payload))
    post_unposted(session, tenant_id=tenant_id)  # post to the GL at transaction time
    return bill


def update_bill(session, *, public_id: str, payload: BillUpdate):
    from app.models.procurement import PoInvoice
    from fastapi import HTTPException
    bill = repo.bill_by_public(session, public_id=public_id)
    if bill and session.scalar(select(PoInvoice.id).where(PoInvoice.supplier_bill_id == bill.id,
        PoInvoice.tenant_id == bill.tenant_id, PoInvoice.is_deleted.is_(False)).limit(1)):
        if (payload.supplier != bill.supplier_name or payload.poRef != bill.po_ref
                or payload.amount != bill.amount):
            raise HTTPException(409, "This payable is linked to a procurement invoice. Reconcile the linked invoice before changing its supplier, PO or amount.")
    return repo.update_bill(session, public_id=public_id, **_bill_fields(payload))

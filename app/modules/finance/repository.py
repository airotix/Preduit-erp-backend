"""Finance data access. Tenant-filtered automatically by RLS."""
import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.catalog import Category, Product
from app.models.finance import (
    Account, BankAccount, BankTransaction, BudgetLine, CreditNote, FiscalPeriod,
    FixedAsset, JournalEntry, JournalLine, LedgerEntry, Payment, SupplierBill,
)
from app.models.procurement import Supplier
from app.models.sales import Customer, Invoice, SalesOrderLine

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------- Overview aggregations ----------

def _sum_accounts(session, *conds) -> float:
    return float(session.execute(
        select(func.coalesce(func.sum(Account.balance), 0))
        .where(Account.is_deleted == False, *conds)  # noqa: E712
    ).scalar_one() or 0)


def overview_metrics(session: Session) -> dict:
    """Consolidated finance position for the Overview screen — cash & P&L derived
    from posted GL (trial balance) so they match the statements; AR/AP from the
    open subledger documents."""
    mags = account_magnitudes(session)

    def _sum(pred) -> float:
        return round(sum(m["amount"] for m in mags if pred(m)), 2)

    def _is_cash(m) -> bool:
        n = (m["name"] or "").lower()
        return m["acct_type"] == "Asset" and ("cash" in n or "bank" in n)

    cash_total = _sum(_is_cash)
    cash_count = sum(1 for m in mags if _is_cash(m))

    # AR — unpaid customer invoices.
    ar_rows = session.execute(
        select(Invoice.amount).where(
            Invoice.is_deleted == False,  # noqa: E712
            Invoice.status.notin_(["Paid", "Void"]),
        )
    ).scalars().all()
    ar_total = float(sum(ar_rows) or 0)

    # AP — unpaid supplier bills.
    ap_rows = session.execute(
        select(SupplierBill.amount).where(
            SupplierBill.is_deleted == False,  # noqa: E712
            SupplierBill.status != "Paid",
        )
    ).scalars().all()
    ap_total = float(sum(ap_rows) or 0)

    revenue = _sum(lambda m: m["acct_type"] in ("Income", "Revenue"))
    cogs = _sum(lambda m: m["acct_type"] == "Expense"
                and ("cost of goods" in (m["name"] or "").lower()
                     or "cogs" in (m["name"] or "").lower()))
    expense_total = _sum(lambda m: m["acct_type"] == "Expense")
    opex = max(expense_total - cogs, 0.0)
    tax = _sum(lambda m: "vat" in (m["name"] or "").lower()
               or "income tax" in (m["name"] or "").lower())

    return {
        "cash_total": cash_total, "cash_count": cash_count,
        "ar_total": ar_total, "ar_count": len(ar_rows),
        "ap_total": ap_total, "ap_count": len(ap_rows),
        "revenue": revenue, "cogs": cogs, "opex": opex, "tax": tax,
    }


def revenue_by_month(session: Session, *, months: int = 6) -> list[dict]:
    """Trailing revenue by calendar month from invoices (real issued dates)."""
    rows = session.execute(
        select(Invoice.issued_date, Invoice.amount).where(
            Invoice.is_deleted == False, Invoice.issued_date.isnot(None)  # noqa: E712
        )
    ).all()
    agg: dict[tuple, float] = {}
    for issued, amount in rows:
        key = (issued.year, issued.month)
        agg[key] = agg.get(key, 0.0) + float(amount or 0)
    ordered = sorted(agg.keys())[-months:]
    return [{"label": _MONTHS[m - 1], "revenue": agg[(y, m)]} for (y, m) in ordered]


def recent_journal_lines(session: Session, *, limit: int = 6) -> list[dict]:
    """Most recent journal lines (one row per posting) for the Overview feed."""
    entries = session.execute(
        select(JournalEntry.id, JournalEntry.entry_no, JournalEntry.entry_date)
        .where(JournalEntry.is_deleted == False)  # noqa: E712
        .order_by(JournalEntry.id.desc()).limit(limit)
    ).all()
    out: list[dict] = []
    for eid, entry_no, entry_date in entries:
        for ln in session.execute(
            select(JournalLine.account, JournalLine.description, JournalLine.debit, JournalLine.credit)
            .where(JournalLine.entry_id == eid).order_by(JournalLine.id)
        ):
            m = ln._mapping
            out.append({
                "date": entry_date, "entry": entry_no, "account": m["account"],
                "memo": m["description"], "debit": float(m["debit"] or 0),
                "credit": float(m["credit"] or 0),
            })
            if len(out) >= limit:
                return out
    return out


def _list(session, columns, model, order, limit, offset):
    stmt = (select(*columns).where(model.is_deleted == False)  # noqa: E712
            .order_by(order).limit(limit).offset(offset))
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(model).where(model.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


# ---------- Chart of accounts ----------

def list_accounts(session, *, limit, offset):
    stmt = (
        select(Account.public_id, Account.code, Account.name, Account.acct_type,
               Account.subtype, Account.description, Account.currency_code,
               Account.opening_balance, Account.tax_rate, Account.parent_code,
               Account.is_active, Account.balance)
        .where(Account.is_deleted == False)  # noqa: E712
        .order_by(Account.code).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Account).where(Account.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def _apply_account_fields(a: Account, *, code, name, acct_type, subtype, currency,
                          opening_balance, tax_rate, parent, description, active) -> None:
    a.code = code
    a.name = name
    a.acct_type = acct_type
    a.subtype = subtype
    a.currency_code = currency or "EUR"
    a.opening_balance = opening_balance or 0
    a.balance = opening_balance or 0
    a.tax_rate = tax_rate
    a.parent_code = parent
    a.description = description
    a.is_active = active


def create_account(session: Session, *, tenant_id: UUID, **fields) -> Account:
    a = Account(tenant_id=tenant_id)
    _apply_account_fields(a, **fields)
    session.add(a)
    session.flush()
    session.refresh(a)
    return a


def update_account(session: Session, *, public_id: str, **fields) -> Account | None:
    a = session.execute(
        select(Account).where(Account.public_id == public_id,
                              Account.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if a is None:
        return None
    _apply_account_fields(a, **fields)
    session.flush()
    session.refresh(a)
    return a


# ---------- Journal entries ----------

def list_journals(session, *, limit, offset):
    return _list(session, [JournalEntry.public_id, JournalEntry.entry_no, JournalEntry.entry_date,
                           JournalEntry.memo, JournalEntry.total_debit, JournalEntry.total_credit,
                           JournalEntry.status],
                 JournalEntry, JournalEntry.id.desc(), limit, offset)


def _apply_journal_fields(je: JournalEntry, *, reference, memo, debit, credit, status, date) -> None:
    je.entry_no = reference
    je.memo = memo
    je.total_debit = debit
    je.total_credit = credit
    je.status = status
    je.entry_date = date


def create_journal(session: Session, *, tenant_id: UUID, **fields) -> JournalEntry:
    je = JournalEntry(tenant_id=tenant_id)
    _apply_journal_fields(je, **fields)
    session.add(je)
    session.flush()
    session.refresh(je)
    return je


def update_journal(session: Session, *, public_id: str, **fields) -> JournalEntry | None:
    je = session.execute(
        select(JournalEntry).where(JournalEntry.public_id == public_id,
                                   JournalEntry.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if je is None:
        return None
    _apply_journal_fields(je, **fields)
    session.flush()
    session.refresh(je)
    return je


def get_journal_detail(session: Session, *, public_id: str) -> dict | None:
    je = session.execute(
        select(JournalEntry).where(JournalEntry.public_id == public_id,
                                   JournalEntry.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if je is None:
        return None
    lines = [dict(r._mapping) for r in session.execute(
        select(JournalLine.account, JournalLine.description, JournalLine.debit, JournalLine.credit)
        .where(JournalLine.entry_id == je.id)
    )]
    return {"entry": je, "lines": lines}


# ---------- Payments ----------

def list_payments(session, *, limit, offset):
    return _list(session, [Payment.public_id, Payment.payment_no, Payment.pay_date, Payment.party,
                           Payment.allocated_to, Payment.amount, Payment.pay_type, Payment.status,
                           Payment.method, Payment.reference, Payment.notes],
                 Payment, Payment.id.desc(), limit, offset)


def _apply_payment_fields(p: Payment, *, party, amount, pay_type, method, reference,
                          allocated_to, pay_date, status, notes) -> None:
    p.party = party
    p.amount = amount
    p.pay_type = pay_type
    p.method = method
    p.reference = reference
    p.allocated_to = allocated_to
    p.pay_date = pay_date
    p.status = status
    p.notes = notes


def create_payment(session: Session, *, tenant_id: UUID, **fields) -> Payment:
    p = Payment(tenant_id=tenant_id)
    _apply_payment_fields(p, **fields)
    # Link to the party by id (customer for receipts, supplier otherwise).
    if p.pay_type == "Receipt":
        cid = session.execute(select(Customer.id).where(Customer.name == p.party)).scalars().first()
        if cid:
            p.party_type, p.party_id = "customer", cid
    else:
        sup = find_supplier(session, name=p.party)
        if sup:
            p.party_type, p.party_id = "supplier", sup.id
    session.add(p)
    session.flush()
    p.payment_no = f"PMT-{2200 + p.id}"
    session.flush()
    session.refresh(p)
    return p


def update_payment(session: Session, *, public_id: str, **fields) -> Payment | None:
    p = session.execute(
        select(Payment).where(Payment.public_id == public_id,
                              Payment.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if p is None:
        return None
    _apply_payment_fields(p, **fields)
    session.flush()
    session.refresh(p)
    return p


# ---------- Aging ----------

_EMPTY_BUCKETS = {"current_amt": 0.0, "b1_30": 0.0, "b31_60": 0.0, "b61_90": 0.0, "b90_plus": 0.0}


def _bucket(due_on, today: datetime.date) -> str:
    if not due_on:
        return "current_amt"
    days = (today - due_on).days
    if days <= 0:
        return "current_amt"
    if days <= 30:
        return "b1_30"
    if days <= 60:
        return "b31_60"
    if days <= 90:
        return "b61_90"
    return "b90_plus"


def list_ar_aging(session: Session) -> list[dict]:
    """Live AR aging: unpaid invoices bucketed by how overdue they are."""
    today = datetime.date.today()
    stmt = (
        select(Invoice.customer_name, Customer.region, Invoice.amount, Invoice.due_on)
        .outerjoin(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.status != "Paid", Invoice.is_deleted == False)  # noqa: E712
    )
    agg: dict[tuple, dict] = {}
    for r in session.execute(stmt):
        m = r._mapping
        k = (m["customer_name"], m["region"])
        row = agg.setdefault(k, {"customer_name": m["customer_name"], "region": m["region"], **_EMPTY_BUCKETS})
        row[_bucket(m["due_on"], today)] += float(m["amount"] or 0)
    return sorted(agg.values(), key=lambda x: x["customer_name"])


def list_ap_aging(session: Session) -> list[dict]:
    """Live AP aging: unpaid supplier bills (Open or Scheduled) bucketed by age."""
    today = datetime.date.today()
    stmt = (
        select(SupplierBill.supplier_name, Supplier.region, SupplierBill.amount, SupplierBill.due_on)
        .outerjoin(Supplier, Supplier.name == SupplierBill.supplier_name)
        .where(SupplierBill.status != "Paid", SupplierBill.is_deleted == False)  # noqa: E712
    )
    agg: dict[tuple, dict] = {}
    for r in session.execute(stmt):
        m = r._mapping
        k = (m["supplier_name"], m["region"])
        row = agg.setdefault(k, {"supplier_name": m["supplier_name"], "region": m["region"], **_EMPTY_BUCKETS})
        row[_bucket(m["due_on"], today)] += float(m["amount"] or 0)
    return sorted(agg.values(), key=lambda x: x["supplier_name"])


# ---------- Supplier bills (payables) ----------

def list_bills(session, *, limit, offset):
    return _list(session, [SupplierBill.public_id, SupplierBill.bill_no, SupplierBill.supplier_name,
                           SupplierBill.po_ref, SupplierBill.amount, SupplierBill.due_on,
                           SupplierBill.status],
                 SupplierBill, SupplierBill.id.desc(), limit, offset)


def _apply_bill_fields(b: SupplierBill, *, supplier_name, po_ref, amount, due_on, status) -> None:
    b.supplier_name = supplier_name
    b.po_ref = po_ref
    b.amount = amount
    b.due_on = due_on
    b.status = status


def create_bill(session: Session, *, tenant_id: UUID, **fields) -> SupplierBill:
    b = SupplierBill(tenant_id=tenant_id)
    _apply_bill_fields(b, **fields)
    sup = find_supplier(session, name=b.supplier_name)
    if sup:
        b.supplier_id = sup.id
    session.add(b)
    session.flush()
    b.bill_no = f"BILL-{1200 + b.id}"
    session.flush()
    session.refresh(b)
    return b


def update_bill(session: Session, *, public_id: str, **fields) -> SupplierBill | None:
    b = session.execute(
        select(SupplierBill).where(SupplierBill.public_id == public_id,
                                   SupplierBill.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if b is None:
        return None
    _apply_bill_fields(b, **fields)
    session.flush()
    session.refresh(b)
    return b


def list_open_bills(session: Session) -> list[SupplierBill]:
    return list(session.execute(
        select(SupplierBill).where(SupplierBill.status == "Open",
                                   SupplierBill.is_deleted == False)  # noqa: E712
    ).scalars())


# ---------- Ledgers (running statements) ----------

_MONTH_ABBR = {m.lower(): i + 1 for i, m in enumerate(_MONTHS)}


def _parse_dm(s) -> datetime.date | None:
    """Best-effort parse of a loose 'DD Mon' / 'Mon DD' / ISO date string."""
    if not s:
        return None
    if isinstance(s, datetime.date):
        return s
    parts = str(s).replace(",", " ").split()
    try:
        iso = datetime.date.fromisoformat(str(s)[:10])
        return iso
    except ValueError:
        pass
    try:
        if len(parts) >= 2:
            a, b = parts[0], parts[1]
            if a.isdigit():
                day, mon = int(a), _MONTH_ABBR.get(b[:3].lower())
            else:
                mon, day = _MONTH_ABBR.get(a[:3].lower()), int(b)
            if mon:
                return datetime.date(datetime.date.today().year, mon, day)
    except (ValueError, KeyError):
        return None
    return None


def _customer_entries(session: Session, customer_id: int, customer_name: str | None = None) -> list[dict]:
    """Ledger entries for a customer. Match invoices/receipts by customer_id OR
    matching customer name, so documents that were only name-linked (customer_id
    null or set before the customer existed) still appear on the ledger."""
    entries: list[dict] = []
    inv_match = [Invoice.customer_id == customer_id]
    pay_match = [and_(Payment.party_type == "customer", Payment.party_id == customer_id)]
    if customer_name:
        inv_match.append(Invoice.customer_name == customer_name)
        pay_match.append(Payment.party == customer_name)
    for inv in session.execute(
        select(Invoice).where(Invoice.is_deleted == False, or_(*inv_match))  # noqa: E712
    ).scalars():
        entries.append({"date": inv.issued_date, "ref": inv.invoice_no or "INV",
                        "desc": "Sales invoice", "debit": float(inv.amount or 0), "credit": 0.0,
                        "kind": "invoice", "public_id": str(inv.public_id),
                        "paid": inv.status == "Paid", "base_amount": float(inv.amount or 0)})
    for p in session.execute(
        select(Payment).where(
            Payment.is_deleted == False, Payment.pay_type == "Receipt",  # noqa: E712
            or_(*pay_match),
        )
    ).scalars():
        entries.append({"date": _parse_dm(p.pay_date), "ref": p.payment_no or "RCPT",
                        "desc": p.notes or "Payment received", "debit": 0.0,
                        "credit": float(p.amount or 0)})
    for cn in session.execute(
        select(CreditNote).where(CreditNote.is_deleted == False,  # noqa: E712
                                 CreditNote.customer_id == customer_id)
    ).scalars():
        entries.append({"date": cn.cn_date, "ref": cn.cn_no or "CN",
                        "desc": cn.reason or "Credit note", "debit": 0.0,
                        "credit": float(cn.amount or 0)})
    for le in session.execute(
        select(LedgerEntry).where(LedgerEntry.is_deleted == False,  # noqa: E712
                                  LedgerEntry.customer_id == customer_id)
    ).scalars():
        entries.append({"date": le.entry_date, "ref": "ENTRY",
                        "desc": le.description or "Ledger entry",
                        "debit": float(le.debit or 0), "credit": float(le.credit or 0)})
    entries.sort(key=lambda e: e["date"] or datetime.date.max)
    return entries


def _supplier_entries(session: Session, supplier_id: int) -> list[dict]:
    entries: list[dict] = []
    for b in session.execute(
        select(SupplierBill).where(SupplierBill.is_deleted == False,  # noqa: E712
                                   SupplierBill.supplier_id == supplier_id)
    ).scalars():
        entries.append({"date": b.due_on, "ref": b.bill_no or "BILL",
                        "desc": b.po_ref or "Supplier bill", "debit": 0.0,
                        "credit": float(b.amount or 0)})
    for p in session.execute(
        select(Payment).where(Payment.is_deleted == False,  # noqa: E712
                              Payment.party_type == "supplier", Payment.party_id == supplier_id,
                              Payment.pay_type.in_(["Disbursement", "Payment"]))
    ).scalars():
        entries.append({"date": _parse_dm(p.pay_date), "ref": p.payment_no or "PAY",
                        "desc": p.notes or "Payment", "debit": float(p.amount or 0), "credit": 0.0})
    entries.sort(key=lambda e: e["date"] or datetime.date.max)
    return entries


def customer_parties(session: Session) -> list[dict]:
    out = []
    for c in session.execute(
        select(Customer).where(Customer.is_deleted == False).order_by(Customer.name)  # noqa: E712
    ).scalars():
        entries = _customer_entries(session, c.id, c.name)
        debit = sum(e["debit"] for e in entries)
        credit = sum(e["credit"] for e in entries)
        bal = float(c.opening_balance or 0) + debit - credit
        out.append({"public_id": str(c.public_id), "name": c.name, "code": c.code, "balance": bal})
    return out


def supplier_parties(session: Session) -> list[dict]:
    out = []
    for s in session.execute(
        select(Supplier).where(Supplier.is_deleted == False).order_by(Supplier.name)  # noqa: E712
    ).scalars():
        entries = _supplier_entries(session, s.id)
        debit = sum(e["debit"] for e in entries)
        credit = sum(e["credit"] for e in entries)
        bal = float(s.opening_balance or 0) + credit - debit
        out.append({"public_id": str(s.public_id), "name": s.name, "code": s.code, "balance": bal})
    return out


def customer_statement(session: Session, *, public_id: str) -> dict | None:
    c = session.execute(
        select(Customer).where(Customer.public_id == public_id,
                               Customer.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if c is None:
        return None
    overdue = session.execute(
        select(func.count()).select_from(Invoice).where(
            Invoice.is_deleted == False, Invoice.customer_id == c.id,  # noqa: E712
            Invoice.status != "Paid", Invoice.due_on.isnot(None),
            Invoice.due_on < datetime.date.today(),
        )
    ).scalar_one()
    # Consolidated per-invoice view: each invoice carries the sum of receipts
    # allocated to it, so payments accumulate on the same line as their debit.
    inv_match = [Invoice.customer_id == c.id, Invoice.customer_name == c.name]
    invoices = session.execute(
        select(Invoice).where(Invoice.is_deleted == False, or_(*inv_match))  # noqa: E712
        .order_by(Invoice.issued_date, Invoice.id)
    ).scalars().all()
    inv_nos = {inv.invoice_no for inv in invoices if inv.invoice_no}

    receipts = session.execute(
        select(Payment).where(
            Payment.is_deleted == False, Payment.pay_type == "Receipt",  # noqa: E712
            or_(and_(Payment.party_type == "customer", Payment.party_id == c.id),
                Payment.party == c.name),
        )
    ).scalars().all()
    credit_by_inv: dict[str, float] = {}
    extras: list[dict] = []
    for p in receipts:
        alloc = (p.allocated_to or "").strip()
        amt = float(p.amount or 0)
        allocated = bool(alloc and alloc in inv_nos)
        if allocated:  # tracked only to compute the invoice's paid/remaining status
            credit_by_inv[alloc] = credit_by_inv.get(alloc, 0.0) + amt
        # Every receipt is its own credit line, dated when it was recorded and
        # referencing the original invoice it was paid against.
        extras.append({"date": _parse_dm(p.pay_date),
                       "ref": alloc if allocated else (p.payment_no or "RCPT"),
                       "desc": p.notes or "Payment received", "debit": 0.0,
                       "credit": amt, "editType": "payment", "editId": str(p.public_id)})
    for cn in session.execute(
        select(CreditNote).where(CreditNote.is_deleted == False,  # noqa: E712
                                 CreditNote.customer_id == c.id)
    ).scalars():
        extras.append({"date": cn.cn_date, "ref": cn.cn_no or "CN",
                       "desc": cn.reason or "Credit note", "debit": 0.0,
                       "credit": float(cn.amount or 0), "editType": "cn",
                       "editId": str(cn.public_id)})
    # Manual ledger entries (New entry): free-form debit/credit + description.
    for le in session.execute(
        select(LedgerEntry).where(LedgerEntry.is_deleted == False,  # noqa: E712
                                  LedgerEntry.customer_id == c.id)
    ).scalars():
        extras.append({"date": le.entry_date, "ref": "ENTRY",
                       "desc": le.description or "Ledger entry",
                       "debit": float(le.debit or 0), "credit": float(le.credit or 0),
                       "editType": "manual", "editId": str(le.public_id)})

    inv_rows = []
    for inv in invoices:
        amt = float(inv.amount or 0)
        applied = round(credit_by_inv.get(inv.invoice_no or "", 0.0), 2)
        inv_rows.append({
            # Invoice line shows the debit only — payments are their own credit
            # lines. `applied`/`remaining` drive the Record payment button/default.
            "date": inv.issued_date, "ref": inv.invoice_no or "INV",
            "desc": inv.memo or "Sales invoice",
            "debit": amt, "credit": 0.0, "remaining": round(amt - applied, 2),
            "public_id": str(inv.public_id), "editType": "invoice", "editId": str(inv.public_id),
            "paid": (inv.status == "Paid") or (amt > 0 and applied >= amt),
        })
    extras.sort(key=lambda e: e["date"] or datetime.date.max)
    return {"party": c, "opening": float(c.opening_balance or 0),
            "invoices": inv_rows, "extras": extras, "overdue": overdue}


def create_ledger_entry(session: Session, *, tenant_id: UUID, customer_id: int | None,
                        description: str, debit, credit) -> LedgerEntry:
    le = LedgerEntry(tenant_id=tenant_id, customer_id=customer_id,
                     entry_date=datetime.date.today(), description=description,
                     debit=debit or 0, credit=credit or 0)
    session.add(le)
    session.flush()
    session.refresh(le)
    return le


def customer_by_public(session: Session, *, public_id: str) -> Customer | None:
    return session.execute(
        select(Customer).where(Customer.public_id == public_id,
                               Customer.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def update_ledger_description(session: Session, *, edit_type: str, public_id: str,
                              description: str) -> bool:
    """Edit the description of any ledger row, routed to its source record."""
    model_field = {
        "invoice": (Invoice, "memo"),
        "manual": (LedgerEntry, "description"),
        "payment": (Payment, "notes"),
        "cn": (CreditNote, "reason"),
    }.get(edit_type)
    if model_field is None:
        return False
    model, field = model_field
    obj = session.execute(
        select(model).where(model.public_id == public_id,
                            model.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if obj is None:
        return False
    setattr(obj, field, description)
    session.flush()
    return True


def receipts_applied_to_invoice(session: Session, *, invoice_no: str | None,
                                customer_id: int | None, customer_name: str | None) -> float:
    """Total receipts already allocated to a specific invoice for a customer."""
    if not invoice_no:
        return 0.0
    party = [and_(Payment.party_type == "customer", Payment.party_id == customer_id)]
    if customer_name:
        party.append(Payment.party == customer_name)
    total = session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.is_deleted == False, Payment.pay_type == "Receipt",  # noqa: E712
            Payment.allocated_to == invoice_no, or_(*party),
        )
    ).scalar_one()
    return float(total or 0)


def supplier_statement(session: Session, *, public_id: str) -> dict | None:
    s = session.execute(
        select(Supplier).where(Supplier.public_id == public_id,
                               Supplier.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if s is None:
        return None
    return {"party": s, "opening": float(s.opening_balance or 0),
            "entries": _supplier_entries(session, s.id)}


# ---------- Profitability ----------

def list_accounts_min(session: Session) -> list[dict]:
    """Lightweight account list for the journal-entry account picker."""
    rows = session.execute(
        select(Account.code, Account.name, Account.acct_type)
        .where(Account.is_deleted == False, Account.is_active == True)  # noqa: E712
        .order_by(Account.code)
    ).all()
    return [{"code": c, "name": n, "label": f"{c} · {n}", "type": t} for c, n, t in rows]


def find_account_by_code(session: Session, *, code: str) -> Account | None:
    return session.execute(
        select(Account).where(Account.code == code, Account.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def find_account(session: Session, *, acct_type: str | None = None,
                 name_like: str | None = None) -> Account | None:
    conds = [Account.is_deleted == False]  # noqa: E712
    if acct_type:
        conds.append(Account.acct_type == acct_type)
    if name_like:
        conds.append(Account.name.ilike(name_like))
    return session.execute(
        select(Account).where(*conds).order_by(Account.code)
    ).scalars().first()


def _unposted(session: Session, model):
    return session.execute(
        select(model).where(model.is_deleted == False, model.posted == False)  # noqa: E712
    ).scalars().all()


def unposted_invoices(session):
    return _unposted(session, Invoice)


def unposted_bills(session):
    return _unposted(session, SupplierBill)


def unposted_payments(session):
    return _unposted(session, Payment)


def unposted_credit_notes(session):
    return _unposted(session, CreditNote)


def create_full_journal(session: Session, *, tenant_id: UUID, reference: str | None,
                        entry_on, entry_display: str, memo: str, lines: list[dict]) -> JournalEntry:
    """Persist a balanced multi-line journal entry as Posted."""
    total_debit = sum(float(l["debit"] or 0) for l in lines)
    total_credit = sum(float(l["credit"] or 0) for l in lines)
    je = JournalEntry(
        tenant_id=tenant_id, entry_no=reference or "JE", entry_date=entry_display,
        entry_on=entry_on, memo=memo, total_debit=total_debit, total_credit=total_credit,
        status="Posted", posted_at=datetime.datetime.utcnow(),
    )
    session.add(je)
    session.flush()
    if not reference:
        je.entry_no = f"JE-{5500 + je.id}"
    for l in lines:
        code = str(l["account"]).split("·")[0].strip().split()[0] if l["account"] else ""
        acct = find_account_by_code(session, code=code)
        session.add(JournalLine(
            tenant_id=tenant_id, entry_id=je.id, account=l["account"],
            account_id=acct.id if acct else None, description=l.get("description"),
            debit=l["debit"] or 0, credit=l["credit"] or 0,
        ))
    session.flush()
    session.refresh(je)
    return je


# ---------- Exchange rates (dated; used by the currency conversion + rate sync) ----------

def base_currency(session: Session) -> str | None:
    return session.execute(text(
        "SELECT base_currency_code FROM dbo.tenants "
        "WHERE id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)"
    )).scalar()


def currency_codes(session: Session) -> set[str]:
    """Currency codes known to the ERP (exchange_rates.from/to are FKs to this)."""
    rows = session.execute(text("SELECT code FROM dbo.currencies")).scalars().all()
    return {str(c).strip() for c in rows}


def list_rates(session: Session) -> list[dict]:
    rows = session.execute(text(
        "SELECT from_ccy, to_ccy, rate, valid_from, source FROM dbo.exchange_rates "
        "ORDER BY valid_from DESC, from_ccy, to_ccy"
    )).mappings().all()
    return [dict(r) for r in rows]


def upsert_rate(session: Session, *, tenant_id, from_ccy: str, to_ccy: str,
                rate: float, valid_from, source: str) -> None:
    """Update the dated rate if present, else insert. Avoids MERGE (fewer edge
    cases under RLS). UPDATE is auto-scoped to the tenant by the filter predicate;
    INSERT sets tenant_id explicitly to satisfy the block predicate."""
    params = {"tid": str(tenant_id), "f": from_ccy, "t": to_ccy, "r": rate,
              "vf": valid_from, "s": source}
    res = session.execute(text(
        "UPDATE dbo.exchange_rates SET rate = :r, source = :s "
        "WHERE from_ccy = :f AND to_ccy = :t AND valid_from = :vf"), params)
    if (res.rowcount or 0) == 0:
        session.execute(text(
            "INSERT INTO dbo.exchange_rates (tenant_id, from_ccy, to_ccy, rate, valid_from, source) "
            "VALUES (CAST(:tid AS UNIQUEIDENTIFIER), :f, :t, :r, :vf, :s)"), params)


def reverse_journal(session: Session, *, public_id: str) -> JournalEntry | None:
    src = session.execute(
        select(JournalEntry).where(JournalEntry.public_id == public_id,
                                   JournalEntry.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if src is None:
        return None
    lines = session.execute(
        select(JournalLine).where(JournalLine.entry_id == src.id)
    ).scalars().all()
    rev = JournalEntry(
        tenant_id=src.tenant_id, entry_no="JE", entry_date=src.entry_date,
        entry_on=datetime.date.today(), memo=f"Reversal of {src.entry_no}",
        total_debit=src.total_credit, total_credit=src.total_debit, status="Posted",
        posted_at=datetime.datetime.utcnow(), reversed_of_id=src.id,
    )
    session.add(rev)
    session.flush()
    rev.entry_no = f"JE-{5500 + rev.id}"
    for ln in lines:
        session.add(JournalLine(
            tenant_id=ln.tenant_id, entry_id=rev.id, account=ln.account,
            account_id=ln.account_id, description=f"Reversal — {ln.description or ''}".strip(" —"),
            debit=ln.credit, credit=ln.debit,
        ))
    session.flush()
    session.refresh(rev)
    return rev


def trial_balance(session: Session) -> list[dict]:
    """Per-account balances derived from POSTED journal lines + opening balance.
    Returns signed debit/credit columns for each account."""
    sums = dict(session.execute(
        select(JournalLine.account_id,
               func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.status == "Posted", JournalEntry.is_deleted == False,  # noqa: E712
               JournalLine.account_id.isnot(None))
        .group_by(JournalLine.account_id)
    ).all())
    rows = []
    for a in session.execute(
        select(Account).where(Account.is_deleted == False).order_by(Account.code)  # noqa: E712
    ).scalars():
        ns = a.normal_side or ("D" if a.acct_type in ("Asset", "Expense") else "C")
        # Opening/chart balances are stored as positive MAGNITUDES; convert to the
        # debit-minus-credit convention by signing per normal side (credit-normal
        # accounts carry a credit i.e. negative net). Fall back to `balance` when
        # no opening balance is set so statements always have substance.
        start = float(a.opening_balance or 0) or float(a.balance or 0)
        signed_start = start if ns == "D" else -start
        net = signed_start + float(sums.get(a.id, 0) or 0)  # debit-positive
        rows.append({
            "code": a.code, "name": a.name, "acct_type": a.acct_type,
            "normal_side": ns,
            "net": net,
            "debit": net if net >= 0 else 0.0,
            "credit": -net if net < 0 else 0.0,
        })
    return rows


def account_magnitudes(session: Session) -> list[dict]:
    """Each account's balance as a positive magnitude (posted journals + opening),
    the single source of truth used by the Overview KPIs and finance dashboards.
    Derived from `trial_balance` so it always matches the financial statements."""
    out = []
    for r in trial_balance(session):
        mag = r["net"] if r["normal_side"] == "D" else -r["net"]
        out.append({"code": r["code"], "name": r["name"],
                    "acct_type": r["acct_type"], "amount": round(mag, 2)})
    return out


def get_or_create_account(session: Session, *, tenant_id: UUID, code: str, name: str,
                          acct_type: str, normal_side: str) -> Account:
    a = find_account_by_code(session, code=code)
    if a:
        return a
    a = Account(tenant_id=tenant_id, code=code, name=name, acct_type=acct_type,
                currency_code="EUR", opening_balance=0, balance=0,
                normal_side=normal_side, is_active=True)
    session.add(a)
    session.flush()
    session.refresh(a)
    return a


# ---------- Bank reconciliation ----------

def list_bank_accounts(session: Session):
    return session.execute(
        select(BankAccount).where(BankAccount.is_deleted == False)  # noqa: E712
        .order_by(BankAccount.name)
    ).scalars().all()


def get_bank_account(session: Session, *, public_id):
    return session.execute(
        select(BankAccount).where(BankAccount.public_id == public_id,
                                  BankAccount.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def create_bank_account(session: Session, *, tenant_id: UUID, **fields) -> BankAccount:
    b = BankAccount(tenant_id=tenant_id, **fields)
    session.add(b)
    session.flush()
    session.refresh(b)
    return b


def list_bank_transactions(session: Session, *, bank_account_id: int):
    return session.execute(
        select(BankTransaction).where(BankTransaction.is_deleted == False,  # noqa: E712
                                      BankTransaction.bank_account_id == bank_account_id)
        .order_by(BankTransaction.txn_date, BankTransaction.id)
    ).scalars().all()


def create_bank_transaction(session: Session, *, tenant_id: UUID, bank_account_id: int,
                            txn_date, description, amount) -> BankTransaction:
    t = BankTransaction(tenant_id=tenant_id, bank_account_id=bank_account_id,
                        txn_date=txn_date, description=description, amount=amount,
                        status="Unmatched")
    session.add(t)
    session.flush()
    session.refresh(t)
    return t


def auto_match(session: Session, *, bank_account_id: int) -> int:
    """Match unmatched statement lines to payments by amount + direction."""
    txns = session.execute(
        select(BankTransaction).where(
            BankTransaction.is_deleted == False,  # noqa: E712
            BankTransaction.bank_account_id == bank_account_id,
            BankTransaction.status == "Unmatched",
        )
    ).scalars().all()
    used = {t.matched_payment_id for t in session.execute(
        select(BankTransaction).where(BankTransaction.matched_payment_id.isnot(None))
    ).scalars().all()}
    payments = session.execute(
        select(Payment).where(Payment.is_deleted == False)  # noqa: E712
    ).scalars().all()
    matched = 0
    for t in txns:
        want_type = "Receipt" if float(t.amount) >= 0 else "Disbursement"
        target = round(abs(float(t.amount)), 2)
        for p in payments:
            if p.id in used:
                continue
            p_type = "Receipt" if p.pay_type == "Receipt" else "Disbursement"
            if p_type == want_type and round(float(p.amount or 0), 2) == target:
                t.matched_payment_id = p.id
                t.status = "Matched"
                used.add(p.id)
                matched += 1
                break
    session.flush()
    return matched


def reconcile_matched(session: Session, *, bank_account_id: int) -> int:
    txns = session.execute(
        select(BankTransaction).where(
            BankTransaction.is_deleted == False,  # noqa: E712
            BankTransaction.bank_account_id == bank_account_id,
            BankTransaction.status == "Matched",
        )
    ).scalars().all()
    for t in txns:
        t.status = "Reconciled"
    session.flush()
    return len(txns)


def payment_no_by_id(session: Session, pid: int | None) -> str | None:
    if not pid:
        return None
    return session.execute(select(Payment.payment_no).where(Payment.id == pid)).scalars().first()


# ---------- Fiscal periods ----------

def list_periods(session: Session):
    return session.execute(
        select(FiscalPeriod).where(FiscalPeriod.is_deleted == False)  # noqa: E712
        .order_by(FiscalPeriod.start_date.desc())
    ).scalars().all()


def create_period(session: Session, *, tenant_id: UUID, name, start_date, end_date) -> FiscalPeriod:
    p = FiscalPeriod(tenant_id=tenant_id, name=name, start_date=start_date,
                     end_date=end_date, status="Open")
    session.add(p)
    session.flush()
    session.refresh(p)
    return p


def set_period_status(session: Session, *, public_id, status):
    p = session.execute(
        select(FiscalPeriod).where(FiscalPeriod.public_id == public_id,
                                   FiscalPeriod.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if p is None:
        return None
    p.status = status
    session.flush()
    return p


def is_closed_date(session: Session, d) -> bool:
    if d is None:
        return False
    return session.execute(
        select(func.count()).select_from(FiscalPeriod).where(
            FiscalPeriod.is_deleted == False, FiscalPeriod.status == "Closed",  # noqa: E712
            FiscalPeriod.start_date <= d, FiscalPeriod.end_date >= d,
        )
    ).scalar_one() > 0


# ---------- Budgets ----------

def list_budget_lines(session: Session, *, fiscal_year: int):
    return session.execute(
        select(BudgetLine).where(BudgetLine.is_deleted == False,  # noqa: E712
                                 BudgetLine.fiscal_year == fiscal_year)
        .order_by(BudgetLine.account_code)
    ).scalars().all()


def create_budget_line(session: Session, *, tenant_id: UUID, fiscal_year, account_code,
                       account_name, amount) -> BudgetLine:
    bl = BudgetLine(tenant_id=tenant_id, fiscal_year=fiscal_year, account_code=account_code,
                    account_name=account_name, amount=amount)
    session.add(bl)
    session.flush()
    session.refresh(bl)
    return bl


# ---------- Fixed assets ----------

def list_fixed_assets(session: Session):
    return session.execute(
        select(FixedAsset).where(FixedAsset.is_deleted == False)  # noqa: E712
        .order_by(FixedAsset.id.desc())
    ).scalars().all()


def create_fixed_asset(session: Session, *, tenant_id: UUID, **fields) -> FixedAsset:
    fa = FixedAsset(tenant_id=tenant_id, **fields)
    session.add(fa)
    session.flush()
    fa.asset_no = f"FA-{1000 + fa.id}"
    session.flush()
    session.refresh(fa)
    return fa


def get_fixed_asset(session: Session, *, public_id):
    return session.execute(
        select(FixedAsset).where(FixedAsset.public_id == public_id,
                                 FixedAsset.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def fx_factor(session: Session, *, target: str) -> float:
    """Conversion factor from the tenant's base currency to `target` (1.0 if same
    or no rate on file). Uses the latest exchange_rates row for this tenant."""
    base = session.execute(text(
        "SELECT base_currency_code FROM dbo.tenants "
        "WHERE id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)"
    )).scalar()
    if not base or not target or base == target:
        return 1.0
    rate = session.execute(
        text("SELECT TOP 1 rate FROM dbo.exchange_rates "
             "WHERE from_ccy = :b AND to_ccy = :t ORDER BY valid_from DESC"),
        {"b": base, "t": target},
    ).scalar()
    return float(rate) if rate else 1.0


def category_revenue(session: Session) -> list[dict]:
    """Revenue by product category from sales order lines (line name → product title)."""
    rows = session.execute(
        select(Category.name, func.coalesce(func.sum(SalesOrderLine.line_total), 0))
        .select_from(SalesOrderLine)
        .join(Product, Product.title == SalesOrderLine.name)
        .join(Category, Category.id == Product.category_id)
        .where(Product.is_deleted == False)  # noqa: E712
        .group_by(Category.name)
        .order_by(Category.name)
    ).all()
    return [{"segment": n, "revenue": float(v or 0)} for n, v in rows]

"""Sales business logic → frontend ScreenConfig."""
import datetime
import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.sales import repository as repo
from app.modules.sales.dto import (
    CustomerCreate, CustomerUpdate, InvoiceCreate, OrderCreate, ReturnCreate,
)
from app.presenters.screen import board_config, initials, list_config, text_cell

_TYPE_TONE = {"Wholesale": "navy", "Retail": "neutral"}
_CHANNEL_TONE = {"Wholesale": "navy", "Online": "green", "Marketplace": "amber", "Retail": "neutral"}
_ORDER_STATUS_TONE = {"New": "gray", "Picking": "amber", "Shipped": "green", "Cancelled": "red"}
_INV_STATUS_TONE = {"Open": "amber", "Paid": "green", "Overdue": "red"}
_RET_STATUS_TONE = {"Inspecting": "amber", "Refunded": "green", "Rejected": "red"}

# Fulfillment board columns: (status, title, accent, tone, metaIcon)
_BOARD_COLUMNS = [
    ("New", "New", "#9499A6", "neutral", "package"),
    ("Picking", "Picking", "#D29A22", "amber", "package"),
    ("Packed", "Packed", "#3A4256", "navy", "package"),
    ("Shipped", "Shipped", "#2E9E6B", "green", "truck"),
]


def _tid(tenant_id: str | UUID) -> UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def customers_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_customers(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], avatar=True, sub=r["email"]),
            text_cell(r["kind"], badge=_TYPE_TONE.get(r["kind"], "neutral")),
            r["region"] or "—",
            text_cell("0", align="center", mono=True),   # orders — wired when Orders lands
            text_cell("—", align="right", mono=True, strong=True),  # lifetime value
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Customer"}, {"label": "Type"}, {"label": "Region"},
            {"label": "Orders", "align": "center"},
            {"label": "Lifetime", "align": "right"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{
            "name": r["name"], "email": r["email"], "type": r["kind"],
            "region": r["region"], "phone": r["phone"], "address": r["address"],
        } for r in rows],
        search="Search customers…", action="New customer",
        filters=["Type", "Region"],
    )


def _customer_fields(p) -> dict:
    return {"name": p.name, "email": str(p.email), "kind": p.type,
            "region": p.region, "phone": p.phone, "address": p.address}


def create_customer(session: Session, *, tenant_id: str | UUID, payload: CustomerCreate):
    return repo.create_customer(session, tenant_id=_tid(tenant_id), **_customer_fields(payload))


def update_customer(session: Session, *, public_id: str, payload: CustomerUpdate):
    return repo.update_customer(session, public_id=public_id, **_customer_fields(payload))


# ---------- Orders ----------

def orders_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_orders(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        amount = f"€{r['total']:,.0f}" if r["total"] is not None else "—"
        grid.append([
            text_cell(f"#{r['order_no']}" if r["order_no"] else "—", strong=True, mono=True),
            text_cell(r["customer_name"], avatar=True, sub=r["channel"]),
            text_cell(r["channel"], badge=_CHANNEL_TONE.get(r["channel"], "neutral")),
            text_cell(str(r["item_count"]), align="center", mono=True),
            text_cell(amount, align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_ORDER_STATUS_TONE.get(r["status"], "gray")),
        ])
    return list_config(
        columns=[
            {"label": "Order"}, {"label": "Customer"}, {"label": "Channel"},
            {"label": "Items", "align": "center"},
            {"label": "Total", "align": "right"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search orders, customer…", action="New order",
        filters=["Channel", "Status", "Date"],
    )


def create_order(session: Session, *, tenant_id: str | UUID, payload: OrderCreate):
    tid = _tid(tenant_id)
    customer_id = repo.find_customer_id(session, name=payload.customer)
    lines = [{"name": l.name, "sku": l.sku, "qty": l.qty, "price": l.price}
             for l in payload.lines]
    return repo.create_order(
        session, tenant_id=tid, customer_id=customer_id, customer_name=payload.customer,
        channel=payload.channel, lines=lines,
    )


# ---------- Fulfillment board ----------

def board_screen(session: Session) -> dict:
    rows, _ = repo.list_orders(session, limit=200, offset=0)
    columns = []
    for status, title, accent, tone, meta_icon in _BOARD_COLUMNS:
        cards = []
        for r in rows:
            if r["status"] != status:
                continue
            cards.append({
                "ref": f"#{r['order_no']}" if r["order_no"] else "—",
                "title": r["customer_name"],
                "sub": f"{r['item_count']} items · {r['channel']}",
                "meta": f"€{r['total']:,.0f}" if r["total"] is not None else "—",
                "metaIcon": meta_icon,
                "av": initials(r["customer_name"]),
                "tone": tone,
            })
        columns.append({"title": title, "accent": accent, "count": len(cards), "cards": cards})
    return board_config(columns)


# ---------- Invoices ----------

def invoices_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_invoices(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        issued = r["issued_date"].strftime("%d %b") if r["issued_date"] else "—"
        amount = f"€{r['amount']:,.0f}" if r["amount"] is not None else "—"
        grid.append([
            text_cell(r["invoice_no"] or "—", strong=True, mono=True),
            r["customer_name"],
            issued,
            r["due_date"] or "—",
            text_cell(amount, align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_INV_STATUS_TONE.get(r["status"], "gray")),
        ])
    return list_config(
        columns=[
            {"label": "Invoice"}, {"label": "Customer"}, {"label": "Issued"},
            {"label": "Due"}, {"label": "Amount", "align": "right"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search invoices…", action="New invoice", filters=["Status", "Customer"],
    )


def _parse_date(s: str | None) -> datetime.date | None:
    """Accept an ISO date (YYYY-MM-DD) from a date input; ignore anything else."""
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def create_invoice(session: Session, *, tenant_id: str | UUID, payload: InvoiceCreate):
    tid = _tid(tenant_id)
    customer_id = repo.find_customer_id(session, name=payload.customer)
    due_on = _parse_date(payload.dueDate)
    due_display = due_on.strftime("%d %b %Y") if due_on else payload.dueDate
    return repo.create_invoice(
        session, tenant_id=tid, customer_id=customer_id, customer_name=payload.customer,
        amount=payload.amount, due_date=due_display, due_on=due_on,
    )


# ---------- Returns ----------

def returns_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_returns(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        refund = f"€{r['refund']:,.0f}" if r["refund"] is not None else "—"
        grid.append([
            text_cell(r["rma_no"] or "—", strong=True, mono=True),
            r["order_ref"] or "—",
            r["customer_name"],
            r["reason"] or "—",
            text_cell(refund, align="right", mono=True),
            text_cell(r["status"], badge=_RET_STATUS_TONE.get(r["status"], "gray")),
        ])
    return list_config(
        columns=[
            {"label": "RMA"}, {"label": "Order"}, {"label": "Customer"},
            {"label": "Reason"}, {"label": "Refund", "align": "right"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search RMAs…", action="New return", filters=["Reason", "Status"],
    )


def set_order_status(session, *, public_id, status):
    return repo.set_order_status(session, public_id=public_id, status=status)


def set_invoice_status(session, *, public_id, status):
    return repo.set_invoice_status(session, public_id=public_id, status=status)


def set_return_status(session, *, public_id, status):
    return repo.set_return_status(session, public_id=public_id, status=status)


def create_return(session: Session, *, tenant_id: str | UUID, payload: ReturnCreate):
    tid = _tid(tenant_id)
    return repo.create_return(
        session, tenant_id=tid, order_ref=payload.order, customer_name=payload.customer,
        reason=payload.reason, refund=payload.refund,
    )


# ---------- Detail pages ----------

def _money(v) -> str:
    return f"€{v:,.2f}" if v is not None else "—"


def _party(customer, fallback_name: str) -> dict:
    if customer is None:
        return {"name": fallback_name, "email": "", "phone": "", "addr": ""}
    return {
        "name": customer.name, "email": customer.email or "",
        "phone": customer.phone or "", "addr": customer.address or "",
    }


def _doc_lines(lines: list[dict]) -> list[dict]:
    return [
        {"name": l["name"], "sku": l["sku"] or "", "qty": l["qty"],
         "price": _money(l["price"]), "total": _money(l["line_total"])}
        for l in lines
    ]


_ORDER_STAGES = ["New", "Picking", "Packed", "Shipped"]


def order_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_order_detail(session, public_id=public_id)
    if data is None:
        return None
    o, lines, cust = data["order"], data["lines"], data["customer"]
    subtotal = sum((l["line_total"] or 0) for l in lines)
    idx = _ORDER_STAGES.index(o.status) if o.status in _ORDER_STAGES else 0
    stages = [("shopping-bag", "accent", "Order placed"), ("box", "amber", "Picking"),
              ("package", "navy", "Packed"), ("truck", "green", "Shipped")]
    timeline = [{"icon": ic, "tone": tn, "title": ti, "time": "", "done": i <= idx}
                for i, (ic, tn, ti) in enumerate(stages)]
    return {
        "variant": "order",
        "ref": f"#{o.order_no}" if o.order_no else "—",
        "title": o.customer_name,
        "statusLabel": o.status,
        "statusTone": _ORDER_STATUS_TONE.get(o.status, "neutral"),
        "meta": [
            {"k": "Channel", "v": o.channel},
            {"k": "Order date", "v": o.order_date.strftime("%d %b %Y") if o.order_date else "—"},
            {"k": "Items", "v": str(o.item_count)},
            {"k": "Total", "v": _money(o.total)},
        ],
        "tabs": ["Summary", "Items", "Fulfillment", "Invoices", "Activity"],
        "doc": {
            "lines": _doc_lines(lines),
            "totals": [{"k": "Subtotal", "v": _money(subtotal)}],
            "grand": _money(o.total),
            "timelineTitle": "Fulfillment status",
            "partyTitle": "Customer",
            "timeline": timeline,
            "party": _party(cust, o.customer_name),
        },
    }


def invoice_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_invoice_detail(session, public_id=public_id)
    if data is None:
        return None
    inv, lines, cust = data["invoice"], data["lines"], data["customer"]
    subtotal = sum((l["line_total"] or 0) for l in lines)
    paid = inv.status == "Paid"
    balance = 0 if paid else (inv.amount or 0)
    timeline = [
        {"icon": "file-text", "tone": "navy", "title": "Invoice issued",
         "time": inv.issued_date.strftime("%d %b %Y") if inv.issued_date else "", "done": True},
        {"icon": "banknote" if paid else "clock", "tone": "green" if paid else "amber",
         "title": "Paid in full" if paid else f"Balance due · {_money(balance)}",
         "time": inv.due_date or "", "done": paid},
    ]
    return {
        "variant": "invoice",
        "ref": inv.invoice_no or "—",
        "title": inv.customer_name,
        "statusLabel": inv.status,
        "statusTone": _INV_STATUS_TONE.get(inv.status, "neutral"),
        "meta": [
            {"k": "Customer", "v": inv.customer_name},
            {"k": "Issued", "v": inv.issued_date.strftime("%d %b %Y") if inv.issued_date else "—"},
            {"k": "Due", "v": inv.due_date or "—"},
            {"k": "Balance", "v": _money(balance)},
        ],
        "tabs": ["Summary", "Lines", "Payments", "Activity"],
        "doc": {
            "lines": _doc_lines(lines),
            "totals": [{"k": "Subtotal", "v": _money(subtotal)}],
            "grand": _money(inv.amount),
            "timelineTitle": "Payment activity",
            "partyTitle": "Billed to",
            "timeline": timeline,
            "party": _party(cust, inv.customer_name),
        },
    }


def customer_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_customer_detail(session, public_id=public_id)
    if data is None:
        return None
    c, orders = data["customer"], data["orders"]
    lifetime, open_inv = data["lifetime"], data["open_invoices"]
    count = len(orders)
    avg = (lifetime / count) if count else 0
    return {
        "variant": "entity",
        "ref": "CUSTOMER",
        "title": c.name,
        "statusLabel": c.kind,
        "statusTone": _TYPE_TONE.get(c.kind, "neutral"),
        "meta": [
            {"k": "Region", "v": c.region or "—"},
            {"k": "Orders", "v": str(count)},
            {"k": "Open invoices", "v": str(open_inv)},
            {"k": "Lifetime", "v": _money(lifetime)},
        ],
        "tabs": ["Overview", "Orders", "Invoices", "Activity"],
        "entity": {
            "scorecardTitle": "Customer health",
            "scorecard": [
                {"label": "Orders", "value": str(count), "sub": "all time", "tone": "navy"},
                {"label": "Avg order", "value": _money(avg), "sub": "lifetime", "tone": "accent"},
                {"label": "Lifetime value", "value": _money(lifetime), "sub": "revenue", "tone": "green"},
                {"label": "Open invoices", "value": str(open_inv), "sub": "unpaid", "tone": "amber"},
            ],
            "relatedTitle": "Recent orders",
            "related": [
                {"a": o["order_no"] or "—", "b": f"{o['item_count']} items · {o['channel']}",
                 "c": _money(o["total"]), "tone": _ORDER_STATUS_TONE.get(o["status"], "neutral"),
                 "s": o["status"]}
                for o in orders[:6]
            ],
            "contact": _party(c, c.name),
            "timeline": [
                {"icon": "shopping-bag", "tone": _ORDER_STATUS_TONE.get(o["status"], "neutral"),
                 "title": f"Order {o['order_no']} · {o['status']}", "time": _money(o["total"]), "done": True}
                for o in orders[:4]
            ],
        },
    }

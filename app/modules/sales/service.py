"""Sales business logic → frontend ScreenConfig."""
import datetime
import json
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
    stats = repo.order_stats_by_customer(session)
    grid = []
    for r in rows:
        orders, lifetime = stats.get(r["name"], (0, 0.0))
        grid.append([
            text_cell(r["name"], avatar=True, sub=r["email"]),
            text_cell(r["kind"], badge=_TYPE_TONE.get(r["kind"], "neutral")),
            r["region"] or "—",
            text_cell(str(orders), align="center", mono=True),
            text_cell(_money(lifetime) if orders else "—", align="right", mono=True, strong=True),
        ])
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
    name = payload.customer.strip()
    customer_id = repo.find_customer_id(session, name=name)
    if customer_id is None:
        # Auto-register a customer we haven't seen before, so they show up in
        # the Customers tab and can be reused on future orders.
        kind = "Wholesale" if payload.channel == "Wholesale" else "Retail"
        cust = repo.create_customer(
            session, tenant_id=tid, name=name, email="", kind=kind,
            region=None, phone=None, address=None,
        )
        customer_id = cust.id
    lines = [{"name": l.name, "color": l.color, "size": l.size, "sku": l.sku,
              "qty": l.qty, "price": l.price}
             for l in payload.lines]
    order = repo.create_order(
        session, tenant_id=tid, customer_id=customer_id, customer_name=payload.customer,
        channel=payload.channel, lines=lines,
    )
    # Kick off production: one work order for the whole sales order, linked back
    # to it so its detail can show the order's line items (Production → Orders).
    from app.modules.production import repository as prod_repo
    total_qty = sum(int(l["qty"] or 0) for l in lines)
    names: list[str] = []
    for l in lines:
        nm = (l["name"] or "").strip()
        if nm and nm not in names:
            names.append(nm)
    if total_qty > 0:
        if len(names) == 1:
            style_label = names[0]
        elif names:
            style_label = f"{names[0]} +{len(names) - 1} more"
        else:
            style_label = order.order_no or "Order"
        prod_repo.create_porder(session, tenant_id=tid, style=style_label, factory=None,
                                qty=total_qty, sales_order_id=order.id)
    return order


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
    inv = repo.create_invoice(
        session, tenant_id=tid, customer_id=customer_id, customer_name=payload.customer,
        amount=payload.amount, due_date=due_display, due_on=due_on,
    )
    # Post the new receivable to the GL at transaction time (local import avoids
    # a module-load cycle: finance depends on sales models).
    from app.modules.finance import service as fin_service
    fin_service.post_unposted(session, tenant_id=tid)
    return inv


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


# ============================================================================
# Commercial / retail invoices generated from a sales order
# Retail & Online → flat receipt layout · Wholesale → colour×size matrix.
# Mirrors the procurement PO-invoice feature.
# ============================================================================

_SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "5XL",
               "6XL", "7XL", "XL2", "XL3", "XL4"]
_INVOICE_TYPES = {"Retail", "Online", "Wholesale"}
_PRICE_KEY = {"Retail": "retail_price", "Online": "online_price", "Wholesale": "wholesale_price"}


def _size_rank(s: str) -> int:
    u = (s or "").upper().strip()
    return _SIZE_ORDER.index(u) if u in _SIZE_ORDER else len(_SIZE_ORDER) + 1


def _exporter_from_company(c: dict) -> dict:
    """Build the invoice letterhead (our business) from the company profile."""
    addr = ", ".join(p for p in [c.get("street"), c.get("city"), c.get("state"), c.get("postal")] if p)
    return {
        "name": c.get("name") or "",
        "address": addr,
        "country": c.get("country") or "",
        "tel": c.get("phone") or c.get("support_line") or "",
        "email": c.get("business_email") or "",
        "taxId": c.get("tax_registration") or c.get("registration_number") or "",
    }


def _channel_price(variant: dict | None, invoice_type: str, fallback) -> float:
    """Pick the price for the selected invoice type; fall back to the variant's
    base price, then to the order line's recorded price."""
    if variant:
        p = variant.get(_PRICE_KEY[invoice_type])
        if p is None:
            p = variant.get("base_price")
        if p is not None:
            return round(float(p), 4)
    return round(float(fallback or 0), 4)


def build_sales_invoice_draft(session: Session, *, order_no: str, invoice_type: str) -> dict | None:
    """Assemble a prefilled invoice document from a sales order. Colour/size and
    per-channel pricing are enriched by looking up each line's SKU in the catalog."""
    invoice_type = invoice_type if invoice_type in _INVOICE_TYPES else "Retail"
    order = repo.order_by_no(session, order_no=order_no)
    if order is None:
        return None
    lines = repo.order_lines(session, order_id=order.id)
    variants = repo.variants_by_sku(session, skus=[l["sku"] for l in lines])
    customer = repo._customer_by_id(session, order.customer_id)
    currency = order.currency_code or "USD"
    company = repo.company_info(session)
    biz = company.get("legal_name") or company.get("name") or ""
    today = datetime.date.today().isoformat()
    order_date = order.order_date.isoformat() if order.order_date else ""

    exporter = _exporter_from_company(company)
    buyer = {
        "name": customer.name if customer else order.customer_name,
        "phone": (customer.phone if customer else "") or "",
        "email": (customer.email if customer else "") or "",
        "taxId": (customer.code if customer else "") or "",
        "address": (customer.address if customer else "") or "",
        "country": (customer.region if customer else "") or "",
    }

    is_wholesale = invoice_type == "Wholesale"

    if is_wholesale:
        # group: article(style) -> { colors: {color: {qty:{size:q}, price}}, sizes, image, fabric, hs }
        articles: dict[str, dict] = {}
        for l in lines:
            v = variants.get((l["sku"] or "").strip())
            art = (l["name"] or (v or {}).get("title") or "Article").strip()
            # Prefer the colour/size captured on the order line; fall back to the
            # catalog variant looked up by SKU.
            color = (l.get("color") or (v or {}).get("color") or "—").strip()
            size = (l.get("size") or (v or {}).get("size") or "—").strip()
            qty = int(l["qty"] or 0)
            price = _channel_price(v, invoice_type, l["price"])
            a = articles.setdefault(art, {
                "colors": {}, "sizes": set(),
                "image": (v or {}).get("image"), "fabric": (v or {}).get("fabric") or "",
                "hs": (v or {}).get("hs_code") or "", "sku": (l["sku"] or ""),
            })
            row = a["colors"].setdefault(color, {"qty": {}, "price": price})
            row["qty"][size] = row["qty"].get(size, 0) + qty
            if price and not row["price"]:
                row["price"] = price
            a["sizes"].add(size)
            if not a["image"] and (v or {}).get("image"):
                a["image"] = (v or {}).get("image")

        article_list = []
        grand_qty, grand_amt = 0, 0.0
        for art, a in articles.items():
            sizes = sorted(a["sizes"], key=_size_rank)
            rows = []
            for color, cd in a["colors"].items():
                total = sum(cd["qty"].values())
                unit = round(float(cd["price"]), 4)
                rows.append({"color": color, "qty": cd["qty"], "total": total,
                             "unitPrice": unit, "amount": round(total * unit, 2)})
            sub_qty = sum(r["total"] for r in rows)
            sub_amt = round(sum(r["amount"] for r in rows), 2)
            grand_qty += sub_qty
            grand_amt += sub_amt
            article_list.append({
                "articleNo": "", "style": art, "description": art, "fabric": a["fabric"],
                "sku": a["sku"], "hsCode": a["hs"], "image": a["image"] or None,
                "sizes": sizes, "rows": rows, "subtotalQty": sub_qty, "subtotalAmount": sub_amt,
            })
        grand_amt = round(grand_amt, 2)
        return {
            "layout": "wholesale", "invoiceType": invoice_type,
            "invoiceNo": "", "invoiceDate": today, "orderNo": order.order_no or "",
            "poNo": order.order_no or "", "orderDate": order_date, "deliveryDate": "",
            "paymentTerms": "Net 30", "salesRep": "", "shipTo": "Same as buyer",
            "currency": currency,
            "exporter": exporter, "buyer": buyer,
            "articles": article_list,
            "remit": {"title": biz, "bank": "", "account": ""},
            "terms": "Payment Net 30 from invoice date. Minimum reorder 12 units per style.",
            "totals": {"totalQty": grand_qty, "subtotal": grand_amt, "discount": 0.0,
                       "taxRate": 0.0, "tax": 0.0, "freight": 0.0, "total": grand_amt},
            "preparedBy": "",
        }

    # ---- Retail / Online: flat receipt ----
    flat, grand_qty, grand_amt = [], 0, 0.0
    for l in lines:
        v = variants.get((l["sku"] or "").strip())
        qty = int(l["qty"] or 0)
        unit = _channel_price(v, invoice_type, l["price"])
        amount = round(qty * unit, 2)
        grand_qty += qty
        grand_amt += amount
        flat.append({
            "article": (l["name"] or (v or {}).get("title") or "").strip(),
            "colour": (l.get("color") or (v or {}).get("color") or ""),
            "size": (l.get("size") or (v or {}).get("size") or ""),
            "qty": qty, "unitPrice": unit, "amount": amount,
        })
    grand_amt = round(grand_amt, 2)
    return {
        "layout": "retail", "invoiceType": invoice_type,
        "invoiceNo": "", "invoiceDate": today, "orderRef": order.order_no or "",
        "paymentMethod": "Card", "cashier": "", "status": "Paid", "currency": currency,
        "exporter": exporter, "buyer": buyer,
        "lines": flat,
        "note": "Thank you for shopping with us. Exchanges within 14 days with this receipt. Sale items final.",
        "totals": {"totalQty": grand_qty, "subtotal": grand_amt, "discount": 0.0,
                   "taxRate": 0.0, "tax": 0.0, "total": grand_amt},
        "preparedBy": "",
    }


def _sinv_summary(inv) -> dict:
    return {
        "publicId": str(inv.public_id), "invoiceNo": inv.invoice_no, "orderNo": inv.order_no,
        "customer": inv.customer_name, "invoiceType": inv.invoice_type,
        "currency": inv.currency_code, "total": float(inv.total or 0), "status": inv.status,
        "createdAt": inv.created_at.isoformat() + "Z" if inv.created_at else None,
    }


def _sinv_persist_fields(data: dict) -> dict:
    totals = data.get("totals") or {}
    return {
        "invoice_no": (data.get("invoiceNo") or "").strip() or None,
        "order_no": data.get("orderNo") or data.get("orderRef") or data.get("poNo"),
        "customer_name": (data.get("buyer") or {}).get("name"),
        "invoice_type": data.get("invoiceType") or "Retail",
        "currency_code": (data.get("currency") or "USD")[:3],
        "total": float(totals.get("total") or 0),
    }


def list_sales_invoice_docs(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_sales_invoices(session, limit=limit, offset=offset)
    return {"invoices": [{
        "publicId": str(r["public_id"]), "invoiceNo": r["invoice_no"], "orderNo": r["order_no"],
        "customer": r["customer_name"], "invoiceType": r["invoice_type"],
        "currency": r["currency_code"], "total": float(r["total"] or 0), "status": r["status"],
        "createdAt": r["created_at"].isoformat() + "Z" if r["created_at"] else None,
    } for r in rows], "total": total}


def create_sales_invoice_doc(session: Session, *, tenant_id, data: dict) -> dict:
    f = _sinv_persist_fields(data)
    inv = repo.create_sales_invoice(session, tenant_id=_tid(tenant_id), data=json.dumps(data), **f)
    return {"publicId": str(inv.public_id), "invoiceNo": inv.invoice_no}


def get_sales_invoice_doc(session: Session, *, public_id: str) -> dict | None:
    inv = repo.get_sales_invoice(session, public_id=public_id)
    if inv is None:
        return None
    return {**_sinv_summary(inv), "data": json.loads(inv.data)}


def update_sales_invoice_doc(session: Session, *, public_id: str, data: dict) -> dict | None:
    inv = repo.get_sales_invoice(session, public_id=public_id)
    if inv is None:
        return None
    f = _sinv_persist_fields(data)
    inv.invoice_no = f["invoice_no"] or inv.invoice_no
    inv.order_no = f["order_no"]
    inv.customer_name = f["customer_name"]
    inv.invoice_type = f["invoice_type"]
    inv.currency_code = f["currency_code"]
    inv.total = f["total"]
    inv.data = json.dumps(data)
    session.flush()
    return {"publicId": str(inv.public_id), "invoiceNo": inv.invoice_no}

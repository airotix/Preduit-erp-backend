"""Procurement business logic → frontend ScreenConfig."""
import datetime
import json
import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.procurement import Supplier
from app.modules.procurement import repository as repo
from app.modules.procurement.dto import (
    GoodsReceiptCreate, PurchaseOrderCreate, SupplierCreate, SupplierUpdate,
)
from app.presenters.screen import board_config, initials, list_config, text_cell

_SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "5XL",
               "6XL", "7XL", "XL2", "XL3", "XL4"]


def _size_rank(s: str) -> int:
    u = (s or "").upper().strip()
    return _SIZE_ORDER.index(u) if u in _SIZE_ORDER else len(_SIZE_ORDER) + 1


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _under_1000(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return (_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")).strip()
    return (_ONES[n // 100] + " hundred" + (" " + _under_1000(n % 100) if n % 100 else "")).strip()


def _int_to_words(n: int) -> str:
    if n == 0:
        return "zero"
    parts, scales = [], [(1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand"), (1, "")]
    for value, name in scales:
        if n >= value:
            chunk = n // value
            parts.append(_under_1000(chunk) + (" " + name if name else ""))
            n %= value
    return " ".join(p for p in parts if p).strip()


def amount_in_words(amount: float, currency: str = "USD") -> str:
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    words = _int_to_words(whole).capitalize() + f" {currency}"
    if cents:
        words += f" and {cents}/100"
    return words + " only"


_PO_TONE = {"Pending approval": "amber", "Approved": "green", "Rejected": "red", "Received": "green"}
_GRN_TONE = {"Complete": "green", "Partial": "amber", "Expected": "neutral"}
_SUP_TONE = {"Preferred": "green", "On watch": "amber", "New": "neutral", "Suspended": "red"}

# Board columns: (po status, title, accent, tone)
_BOARD = [
    ("Pending approval", "Awaiting approval", "#D29A22", "amber"),
    ("Approved", "Approved", "#2E9E6B", "green"),
    ("Rejected", "Rejected", "#D9534F", "red"),
]


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def _num(v) -> str:
    return f"{v:g}" if v is not None else "—"


# ---------- Purchase orders ----------

def pos_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_pos(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["po_no"] or "—", strong=True, mono=True),
            text_cell(r["supplier_name"], avatar=True, sub=r["supplier_country"] or ""),
            text_cell(str(r["item_count"]), align="center", mono=True),
            text_cell(f"€{r['total']:,.0f}", align="right", mono=True, strong=True),
            r["expected"] or "—",
            text_cell(r["status"], badge=_PO_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "PO"}, {"label": "Supplier"}, {"label": "Items", "align": "center"},
            {"label": "Total", "align": "right"}, {"label": "Expected"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search purchase orders…", action="New PO", filters=["Supplier", "Status"],
    )


def create_po(session: Session, *, tenant_id: str | UUID, payload: PurchaseOrderCreate):
    tid = _tid(tenant_id)
    supplier = repo.find_supplier(session, name=payload.supplier)
    lines = [{"name": l.name, "color": l.color, "sku": l.sku, "qty": l.qty, "price": l.price}
             for l in payload.lines]
    return repo.create_po(
        session, tenant_id=tid, supplier=supplier, supplier_name=payload.supplier,
        expected=payload.expected, lines=lines,
    )


def _po_money(v) -> str:
    return f"€{v:,.2f}" if v is not None else "—"


def po_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_po_detail(session, public_id=public_id)
    if data is None:
        return None
    po, lines, sup = data["po"], data["lines"], data["supplier"]
    subtotal = sum((l["line_total"] or 0) for l in lines)
    stages = [("Pending approval", "file-text", "amber", "PO created · pending approval"),
              ("Approved", "check-circle-2", "green", "Approved"),
              ("Received", "package", "navy", "Goods received")]
    order = ["Pending approval", "Approved", "Received"]
    idx = order.index(po.status) if po.status in order else 0
    timeline = [{"icon": ic, "tone": tn, "title": ti, "time": "", "done": i <= idx}
                for i, (_, ic, tn, ti) in enumerate(stages)]
    return {
        "variant": "order",  # reuse the doc (line-items) renderer
        "ref": po.po_no or "—",
        "title": po.supplier_name,
        "statusLabel": po.status,
        "statusTone": _PO_TONE.get(po.status, "neutral"),
        "meta": [
            {"k": "Supplier", "v": po.supplier_name},
            {"k": "Expected", "v": po.expected or "—"},
            {"k": "Items", "v": str(po.item_count)},
            {"k": "Total", "v": _po_money(po.total)},
        ],
        "tabs": ["Summary", "Items", "Activity"],
        "doc": {
            "lines": [
                {"name": l["name"],
                 "sku": " · ".join(p for p in [l["color"], l["size"]] if p) or (l["sku"] or ""),
                 "qty": l["qty"], "price": _po_money(l["price"]),
                 "total": _po_money(l["line_total"])}
                for l in lines
            ],
            "totals": [{"k": "Subtotal", "v": _po_money(subtotal)}],
            "grand": _po_money(po.total),
            "timelineTitle": "Approval status",
            "partyTitle": "Supplier",
            "timeline": timeline,
            "party": {
                "name": sup.name if sup else po.supplier_name,
                "email": (sup.email or "") if sup else "",
                "phone": (sup.phone or "") if sup else "",
                "addr": (sup.address or "") if sup else "",
            },
        },
    }


def approvals_board(session: Session) -> dict:
    rows = repo.list_pos_for_board(session)
    columns = []
    for status, title, accent, tone in _BOARD:
        cards = []
        for r in rows:
            if r["status"] != status:
                continue
            cards.append({
                "ref": r["po_no"] or "—",
                "public_id": str(r["public_id"]),
                "title": r["supplier_name"],
                "sub": f"{r['item_count']} lines · {r['category'] or 'PO'}",
                "meta": f"€{r['total']:,.0f}",
                "metaIcon": "banknote",
                "av": initials(r["supplier_name"]),
                "tone": tone,
                "approvable": status == "Pending approval",
                "tag": "Approval" if status == "Pending approval" else None,
            })
        columns.append({"title": title, "accent": accent, "count": len(cards), "cards": cards})
    return board_config(columns)


# ---------- Goods receipts ----------

def receipts_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_receipts(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["grn_no"] or "—", strong=True, mono=True),
            r["po_ref"] or "—",
            text_cell(r["supplier_name"], avatar=True, sub=r["supplier_country"] or ""),
            text_cell(str(r["line_count"]), align="center", mono=True),
            text_cell(f"{r['received_count']} / {r['line_count']}", mono=True),
            text_cell(r["status"], badge=_GRN_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "GRN"}, {"label": "PO"}, {"label": "Supplier"},
            {"label": "Lines", "align": "center"}, {"label": "Received"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search goods receipts…", action="Receive against PO",
        filters=["Supplier", "Status"],
    )


def create_receipt(session: Session, *, tenant_id: str | UUID, payload: GoodsReceiptCreate):
    tid = _tid(tenant_id)
    supplier = repo.find_supplier(session, name=payload.supplier)
    return repo.create_receipt(
        session, tenant_id=tid, po_ref=payload.po, supplier=supplier,
        supplier_name=payload.supplier, lines=payload.lines,
    )


def set_po_status(session, *, public_id, status):
    return repo.set_po_status(session, public_id=public_id, status=status)


def set_receipt_status(session, *, public_id, status):
    return repo.set_receipt_status(session, public_id=public_id, status=status)


# ---------- Suppliers ----------

def suppliers_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_suppliers(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], avatar=True, sub=r["category"] or ""),
            r["region"] or "—",
            r["lead_time"] or "—",
            text_cell(f"{r['on_time_pct']}%", align="right", mono=True),
            text_cell(_num(r["score"]), align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_SUP_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Supplier"}, {"label": "Region"}, {"label": "Lead time"},
            {"label": "On-time", "align": "right"}, {"label": "Score", "align": "right"},
            {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "region": r["region"],
                  "leadTime": r["lead_time"], "category": r["category"]} for r in rows],
        search="Search suppliers…", action="New supplier", filters=["Region", "Rating"],
    )


def create_supplier(session: Session, *, tenant_id: str | UUID, payload: SupplierCreate):
    return repo.create_supplier(
        session, tenant_id=_tid(tenant_id), name=payload.name, region=payload.region,
        lead_time=payload.leadTime, category=payload.category,
    )


def update_supplier(session: Session, *, public_id: str, payload: SupplierUpdate):
    return repo.update_supplier(
        session, public_id=public_id, name=payload.name, region=payload.region,
        lead_time=payload.leadTime, category=payload.category,
    )


# ---------- Vendor scorecards ----------

def scorecard_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_suppliers(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], avatar=True, sub=r["category"] or ""),
            text_cell(f"{r['on_time_pct']}%", align="right", mono=True),
            text_cell(f"{r['defect_rate']}%" if r["defect_rate"] is not None else "—", align="right", mono=True),
            text_cell(_num(r["price_rating"]), align="right", mono=True),
            text_cell(_num(r["score"]), align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_SUP_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Supplier"}, {"label": "On-time", "align": "right"},
            {"label": "Defect rate", "align": "right"}, {"label": "Price rating", "align": "right"},
            {"label": "Score", "align": "right"}, {"label": "Rating"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        search="Search suppliers…", action="Export", filters=["Region"],
    )


# ---------- Detail pages ----------

def supplier_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_supplier_detail(session, public_id=public_id)
    if data is None:
        return None
    s, pos = data["supplier"], data["pos"]
    ytd, open_pos = data["ytd"], data["open_pos"]
    return {
        "variant": "entity",
        "ref": "SUPPLIER",
        "title": s.name,
        "statusLabel": s.status,
        "statusTone": _SUP_TONE.get(s.status, "neutral"),
        "meta": [
            {"k": "Region", "v": s.region or "—"},
            {"k": "Lead time", "v": s.lead_time or "—"},
            {"k": "Open POs", "v": str(open_pos)},
            {"k": "YTD spend", "v": f"€{ytd:,.0f}"},
        ],
        "tabs": ["Overview", "Purchase Orders", "Scorecard", "Activity"],
        "entity": {
            "scorecardTitle": "Vendor scorecard",
            "scorecard": [
                {"label": "On-time delivery", "value": f"{s.on_time_pct}%", "sub": "last 12 mo", "tone": "green"},
                {"label": "Defect rate", "value": f"{s.defect_rate}%" if s.defect_rate is not None else "—", "sub": "AQL 2.5", "tone": "amber"},
                {"label": "Price rating", "value": f"{_num(s.price_rating)} / 5", "sub": "vs market", "tone": "navy"},
                {"label": "Overall score", "value": _num(s.score), "sub": s.status, "tone": "accent"},
            ],
            "relatedTitle": "Recent purchase orders",
            "related": [
                {"a": p["po_no"] or "—", "b": f"{p['item_count']} lines",
                 "c": f"€{p['total']:,.0f}", "tone": _PO_TONE.get(p["status"], "neutral"),
                 "s": p["status"]}
                for p in pos[:6]
            ],
            "contact": {"name": s.name, "email": s.email or "",
                        "phone": s.phone or "", "addr": s.address or ""},
            "timeline": [
                {"icon": "file-text", "tone": _PO_TONE.get(p["status"], "neutral"),
                 "title": f"{p['po_no']} · {p['status']}", "time": f"€{p['total']:,.0f}", "done": True}
                for p in pos[:4]
            ],
        },
    }


def receipt_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_receipt_detail(session, public_id=public_id)
    if data is None:
        return None
    g, lines = data["grn"], data["lines"]

    def _line(l: dict) -> dict:
        out = (l["ordered"] or 0) - (l["received"] or 0)
        if out == 0:
            badge, tone = "Complete", "green"
        elif (l["received"] or 0) == 0:
            badge, tone = "Awaiting", "neutral"
        else:
            badge, tone = "Partial", "amber"
        return {"name": l["name"], "sku": l["sku"] or "", "ordered": l["ordered"],
                "received": l["received"], "outstanding": out, "badge": badge, "tone": tone}

    return {
        "variant": "receipt",
        "ref": g.grn_no or "—",
        "title": f"Receipt against {g.po_ref or '—'}",
        "statusLabel": g.status,
        "statusTone": _GRN_TONE.get(g.status, "neutral"),
        "meta": [
            {"k": "PO", "v": g.po_ref or "—"},
            {"k": "Supplier", "v": g.supplier_name},
            {"k": "Received", "v": g.received_date or "—"},
            {"k": "Location", "v": g.location or "—"},
        ],
        "tabs": ["Lines", "Documents", "Activity"],
        "receipt": {
            "lines": [_line(l) for l in lines],
            "note": (
                f"Receiving against {g.po_ref or 'the PO'} posts received quantities into "
                f"{g.location or 'inventory'} and accrues accounts payable. Outstanding units "
                "stay open until fully received."
            ),
        },
    }


# ---------- Commercial invoice from a PO ----------

def build_invoice_draft(session: Session, *, po_no: str) -> dict | None:
    """Assemble a prefilled commercial-invoice document from a PO. Lines are
    grouped by ARTICLE (product/style); each article gets a colour×size matrix."""
    po = repo.po_by_no(session, po_no=po_no)
    if po is None:
        return None
    lines = repo.po_lines(session, po_id=po.id)
    supplier = session.get(Supplier, po.supplier_id) if po.supplier_id else None
    currency = po.currency_code or "USD"
    images = repo.product_images(session)   # article (product title) → image URL

    # group: article -> { colors: {color: {qty:{size:q}, price}}, sizes:set }
    articles: dict[str, dict] = {}
    for l in lines:
        art = (l["name"] or "Article").strip()
        color = (l["color"] or "—").strip()
        size = (l["size"] or "—").strip()
        qty = int(l["qty"] or 0)
        price = float(l["price"] or 0)
        a = articles.setdefault(art, {"colors": {}, "sizes": set()})
        row = a["colors"].setdefault(color, {"qty": {}, "price": price})
        row["qty"][size] = row["qty"].get(size, 0) + qty
        if price and not row["price"]:
            row["price"] = price
        a["sizes"].add(size)

    article_list = []
    grand_qty = 0
    grand_amt = 0.0
    for art, a in articles.items():
        sizes = sorted(a["sizes"], key=_size_rank)
        rows = []
        for color, cd in a["colors"].items():
            total = sum(cd["qty"].values())
            unit = round(float(cd["price"]), 4)
            amount = round(total * unit, 2)
            rows.append({"color": color, "qty": cd["qty"], "total": total,
                         "unitPrice": unit, "amount": amount})
        sub_qty = sum(r["total"] for r in rows)
        sub_amt = round(sum(r["amount"] for r in rows), 2)
        grand_qty += sub_qty
        grand_amt += sub_amt
        article_list.append({
            "articleNo": "", "style": art, "description": art, "fabric": "", "hsCode": "",
            "image": images.get(art) or None, "sizes": sizes, "rows": rows,
            "subtotalQty": sub_qty, "subtotalAmount": sub_amt,
        })

    grand_amt = round(grand_amt, 2)
    today = datetime.date.today().isoformat()
    # Document presentation: our business is the letterhead (top-left) card; the
    # supplier is shown as the counterparty in the right-hand box.
    biz = repo.tenant_name(session) or ""
    supplier_name = supplier.name if supplier else po.supplier_name
    return {
        "invoiceNo": "", "invoiceDate": today, "poNo": po.po_no or "",
        "orderDate": "", "deliveryDate": po.expected or "", "contact": "",
        "incoterms": "FOB Karachi", "origin": "Pakistan", "currency": currency,
        "terms": "T/T · L/C", "portLoading": "Karachi", "portDischarge": "",
        # Left letterhead card = our business.
        "exporter": {
            "name": biz, "address": "", "country": "Pakistan",
            "tel": "", "email": "", "taxId": "",
        },
        # Right counterparty box = the supplier.
        "buyer": {
            "name": supplier_name,
            "vat": "",
            "country": (supplier.country_code if supplier else "") or "",
            "tel": (supplier.phone if supplier else "") or "",
            "email": (supplier.email if supplier else "") or "",
        },
        "bank": {"beneficiary": biz, "bank": "", "branch": "",
                 "account": "", "swift": "", "corresp": ""},
        "articles": article_list,
        "totals": {
            "totalQty": grand_qty, "subtotal": grand_amt, "freight": 0.0,
            "total": grand_amt, "amountInWords": amount_in_words(grand_amt, currency),
        },
        "preparedBy": "",
    }


def _invoice_summary(inv) -> dict:
    return {
        "publicId": str(inv.public_id), "invoiceNo": inv.invoice_no, "poNo": inv.po_no,
        "supplier": inv.supplier_name, "currency": inv.currency_code,
        "total": float(inv.total or 0), "status": inv.status,
        "createdAt": inv.created_at.isoformat() + "Z" if inv.created_at else None,
    }


def list_invoices(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_po_invoices(session, limit=limit, offset=offset)
    return {"invoices": [{
        "publicId": str(r["public_id"]), "invoiceNo": r["invoice_no"], "poNo": r["po_no"],
        "supplier": r["supplier_name"], "currency": r["currency_code"],
        "total": float(r["total"] or 0), "status": r["status"],
        "createdAt": r["created_at"].isoformat() + "Z" if r["created_at"] else None,
    } for r in rows], "total": total}


def _persist_fields(data: dict) -> dict:
    totals = data.get("totals") or {}
    return {
        "invoice_no": (data.get("invoiceNo") or "").strip() or None,
        "po_no": data.get("poNo"),
        "supplier_name": (data.get("exporter") or {}).get("name"),
        "currency_code": (data.get("currency") or "USD")[:3],
        "total": float(totals.get("total") or 0),
    }


def create_invoice(session: Session, *, tenant_id, data: dict) -> dict:
    f = _persist_fields(data)
    inv = repo.create_po_invoice(session, tenant_id=_tid(tenant_id), data=json.dumps(data), **f)
    return {"publicId": str(inv.public_id), "invoiceNo": inv.invoice_no}


def get_invoice(session: Session, *, public_id: str) -> dict | None:
    inv = repo.get_po_invoice(session, public_id=public_id)
    if inv is None:
        return None
    return {**_invoice_summary(inv), "data": json.loads(inv.data)}


def update_invoice(session: Session, *, public_id: str, data: dict) -> dict | None:
    inv = repo.get_po_invoice(session, public_id=public_id)
    if inv is None:
        return None
    f = _persist_fields(data)
    inv.invoice_no = f["invoice_no"] or inv.invoice_no
    inv.po_no = f["po_no"]
    inv.supplier_name = f["supplier_name"]
    inv.currency_code = f["currency_code"]
    inv.total = f["total"]
    inv.data = json.dumps(data)
    session.flush()
    return {"publicId": str(inv.public_id), "invoiceNo": inv.invoice_no}

"""Dashboard live data.

Each dashboard returns real, domain-specific values keyed for the frontend
merge (see lib/dashboard-merge.ts): positional `metricsList`, a full `donut`,
optional `bars`, a `table` (title/cols/rows) and per-module `alerts` (which
replace the old "Recent activity" panel). Layout/icons/colours stay in the mock.
Where a module has no time series (inventory/production) `bars` is omitted and
the mock chart shows through.
"""
import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import Account, SupplierBill as APBill
from app.models.inventory import Location, ReorderAlert, StockLevel, StockTransfer
from app.models.procurement import PurchaseOrder
from app.models.production import ProductionOrder
from app.models.quality import Inspection
from app.models.sales import Invoice as ARInvoice, SalesOrder, SalesOrderLine, SalesReturn

# --------------------------------------------------------------------------- #
# Formatting + small helpers
# --------------------------------------------------------------------------- #
def _eurc(v) -> str:
    n = float(v or 0)
    if abs(n) >= 1_000_000:
        return f"€{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"€{n / 1_000:.0f}K"
    return f"€{n:,.0f}"


_PALETTE = ["#262B3F", "#5B6478", "#8A6D3B", "#4A6B5D", "#6E5B7B", "#C2511A"]


def _accent(i: int) -> str:
    return _PALETTE[i % len(_PALETTE)]


_TONE = {
    "red":   ("#FBECEA", "#C0392B", "alert-triangle"),
    "amber": ("#FCEADF", "#C2511A", "alert-circle"),
    "green": ("#EAF5EF", "#1F7A53", "check-circle-2"),
    "navy":  ("#E7E9F0", "#3A4256", "info"),
}


def _alert(tone: str, text: str, time: str = "", icon: str | None = None) -> dict:
    bg, color, dicon = _TONE.get(tone, _TONE["navy"])
    return {"icon": icon or dicon, "bg": bg, "color": color, "text": text, "time": time}


def _ok(text: str) -> list[dict]:
    return [_alert("green", text, "all clear")]


def _delta(cur: float, prev: float) -> tuple[str, bool]:
    """Percent change vs the previous period; ('', True) when not meaningful."""
    if prev <= 0:
        return ("", cur >= 0)
    pct = round((cur - prev) / prev * 100)
    return (f"{pct:+d}%", cur >= prev)


def _bal(session: Session, code: str) -> float:
    v = session.execute(select(Account.balance).where(Account.code == code)).scalar_one_or_none()
    return float(v or 0)


def _sum_type(session: Session, acct_type: str) -> float:
    v = session.execute(
        select(func.coalesce(func.sum(Account.balance), 0))
        .where(Account.acct_type == acct_type, Account.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return float(v or 0)


def _monthly_revenue(session: Session):
    """(bars 0-100 for the last 12 months, [raw monthly totals])."""
    rows = session.execute(
        select(func.year(SalesOrder.order_date), func.month(SalesOrder.order_date),
               func.coalesce(func.sum(SalesOrder.total), 0))
        .where(SalesOrder.is_deleted == False, SalesOrder.order_date.isnot(None))  # noqa: E712
        .group_by(func.year(SalesOrder.order_date), func.month(SalesOrder.order_date))
    ).all()
    data = {(int(y), int(m)): float(t) for y, m, t in rows}
    today = datetime.date.today()
    seq = []
    for i in range(11, -1, -1):
        mm, yy = today.month - i, today.year
        while mm <= 0:
            mm += 12
            yy -= 1
        seq.append((yy, mm))
    totals = [data.get(ym, 0.0) for ym in seq]
    mx = max(totals) or 1.0
    bars = [round(t / mx * 100) for t in totals]
    return bars, totals


def _channel_donut(session: Session):
    rows = session.execute(
        select(SalesOrder.channel, func.count()).where(SalesOrder.is_deleted == False)  # noqa: E712
        .group_by(SalesOrder.channel)
    ).all()
    counts = {ch: c for ch, c in rows}
    total = sum(counts.values())
    labels = [("Wholesale", "Wholesale"), ("Online", "Online (DTC)"),
              ("Marketplace", "Marketplace"), ("Retail", "Retail stores")]
    donut = []
    for key, label in labels:
        c = counts.get(key, 0)
        pct = round(c / total * 100) if total else 0
        donut.append({"label": label, "value": f"{pct}%", "pct": pct})
    return donut, total


def _top_styles(session: Session, limit: int = 5):
    rows = session.execute(
        select(SalesOrderLine.name,
               func.coalesce(func.sum(SalesOrderLine.qty), 0),
               func.coalesce(func.sum(SalesOrderLine.line_total), 0))
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id)
        .where(SalesOrder.is_deleted == False)  # noqa: E712
        .group_by(SalesOrderLine.name)
        .order_by(func.sum(SalesOrderLine.line_total).desc())
        .limit(limit)
    ).all()
    return [[name or "—", "—", f"{int(units):,}", _eurc(rev), _accent(i)]
            for i, (name, units, rev) in enumerate(rows)]


def _age_bucket(due, today) -> str:
    if not due:
        return "Current"
    days = (today - due).days
    if days <= 0:
        return "Current"
    if days <= 30:
        return "1–30"
    if days <= 60:
        return "31–60"
    if days <= 90:
        return "61–90"
    return "90+"


# --------------------------------------------------------------------------- #
# Operations (overview)
# --------------------------------------------------------------------------- #
def overview(session: Session) -> dict:
    bars, totals = _monthly_revenue(session)
    revenue = sum(totals)
    d_val, d_up = _delta(totals[-1], totals[-2]) if len(totals) >= 2 else ("", True)

    open_orders = session.execute(
        select(func.count()).select_from(SalesOrder)
        .where(SalesOrder.is_deleted == False,  # noqa: E712
               SalesOrder.status.not_in(["Shipped", "Cancelled"]))
    ).scalar_one()
    units_shipped = session.execute(
        select(func.coalesce(func.sum(SalesOrder.item_count), 0))
        .where(SalesOrder.is_deleted == False, SalesOrder.status == "Shipped")  # noqa: E712
    ).scalar_one()
    total_stock = session.execute(select(func.count()).select_from(StockLevel)).scalar_one()
    out_stock = session.execute(
        select(func.count()).select_from(StockLevel)
        .where((StockLevel.on_hand - StockLevel.reserved) <= 0)
    ).scalar_one()
    health = 100 if not total_stock else round((1 - out_stock / total_stock) * 100)

    donut, total_orders = _channel_donut(session)

    # Cross-module alerts
    pending_pos = session.execute(
        select(func.count()).select_from(PurchaseOrder)
        .where(PurchaseOrder.is_deleted == False, PurchaseOrder.status == "Pending approval")  # noqa: E712
    ).scalar_one()
    overdue_ar = session.execute(
        select(func.count()).select_from(ARInvoice)
        .where(ARInvoice.is_deleted == False, ARInvoice.status == "Overdue")  # noqa: E712
    ).scalar_one()
    failed_qc = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Fail")  # noqa: E712
    ).scalar_one()
    reorder = session.execute(
        select(func.count()).select_from(ReorderAlert).where(ReorderAlert.is_deleted == False)  # noqa: E712
    ).scalar_one()
    alerts = []
    if out_stock:
        alerts.append(_alert("red", f"{out_stock} variant(s) out of stock", "inventory"))
    if pending_pos:
        alerts.append(_alert("amber", f"{pending_pos} purchase order(s) awaiting approval", "procurement"))
    if overdue_ar:
        alerts.append(_alert("red", f"{overdue_ar} invoice(s) overdue", "finance"))
    if failed_qc:
        alerts.append(_alert("amber", f"{failed_qc} lot(s) failed inspection", "quality"))
    if reorder:
        alerts.append(_alert("amber", f"{reorder} item(s) below reorder point", "inventory"))
    if not alerts:
        alerts = _ok("No operational issues right now")

    return {
        "metricsList": [
            {"label": "Net revenue", "value": _eurc(revenue), "delta": d_val, "up": d_up},
            {"label": "Open orders", "value": f"{open_orders:,}"},
            {"label": "Units shipped", "value": f"{units_shipped:,}"},
            {"label": "Stock health", "value": f"{health}%"},
        ],
        "bars": bars,
        "donut": donut, "donutTotal": f"{total_orders:,}",
        "table": {"rows": _top_styles(session)},
        "alerts": {"title": "Operations alerts", "items": alerts[:6]},
    }


# --------------------------------------------------------------------------- #
# Sales
# --------------------------------------------------------------------------- #
def salesdash(session: Session) -> dict:
    bars, totals = _monthly_revenue(session)
    revenue = sum(totals)
    d_val, d_up = _delta(totals[-1], totals[-2]) if len(totals) >= 2 else ("", True)

    orders = session.execute(
        select(func.count()).select_from(SalesOrder).where(SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one()
    returns = session.execute(
        select(func.count()).select_from(SalesReturn).where(SalesReturn.is_deleted == False)  # noqa: E712
    ).scalar_one()
    aov = revenue / orders if orders else 0
    return_rate = round(returns / orders * 100) if orders else 0
    donut, total_orders = _channel_donut(session)

    new_orders = session.execute(
        select(func.count()).select_from(SalesOrder)
        .where(SalesOrder.is_deleted == False, SalesOrder.status == "New")  # noqa: E712
    ).scalar_one()
    pending_returns = session.execute(
        select(func.count()).select_from(SalesReturn)
        .where(SalesReturn.is_deleted == False, SalesReturn.status == "Inspecting")  # noqa: E712
    ).scalar_one()
    overdue_ar = session.execute(
        select(func.count()).select_from(ARInvoice)
        .where(ARInvoice.is_deleted == False, ARInvoice.status == "Overdue")  # noqa: E712
    ).scalar_one()
    top_order = session.execute(
        select(SalesOrder.order_no, SalesOrder.total)
        .where(SalesOrder.is_deleted == False,  # noqa: E712
               SalesOrder.status.not_in(["Shipped", "Cancelled"]))
        .order_by(SalesOrder.total.desc()).limit(1)
    ).first()
    alerts = []
    if new_orders:
        alerts.append(_alert("green", f"{new_orders} new order(s) to process", "sales"))
    if pending_returns:
        alerts.append(_alert("amber", f"{pending_returns} return(s) awaiting inspection", "returns"))
    if overdue_ar:
        alerts.append(_alert("red", f"{overdue_ar} invoice(s) overdue", "finance"))
    if top_order:
        alerts.append(_alert("navy", f"Largest open order {top_order[0] or '—'} · {_eurc(top_order[1])}", "sales"))
    if not alerts:
        alerts = _ok("No sales issues right now")

    return {
        "metricsList": [
            {"label": "Net sales", "value": _eurc(revenue), "delta": d_val, "up": d_up},
            {"label": "Orders", "value": f"{orders:,}"},
            {"label": "Avg order value", "value": _eurc(aov)},
            {"label": "Return rate", "value": f"{return_rate}%"},
        ],
        "bars": bars,
        "donut": donut, "donutTotal": f"{total_orders:,}",
        "table": {"rows": _top_styles(session)},
        "alerts": {"title": "Sales alerts", "items": alerts[:6]},
    }


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
def invdash(session: Session) -> dict:
    on_hand = session.execute(select(func.coalesce(func.sum(StockLevel.on_hand), 0))).scalar_one()
    available = session.execute(
        select(func.coalesce(func.sum(StockLevel.on_hand - StockLevel.reserved), 0))
    ).scalar_one()
    out_stock = session.execute(
        select(func.count()).select_from(StockLevel).where((StockLevel.on_hand - StockLevel.reserved) <= 0)
    ).scalar_one()
    reorder = session.execute(
        select(func.count()).select_from(ReorderAlert).where(ReorderAlert.is_deleted == False)  # noqa: E712
    ).scalar_one()

    # Donut: stock by location
    loc_rows = session.execute(
        select(Location.name, func.coalesce(func.sum(StockLevel.on_hand), 0))
        .join(StockLevel, StockLevel.location_id == Location.id)
        .where(Location.is_deleted == False)  # noqa: E712
        .group_by(Location.name)
        .order_by(func.sum(StockLevel.on_hand).desc()).limit(5)
    ).all()
    loc_total = sum(int(u) for _, u in loc_rows)
    donut = [{"label": n or "—",
              "value": f"{round(int(u) / loc_total * 100) if loc_total else 0}%",
              "pct": round(int(u) / loc_total * 100) if loc_total else 0} for n, u in loc_rows]

    # Table: low-stock items
    lows = session.execute(
        select(ReorderAlert.sku, ReorderAlert.available, ReorderAlert.reorder_point,
               ReorderAlert.suggested, ReorderAlert.severity)
        .where(ReorderAlert.is_deleted == False)  # noqa: E712
        .order_by(ReorderAlert.available.asc()).limit(6)
    ).all()
    rows = [[sku, f"{avail:,}", f"{rp:,}", f"{sug:,}",
             _TONE["red"][1] if (sev or "").lower() in ("high", "critical") else _accent(i)]
            for i, (sku, avail, rp, sug, sev) in enumerate(lows)]

    # Alerts
    transfers = session.execute(
        select(func.count()).select_from(StockTransfer)
        .where(StockTransfer.is_deleted == False, StockTransfer.status == "In transit")  # noqa: E712
    ).scalar_one()
    alerts = []
    for sku, avail, rp, _sug, sev in lows[:4]:
        tone = "red" if (sev or "").lower() in ("high", "critical") else "amber"
        alerts.append(_alert(tone, f"{sku} low · {avail}/{rp} available", sev or "reorder"))
    if out_stock:
        alerts.insert(0, _alert("red", f"{out_stock} variant(s) out of stock", "critical"))
    if transfers:
        alerts.append(_alert("navy", f"{transfers} stock transfer(s) in transit", "logistics"))
    if not alerts:
        alerts = _ok("Stock levels healthy")

    return {
        "metricsList": [
            {"label": "Units on hand", "value": f"{int(on_hand):,}"},
            {"label": "Available", "value": f"{int(available):,}"},
            {"label": "Out of stock", "value": f"{out_stock:,}"},
            {"label": "Reorder alerts", "value": f"{reorder:,}"},
        ],
        "donut": donut, "donutTotal": f"{loc_total:,}", "donutTitle": "Stock by location",
        "table": {"title": "Low-stock items",
                  "cols": [{"l": "SKU", "a": "left"}, {"l": "Available", "a": "right"},
                           {"l": "Reorder pt", "a": "right"}, {"l": "Suggested", "a": "right"}],
                  "rows": rows},
        "alerts": {"title": "Inventory alerts", "items": alerts[:6]},
    }


# --------------------------------------------------------------------------- #
# Production
# --------------------------------------------------------------------------- #
def proddash(session: Session) -> dict:
    active = (ProductionOrder.is_deleted == False, ProductionOrder.progress < 100)  # noqa: E712
    open_wo = session.execute(select(func.count()).select_from(ProductionOrder).where(*active)).scalar_one()
    units_wip = session.execute(
        select(func.coalesce(func.sum(ProductionOrder.qty), 0)).where(*active)
    ).scalar_one()
    avg_progress = session.execute(
        select(func.coalesce(func.avg(ProductionOrder.progress), 0))
        .where(ProductionOrder.is_deleted == False)  # noqa: E712
    ).scalar_one()
    completed = session.execute(
        select(func.count()).select_from(ProductionOrder)
        .where(ProductionOrder.is_deleted == False, ProductionOrder.progress >= 100)  # noqa: E712
    ).scalar_one()

    # Donut: work orders by stage
    stage_rows = session.execute(
        select(ProductionOrder.stage, func.count()).where(ProductionOrder.is_deleted == False)  # noqa: E712
        .group_by(ProductionOrder.stage)
    ).all()
    stage_total = sum(c for _, c in stage_rows)
    donut = [{"label": s or "—",
              "value": f"{round(c / stage_total * 100) if stage_total else 0}%",
              "pct": round(c / stage_total * 100) if stage_total else 0} for s, c in stage_rows]

    # Table: work orders in progress
    wos = session.execute(
        select(ProductionOrder.order_no, ProductionOrder.style, ProductionOrder.qty,
               ProductionOrder.stage, ProductionOrder.progress)
        .where(ProductionOrder.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrder.progress.asc()).limit(6)
    ).all()
    rows = [[ono or "—", style or "—", f"{qty:,}", f"{stage or '—'} · {prog}%", _accent(i)]
            for i, (ono, style, qty, stage, prog) in enumerate(wos)]

    alerts = []
    for ono, style, _qty, _stage, prog in wos[:4]:
        if prog >= 100:
            continue
        tone = "amber" if prog < 50 else "navy"
        alerts.append(_alert(tone, f"{ono or 'WO'} · {style or 'style'} at {prog}%", "in progress"))
    if completed:
        alerts.append(_alert("green", f"{completed} work order(s) completed", "done"))
    if not alerts:
        alerts = _ok("No production orders in progress")

    return {
        "metricsList": [
            {"label": "Open work orders", "value": f"{open_wo:,}"},
            {"label": "Units in production", "value": f"{int(units_wip):,}"},
            {"label": "Avg progress", "value": f"{round(float(avg_progress))}%"},
            {"label": "Completed", "value": f"{completed:,}"},
        ],
        "donut": donut, "donutTotal": f"{stage_total:,}", "donutTitle": "Work orders by stage",
        "table": {"title": "Work orders in progress",
                  "cols": [{"l": "Order", "a": "left"}, {"l": "Style", "a": "left"},
                           {"l": "Qty", "a": "right"}, {"l": "Stage", "a": "right"}],
                  "rows": rows},
        "alerts": {"title": "Production alerts", "items": alerts[:6]},
    }


# --------------------------------------------------------------------------- #
# Finance
# --------------------------------------------------------------------------- #
def findash(session: Session) -> dict:
    bars, _totals = _monthly_revenue(session)
    cash = _bal(session, "1000")
    ar = _bal(session, "1100")
    ap = _bal(session, "2000")
    revenue = _bal(session, "4000")

    # Donut: top asset accounts (cash & assets)
    asset_rows = session.execute(
        select(Account.name, Account.balance)
        .where(Account.acct_type == "Asset", Account.is_deleted == False)  # noqa: E712
        .order_by(Account.balance.desc()).limit(4)
    ).all()
    assets_total = sum(float(b or 0) for _, b in asset_rows) or 0.0
    donut = [{"label": n,
              "value": f"{round(float(b or 0) / assets_total * 100) if assets_total else 0}%",
              "pct": round(float(b or 0) / assets_total * 100) if assets_total else 0}
             for n, b in asset_rows]

    # Table: largest open balances (AR + AP)
    today = datetime.date.today()
    ar_rows = session.execute(
        select(ARInvoice.customer_name, ARInvoice.amount, ARInvoice.due_on)
        .where(ARInvoice.is_deleted == False, ARInvoice.status.in_(["Open", "Overdue"]))  # noqa: E712
    ).all()
    ap_rows = session.execute(
        select(APBill.supplier_name, APBill.amount, APBill.due_on)
        .where(APBill.is_deleted == False, APBill.status == "Open")  # noqa: E712
    ).all()
    combined = ([(n, "Receivable", d, float(a or 0)) for n, a, d in ar_rows]
                + [(n, "Payable", d, float(a or 0)) for n, a, d in ap_rows])
    combined.sort(key=lambda r: r[3], reverse=True)
    rows = [[n or "—", kind, _age_bucket(d, today), _eurc(a),
             _TONE["red"][1] if kind == "Payable" else _accent(i)]
            for i, (n, kind, d, a) in enumerate(combined[:6])]

    # Alerts
    overdue_ar = session.execute(
        select(func.count()).select_from(ARInvoice)
        .where(ARInvoice.is_deleted == False, ARInvoice.status == "Overdue")  # noqa: E712
    ).scalar_one()
    soon = today + datetime.timedelta(days=7)
    due_ap = session.execute(
        select(func.count()).select_from(APBill)
        .where(APBill.is_deleted == False, APBill.status == "Open",  # noqa: E712
               APBill.due_on.isnot(None), APBill.due_on <= soon)
    ).scalar_one()
    alerts = []
    if overdue_ar:
        alerts.append(_alert("red", f"{overdue_ar} receivable(s) overdue · {_eurc(ar)} outstanding", "collections"))
    if due_ap:
        alerts.append(_alert("amber", f"{due_ap} payable(s) due within 7 days", "payables"))
    if cash < 0:
        alerts.append(_alert("red", "Cash position is negative", "cash"))
    else:
        alerts.append(_alert("navy", f"Cash position {_eurc(cash)}", "treasury"))
    if not alerts:
        alerts = _ok("Finances on track")

    return {
        "metricsList": [
            {"label": "Cash position", "value": _eurc(cash)},
            {"label": "Accounts receivable", "value": _eurc(ar)},
            {"label": "Accounts payable", "value": _eurc(ap)},
            {"label": "Revenue vs target", "value": _eurc(revenue)},
        ],
        "bars": bars,
        "donut": donut, "donutTotal": _eurc(assets_total), "donutTitle": "Cash & assets",
        "table": {"rows": rows},
        "alerts": {"title": "Finance alerts", "items": alerts[:6]},
    }


BUILDERS = {
    "overview": overview, "salesdash": salesdash, "invdash": invdash,
    "proddash": proddash, "findash": findash,
}


def overrides(session: Session, key: str) -> dict:
    fn = BUILDERS.get(key)
    return fn(session) if fn else {}

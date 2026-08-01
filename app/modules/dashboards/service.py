"""Dashboard KPI overrides.

Returns only *values* keyed by the KPI label / donut segment label. The frontend
merges these onto its mock dashboard config, so every tile, chart, table and the
overall layout stay exactly as designed — we just substitute real numbers.
Dashboards whose source module isn't built yet return {} (mock renders untouched).
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import Account
from app.models.inventory import StockLevel
from app.models.quality import Inspection
from app.models.sales import SalesOrder


def _eurc(v) -> str:
    n = float(v or 0)
    if abs(n) >= 1_000_000:
        return f"€{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"€{n / 1_000:.0f}K"
    return f"€{n:,.0f}"


def _bal(session: Session, code: str) -> float:
    v = session.execute(select(Account.balance).where(Account.code == code)).scalar_one_or_none()
    return float(v or 0)


def _sum_type(session: Session, acct_type: str) -> float:
    v = session.execute(
        select(func.coalesce(func.sum(Account.balance), 0))
        .where(Account.acct_type == acct_type, Account.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return float(v or 0)


def core(session: Session) -> dict:
    """Operations / Sales / Inventory / Production tiles + channel donut."""
    revenue = session.execute(
        select(func.coalesce(func.sum(SalesOrder.total), 0))
        .where(SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one()
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

    chan_rows = session.execute(
        select(SalesOrder.channel, func.count()).where(SalesOrder.is_deleted == False)  # noqa: E712
        .group_by(SalesOrder.channel)
    ).all()
    total_orders = sum(c for _, c in chan_rows)
    chan_label = {"Wholesale": "Wholesale", "Online": "Online (DTC)",
                  "Marketplace": "Marketplace", "Retail": "Retail stores"}
    donut = []
    for ch, label in chan_label.items():
        cnt = next((c for k, c in chan_rows if k == ch), 0)
        pct = round(cnt / total_orders * 100) if total_orders else 0
        donut.append({"label": label, "value": f"{pct}%", "pct": pct})

    return {
        "metrics": {
            "Net revenue": {"value": _eurc(revenue)},
            "Open orders": {"value": f"{open_orders:,}"},
            "Units shipped": {"value": f"{units_shipped:,}"},
            "Stock health": {"value": f"{health}%"},
        },
        "donut": donut,
        "donutTotal": f"{total_orders:,}",
    }


def findash(session: Session) -> dict:
    return {
        "metrics": {
            "Cash position": {"value": _eurc(_bal(session, "1000"))},
            "Accounts receivable": {"value": _eurc(_bal(session, "1100"))},
            "Accounts payable": {"value": _eurc(_bal(session, "2000"))},
            "Revenue vs target": {"value": _eurc(_bal(session, "4000"))},
        },
    }


def finreports(session: Session) -> dict:
    revenue = _bal(session, "4000")
    cogs = _bal(session, "5000")
    gross = revenue - cogs
    assets = _sum_type(session, "Asset")
    liabilities = _sum_type(session, "Liability")
    seg = []
    for label, code in [("Inventory", "1200"), ("Cash", "1000"), ("Receivables", "1100")]:
        bal = _bal(session, code)
        pct = round(bal / assets * 100) if assets else 0
        seg.append({"label": label, "value": _eurc(bal), "pct": pct})
    return {
        "metrics": {
            "Gross profit": {"value": _eurc(gross)},
            "Operating income": {"value": _eurc(gross)},
            "Total assets": {"value": _eurc(assets)},
            "Total liabilities": {"value": _eurc(liabilities)},
        },
        "donut": seg,
    }


def qscores(session: Session) -> dict:
    total = session.execute(
        select(func.count()).select_from(Inspection).where(Inspection.is_deleted == False)  # noqa: E712
    ).scalar_one()
    passed = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Pass")  # noqa: E712
    ).scalar_one()
    failed = session.execute(
        select(func.count()).select_from(Inspection)
        .where(Inspection.is_deleted == False, Inspection.result == "Fail")  # noqa: E712
    ).scalar_one()
    pass_rate = round(passed / total * 100) if total else 0
    fail_rate = round(failed / total * 100) if total else 0
    return {
        "metrics": {
            "Pass rate": {"value": f"{pass_rate}%"},
            "Defect rate": {"value": f"{fail_rate}%"},
            "Failed lots": {"value": f"{failed}"},
            "Inspections": {"value": f"{total}"},
        },
    }


BUILDERS = {
    "overview": core, "salesdash": core, "invdash": core, "proddash": core,
    "findash": findash, "finreports": finreports, "qscores": qscores,
}


def overrides(session: Session, key: str) -> dict:
    fn = BUILDERS.get(key)
    return fn(session) if fn else {}

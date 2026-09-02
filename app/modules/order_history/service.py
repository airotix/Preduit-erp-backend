"""Order History business logic — a read-only cross-module drill-down.

Rather than re-deriving each module's view of an order, this composes the
*existing* detail-building functions from Sales, Production, Quality and
Shipments — the same functions their own detail pages already call — keyed
off the one sales order. Nothing here duplicates that logic.
"""
from sqlalchemy.orm import Session

from app.modules.order_history import repository as repo
from app.modules.production import service as production_service
from app.modules.quality import service as quality_service
from app.modules.sales import service as sales_service
from app.modules.shipments import service as shipments_service
from app.presenters.screen import list_config, text_cell

_STATUS_TONE = {"New": "gray", "Picking": "amber", "Packed": "navy",
                "Shipped": "green", "Cancelled": "red"}


def orders_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_shipped_orders(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        amount = f"€{r['total']:,.0f}" if r["total"] is not None else "—"
        grid.append([
            text_cell(f"#{r['order_no']}" if r["order_no"] else "—", strong=True, mono=True),
            text_cell(r["customer_name"], avatar=True, sub=r["channel"]),
            text_cell(r["channel"], badge="neutral"),
            r["order_date"].strftime("%d %b %Y") if r["order_date"] else "—",
            text_cell(amount, align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_STATUS_TONE.get(r["status"], "neutral")),
        ])
    return list_config(
        columns=[
            {"label": "Order"}, {"label": "Customer"}, {"label": "Channel"},
            {"label": "Placed"}, {"label": "Total", "align": "right"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search order history…", filters=["Channel", "Status"],
    )


def order_history_detail(session: Session, *, public_id: str) -> dict | None:
    order = repo.order_by_public(session, public_id=public_id)
    if order is None:
        return None

    # Sales — reuse the order's own detail builder wholesale (lines, party,
    # fulfillment timeline, and the commercial invoices already joined by
    # order_no — that becomes the Finance tab's data too).
    order_tab = sales_service.order_detail(session, public_id=public_id)
    invoices = (order_tab or {}).get("doc", {}).get("orderInvoices", [])

    # Production — same payload the Production Order detail page renders.
    po = repo.production_order_for_sales_order(session, sales_order_id=order.id)
    production_tab = (
        production_service.porder_detail(session, public_id=str(po.public_id))
        if po is not None else None
    )

    # Inspections and shipments are keyed to the PRODUCTION order number (that's
    # the order_ref carried through the QC → shipment chain), so resolve by the
    # production order's number first, falling back to the sales order number.
    prod_ref = po.order_no if (po is not None and po.order_no) else None

    # Quality — same payload the Inspection detail page renders.
    ins = (repo.inspection_for_order(session, order_no=prod_ref)
           or repo.inspection_for_order(session, order_no=order.order_no))
    quality_tab = (
        quality_service.inspection_detail(session, public_id=str(ins.public_id))
        if ins is not None else None
    )

    # Shipment — same payload the Shipment detail page renders (incl. the
    # Label created → In transit → Customs → Out for delivery → Delivered
    # tracking timeline).
    ship = (repo.shipment_for_order(session, order_no=prod_ref)
            or repo.shipment_for_order(session, order_no=order.order_no))
    shipment_tab = (
        shipments_service.shipment_detail(session, public_id=str(ship.public_id))
        if ship is not None else None
    )

    return {
        "orderNo": order.order_no or "—",
        "customer": order.customer_name,
        "channel": order.channel,
        "status": order.status,
        "statusTone": _STATUS_TONE.get(order.status, "neutral"),
        "orderDate": order.order_date.strftime("%d %b %Y") if order.order_date else "—",
        "total": f"€{float(order.total or 0):,.2f}",
        "tabs": ["Order", "Production", "Quality", "Shipment", "Finance"],
        "order": order_tab,
        "production": production_tab,
        "quality": quality_tab,
        "shipment": shipment_tab,
        "finance": {"invoices": invoices},
    }

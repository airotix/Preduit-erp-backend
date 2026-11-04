"""Order History business logic — a read-only cross-module drill-down.

Rather than re-deriving each module's view of an order, this composes the
*existing* detail-building functions from Sales, Production, Quality and
Shipments — the same functions their own detail pages already call — keyed
off the one sales order, or off the purchase order when no sales order sits
behind it. Procurement, receiving and a creation-to-receipt timeline are added
on top. Nothing here duplicates that logic.
"""
import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.batches import BatchLine, InboundAllocation
from app.models.inventory import Location
from app.models.procurement import GoodsReceiptLine, PurchaseOrderLine
from app.modules.order_history import repository as repo
from app.modules.production import service as production_service
from app.modules.quality import service as quality_service
from app.modules.sales import service as sales_service
from app.modules.shipments import service as shipments_service
from app.presenters.screen import list_config, text_cell

_STATUS_TONE = {"New": "gray", "Picking": "amber", "Packed": "navy",
                "Shipped": "green", "Cancelled": "red",
                "Received": "green", "Partially received": "amber"}
_RESULT_TONE = {"Pass": "green", "Fail": "red"}


def _money(value, currency: str = "EUR") -> str:
    return f"€{float(value or 0):,.2f}"


def _day(value) -> str:
    if isinstance(value, datetime.datetime):
        return value.strftime("%d %b %Y, %H:%M")
    return value.strftime("%d %b %Y") if isinstance(value, datetime.date) else "—"


def orders_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_entries(session, limit=limit, offset=offset)
    grid = []
    for r in rows:
        amount = f"€{r['total']:,.0f}" if r["total"] is not None else "—"
        ref = r["order_no"]
        grid.append([
            text_cell((f"#{ref}" if r["kind"] == "sales" else ref) if ref else "—", strong=True, mono=True),
            text_cell(r["customer_name"], avatar=True, sub=r["channel"]),
            text_cell(r["channel"], badge="neutral"),
            r["order_date"].strftime("%d %b %Y") if r["order_date"] else "—",
            text_cell(amount, align="right", mono=True, strong=True),
            text_cell(r["status"], badge=_STATUS_TONE.get(r["status"], "neutral")),
        ])
    return list_config(
        columns=[
            {"label": "Order"}, {"label": "Customer / Supplier"}, {"label": "Type"},
            {"label": "Date"}, {"label": "Total", "align": "right"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"status": r["status"]} for r in rows],
        search="Search order history…", filters=["Channel", "Status"],
    )


def _event(title: str, when: str, tone: str = "navy", icon: str = "check-circle-2", done: bool = True) -> dict:
    return {"icon": icon, "tone": tone, "title": title, "time": when or "—", "done": done}


def _procurement(session: Session, pos) -> dict:
    orders = []
    for po in pos:
        lines = session.scalars(select(PurchaseOrderLine).where(
            PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.tenant_id == po.tenant_id)
            .order_by(PurchaseOrderLine.id)).all()
        orders.append({
            "publicId": str(po.public_id), "poNo": po.po_no or "—", "supplier": po.supplier_name,
            "status": po.status, "statusTone": _STATUS_TONE.get(po.status, "neutral"),
            "expected": po.expected or "—", "total": _money(po.total),
            "lines": [{"name": " · ".join(v for v in (l.name, l.color, l.size) if v), "sku": l.sku or "—",
                       "qty": l.qty, "price": _money(l.price), "total": _money(l.line_total)} for l in lines],
        })
    return {"orders": orders}


def _receiving(session: Session, receipts, inbound) -> dict:
    out_receipts = []
    for grn in receipts:
        lines = session.scalars(select(GoodsReceiptLine).where(
            GoodsReceiptLine.grn_id == grn.id, GoodsReceiptLine.tenant_id == grn.tenant_id)
            .order_by(GoodsReceiptLine.id)).all()
        out_receipts.append({
            "grnNo": grn.grn_no or "—", "poRef": grn.po_ref or "—", "supplier": grn.supplier_name,
            "receivedOn": _day(grn.received_on), "location": grn.location or "—",
            "reference": grn.reference or "—", "reason": grn.reason or "", "actor": grn.actor or "—",
            "status": grn.status,
            "lines": [{"name": l.name, "sku": l.sku or "—", "ordered": l.ordered, "received": l.received} for l in lines],
        })
    out_inbound = []
    for ship in inbound:
        allocations = session.scalars(select(InboundAllocation).where(
            InboundAllocation.shipment_id == ship.id, InboundAllocation.tenant_id == ship.tenant_id)
            .order_by(InboundAllocation.id)).all()
        rows = []
        for a in allocations:
            batch_line = session.get(BatchLine, a.line_id)
            source = session.get(PurchaseOrderLine, batch_line.po_line_id) if batch_line else None
            location = session.get(Location, a.location_id)
            rows.append({
                "name": " · ".join(v for v in ((source.name, source.color, source.size) if source else ()) if v) or "—",
                "location": location.name if location else "—", "shipped": a.qty, "accepted": a.accepted,
                "discrepancy": a.discrepancy_qty, "discrepancyStatus": a.discrepancy_status or "",
            })
        out_inbound.append({"shipmentNo": ship.shipment_no or "—", "status": ship.status,
                            "receivedOn": _day(ship.delivered_at), "lines": rows})
    return {"receipts": out_receipts, "inbound": out_inbound}


def order_history_detail(session: Session, *, public_id: str) -> dict | None:
    order = repo.order_by_public(session, public_id=public_id)
    if order is not None:
        pos = repo.purchase_orders_for_sales_order(session, sales_order_id=order.id)
    else:
        single = repo.purchase_order_by_public(session, public_id=public_id)
        if single is None:
            return None
        pos = [single]
    po_ids = [po.id for po in pos]
    po_nos = [po.po_no for po in pos if po.po_no]

    # Sales — reuse the order's own detail builder wholesale (lines, party,
    # fulfillment timeline, and the commercial invoices already joined by
    # order_no — that becomes the Finance tab's data too).
    order_tab = sales_service.order_detail(session, public_id=public_id) if order is not None else None
    invoices = (order_tab or {}).get("doc", {}).get("orderInvoices", [])

    # Production — same payload the Production Order detail page renders.
    work = (repo.production_order_for_sales_order(session, sales_order_id=order.id) if order is not None else None) \
        or repo.production_order_for_purchase_orders(session, po_ids=po_ids)
    production_tab = (
        production_service.porder_detail(session, public_id=str(work.public_id))
        if work is not None else None
    )

    # Inspections and shipments are keyed to the PRODUCTION order number (that's
    # the order_ref carried through the QC → shipment chain), so resolve by the
    # production order's number first, falling back to the sales order number.
    prod_ref = work.order_no if (work is not None and work.order_no) else None
    refs = [prod_ref, order.order_no if order is not None else None, *po_nos]

    inspections = repo.inspections_for_refs(session, refs=refs)
    latest = inspections[-1] if inspections else None
    quality_tab = (
        quality_service.inspection_detail(session, public_id=str(latest.public_id))
        if latest is not None else None
    )

    shipments = repo.shipments_for(session, sales_order_id=order.id if order is not None else None,
                                   po_ids=po_ids, refs=refs)
    shipments_out = []
    for ship in shipments:
        detail = shipments_service.shipment_detail(session, public_id=str(ship.public_id))
        if detail is not None:
            shipments_out.append({
                "direction": "Inbound" if ship.direction == "Inbound" else "Outbound",
                "route": f"{ship.carrier or 'Carrier not set'} → {ship.destination or 'destination not set'}",
                "dispatchOn": ship.dispatch_on or "—", "eta": ship.eta or "—",
                "detail": detail,
            })
    inbound = [s for s in shipments if s.direction == "Inbound"]
    outbound = [s for s in shipments if s.direction != "Inbound"]

    receipts = repo.receipts_for_po_nos(session, po_nos=po_nos)
    receiving = _receiving(session, receipts, inbound)
    procurement = _procurement(session, pos)

    # ---- Timeline: creation → procurement → production → QC → receiving → shipping
    events: list[dict] = []
    if order is not None:
        events.append(_event(f"Sales order #{order.order_no} placed by {order.customer_name}",
                             _day(order.order_date), "navy", "shopping-cart"))
    for po in pos:
        events.append(_event(f"Purchase order {po.po_no} raised with {po.supplier_name}",
                             f"Status: {po.status}", "navy", "file-text"))
    if production_tab is not None:
        events.append(_event(f"Production order {production_tab['ref']}",
                             f"{production_tab['statusLabel']} · {production_tab['progress']}%", "accent", "factory",
                             done=production_tab["started"]))
        stage_sets = ([(ln["name"], ln["stages"]) for ln in production_tab.get("lines", [])]
                      if production_tab.get("lines") else [("", production_tab["stages"])])
        for name, stages in stage_sets:
            for stage in stages:
                if stage["status"] in ("Completed", "Received"):
                    prefix = f"{name}: " if name else ""
                    events.append(_event(f"{prefix}{stage['name']} completed", stage["end"], "green", "check-circle-2"))
    for ins in inspections:
        when = _day(ins.finalized_at or ins.inspection_date)
        events.append(_event(f"{ins.stage} inspection {ins.inspection_no or ''} — {ins.item or 'order'}: {ins.result}".replace("  ", " "),
                             when, _RESULT_TONE.get(ins.result, "amber"), "badge-check", done=ins.result in ("Pass", "Fail")))
    for ship in inbound:
        if ship.dispatched_at:
            events.append(_event(f"Supplier shipment {ship.shipment_no} dispatched via {ship.carrier or 'carrier'}",
                                 _day(ship.dispatched_at), "accent", "truck"))
        else:
            events.append(_event(f"Supplier shipment {ship.shipment_no} prepared", ship.status, "neutral", "truck", done=False))
    for grn in receipts:
        events.append(_event(f"Warehouse receipt {grn.grn_no} accepted at {grn.location or 'warehouse'} — {grn.line_count} line(s)",
                             _day(grn.received_on), "green", "warehouse"))
    for inv in invoices:
        events.append(_event(f"Invoice {inv['invoiceNo']} issued ({inv['total']})", inv["createdAt"], "navy", "receipt"))
    for ship in outbound:
        if ship.packed_at:
            events.append(_event(f"Shipment {ship.shipment_no} packed", _day(ship.packed_at), "accent", "package"))
        if ship.dispatched_at:
            events.append(_event(f"Shipment {ship.shipment_no} dispatched via {ship.carrier or 'carrier'}", _day(ship.dispatched_at), "accent", "truck"))
        if ship.delivered_at:
            events.append(_event(f"Shipment {ship.shipment_no} delivered", _day(ship.delivered_at), "green", "package-check"))
        if not (ship.packed_at or ship.dispatched_at or ship.delivered_at):
            events.append(_event(f"Shipment {ship.shipment_no} prepared", ship.status, "neutral", "package", done=False))

    # ---- Header and summary tiles
    ordered = sum(l["qty"] for o in procurement["orders"] for l in o["lines"]) \
        or sum(int(l.get("qty") or 0) for l in (order_tab or {}).get("doc", {}).get("lines", []))
    received = sum(l["received"] for r in receiving["receipts"] for l in r["lines"]) \
        or sum(l["accepted"] for i in receiving["inbound"] for l in i["lines"])
    summary = [
        {"k": "Pieces ordered", "v": f"{ordered:,}"},
        {"k": "Pieces received", "v": f"{received:,}"},
        {"k": "Receipts", "v": str(len(receipts))},
        {"k": "Shipments", "v": str(len(shipments_out))},
    ]

    if order is not None:
        header = {
            "kind": "sales", "orderNo": order.order_no or "—", "customer": order.customer_name,
            "channel": order.channel, "status": order.status,
            "statusTone": _STATUS_TONE.get(order.status, "neutral"),
            "orderDate": _day(order.order_date), "total": f"€{float(order.total or 0):,.2f}",
        }
        tabs = ["Timeline", "Order", "Procurement", "Production", "Quality", "Shipments", "Receiving", "Finance"]
    else:
        po = pos[0]
        header = {
            "kind": "purchase", "orderNo": po.po_no or "—", "customer": po.supplier_name,
            "channel": "Purchase order", "status": po.status,
            "statusTone": _STATUS_TONE.get(po.status, "neutral"),
            "orderDate": _day(receipts[0].received_on) if receipts else "—", "total": _money(po.total),
        }
        tabs = ["Timeline", "Procurement", "Production", "Quality", "Shipments", "Receiving"]
    if order is not None and not pos:
        tabs.remove("Procurement")

    return {
        **header,
        "tabs": tabs,
        "summary": summary,
        "timeline": events,
        "order": order_tab,
        "procurement": procurement,
        "production": production_tab,
        "quality": quality_tab,
        "shipments": shipments_out,
        "receiving": receiving,
        "finance": {"invoices": invoices},
    }

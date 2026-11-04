"""One shipment workspace for ordinary orders and production batches."""
from uuid import UUID

from sqlalchemy import select

from app.models.batches import BatchLine, BatchLot, ProductionBatch
from app.models.inventory import Location
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.production import ProductionOrder
from app.models.sales import Customer, SalesOrder
from app.models.shipments import Shipment
from app.modules.batches.workflow import ready as inbound_ready, style_complete


ACTIVE = {"Pending", "Label created", "Dispatched", "In transit", "Customs", "Out for delivery"}
DONE = {"Delivered", "Cancelled", "Returned"}


def display(ship):
    if ship.status in ("Pending", "Label created"):
        return "Prepared"
    if ship.status == "Dispatched":
        return "Dispatched"
    if ship.status in ("In transit", "Customs", "Out for delivery"):
        return "In transit"
    if ship.status == "Delivered":
        return "Received" if ship.direction == "Inbound" else "Delivered"
    return ship.status


def _party_for(session, ship):
    if ship.purchase_order_id:
        po = session.get(PurchaseOrder, ship.purchase_order_id)
        if po:
            return po.supplier_name
    if ship.sales_order_id:
        order = session.get(SalesOrder, ship.sales_order_id)
        if order and order.customer_id:
            customer = session.get(Customer, order.customer_id)
            if customer:
                return customer.name
    if ship.direction == "Inbound":
        return "Supplier"
    return "Customer"


def _match(row, direction, party, source, status):
    if direction and row["direction"] != direction:
        return False
    if party and party.lower() not in (row.get("party") or "").lower():
        return False
    if source and source.lower() not in (row.get("source") or "").lower():
        return False
    if status and status not in (row.get("label"), row.get("status")):
        return False
    return True


def _locations(session, tid):
    rows = session.scalars(select(Location).where(Location.tenant_id == tid, Location.is_deleted.is_(False))
        .order_by(Location.name)).all()
    return [{"id": loc.id, "publicId": str(loc.public_id), "name": loc.name} for loc in rows]


def _shipment_rows(session, tid):
    ships = session.scalars(select(Shipment).where(Shipment.tenant_id == tid, Shipment.is_deleted.is_(False))
        .order_by(Shipment.id.desc())).all()
    rows = []
    for ship in ships:
        label = display(ship)
        rows.append({
            "id": str(ship.public_id), "ref": ship.shipment_no or "Shipment",
            "direction": "Incoming" if ship.direction == "Inbound" else "Outgoing",
            "party": _party_for(session, ship), "source": ship.order_ref or "",
            "status": ship.status, "label": label, "tracking": ship.status if ship.status in ("Customs", "Out for delivery") else None,
            "carrier": ship.carrier or "", "destination": ship.destination or "", "eta": ship.eta or "",
            "legacy": bool(ship.legacy_ambiguous),
        })
    return rows


def _incoming_batch_sources(session, tid, upcoming):
    lines = session.scalars(select(BatchLine).where(BatchLine.tenant_id == tid)).all()
    rows = []
    for line in lines:
        batch = session.get(ProductionBatch, line.batch_id)
        if batch is None or batch.status == "Cancelled":
            continue
        work = session.get(ProductionOrder, batch.order_id)
        po = session.get(PurchaseOrder, work.purchase_order_id) if work else None
        if work is None or po is None or po.is_deleted or po.status in ("Cancelled", "Rejected", "Inactive"):
            continue
        source = session.get(PurchaseOrderLine, line.po_line_id)
        eligible = inbound_ready(session, tid, line)
        awaiting = max(0, line.completed - line.approved - line.held - line.rejected)
        if upcoming:
            if eligible:
                continue
            if not style_complete(session, tid, line):
                blocking = "Production TNA is not complete for this article."
            elif awaiting > 0:
                blocking = "Final QC has not released these pieces."
            elif line.held > 0 and line.approved <= line.received:
                blocking = "Quality is holding these pieces."
            else:
                continue
        elif not eligible:
            continue
        else:
            blocking = None
        if not upcoming and po.status not in ("Approved", "Partially received", "Received"):
            continue
        rows.append({
            "key": f"bin:{line.id}", "direction": "Incoming", "party": po.supplier_name,
            "source": f"{po.po_no} · {work.order_no}", "article": source.name if source else "Article",
            "color": source.color if source else None, "size": source.size if source else None,
            "batch": batch.name, "eligible": eligible, "blocking": blocking,
            "partial": True, "savedDestination": None,
        })
    return rows


def _incoming_ordinary(session, tid, upcoming):
    from app.modules.quality.workflow import supplier_release
    from app.modules.shipments import supplier_inbound
    from app.modules.inventory.receiving import _batch_managed
    pos = session.scalars(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tid,
        PurchaseOrder.is_deleted.is_(False),
        PurchaseOrder.status.not_in(["Cancelled", "Rejected", "Inactive"]))).all()
    rows = []
    for po in pos:
        if _batch_managed(session, po) or not po.supplier_shipment_required:
            continue
        work = session.scalar(select(ProductionOrder).where(ProductionOrder.tenant_id == tid,
            ProductionOrder.purchase_order_id == po.id, ProductionOrder.is_deleted.is_(False)))
        if work is None:
            continue
        release = supplier_release(session, po)
        existing = supplier_inbound.for_po(session, po)
        left = {row["poLineId"]: row["qty"] for row in supplier_inbound.remaining_lines(session, po, release)} if release["ready"] else {}
        if upcoming:
            if release["ready"] or existing:
                continue
            blocking = "Waiting for production TNA and final QC release."
            eligible = 0
        else:
            if not release["ready"] or not left:
                continue
            if po.status not in ("Approved", "Partially received", "Received"):
                continue
            blocking = None
        if upcoming:
            rows.append({
                "key": f"oin:{po.public_id}", "direction": "Incoming", "party": po.supplier_name,
                "source": po.po_no, "article": "Purchase order", "color": None, "size": None,
                "batch": None, "eligible": 0, "blocking": blocking, "partial": False,
                "savedDestination": None,
            })
            continue
        for line in release["lines"]:
            if line["poLineId"] not in left:
                continue
            source = session.get(PurchaseOrderLine, line["poLineId"])
            rows.append({
                "key": f"oin:{po.public_id}:{line['poLineId']}", "direction": "Incoming", "party": po.supplier_name,
                "source": po.po_no, "article": source.name if source else line["description"],
                "color": source.color if source else None, "size": source.size if source else None,
                "batch": None, "eligible": left[line["poLineId"]], "blocking": None, "partial": True,
                "savedDestination": None,
            })
    return rows


def _outgoing(session, tid, upcoming):
    from app.modules.sales import fulfillment
    from app.modules.batches.service import inventory_sources, managed_sales
    orders = session.scalars(select(SalesOrder).where(SalesOrder.tenant_id == tid,
        SalesOrder.is_deleted.is_(False), SalesOrder.status.not_in(["Cancelled", "Inactive", "Shipped"]))
        .order_by(SalesOrder.id)).all()
    rows = []
    for order in orders:
        customer = session.get(Customer, order.customer_id) if order.customer_id else None
        party = customer.name if customer else "Customer"
        saved = ((customer.address or customer.region) if customer else None) or None
        if managed_sales(session, order):
            if upcoming:
                continue
            for source in inventory_sources(session, tid, order.id):
                rows.append({
                    "key": f"sout:{source['reservationId']}", "direction": "Outgoing", "party": party,
                    "source": order.order_no, "article": source["name"], "color": source["color"],
                    "size": source["size"], "batch": f"Warehouse · {source['location']}",
                    "eligible": source["available"], "blocking": None,
                    "partial": bool(order.allow_partial_shipments), "savedDestination": saved,
                })
            lots = session.scalars(select(BatchLot).join(BatchLine, BatchLine.id == BatchLot.line_id)
                .join(PurchaseOrderLine, PurchaseOrderLine.id == BatchLine.po_line_id)
                .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
                .where(BatchLot.tenant_id == tid, PurchaseOrder.sales_order_id == order.id,
                    BatchLot.on_hand > BatchLot.allocated)).all()
            for lot in lots:
                line = session.get(BatchLine, lot.line_id)
                source = session.get(PurchaseOrderLine, line.po_line_id)
                batch = session.get(ProductionBatch, line.batch_id)
                available = lot.on_hand - lot.allocated
                if available <= 0 or batch is None or batch.status == "Cancelled":
                    continue
                location = session.get(Location, lot.location_id)
                rows.append({
                    "key": f"bout:{lot.id}", "direction": "Outgoing", "party": party,
                    "source": order.order_no, "article": source.name, "color": source.color,
                    "size": source.size, "batch": f"{batch.name} · {location.name if location else 'Warehouse'}",
                    "eligible": available, "blocking": None,
                    "partial": bool(order.allow_partial_shipments), "savedDestination": saved,
                })
            continue
        if not order.fulfillment_tracked:
            continue
        from app.modules.sales.ordinary_shipping import active
        summary = fulfillment.stock_summary(session, order)
        reserved = sum(min(row["reserved"], row["outstanding"]) for row in summary)
        missing = any(row["missing"] for row in summary)
        current = active(session, order)
        if upcoming:
            if current or reserved:
                continue
            if not missing:
                continue
            rows.append({
                "key": f"oout:{order.public_id}", "direction": "Outgoing", "party": party,
                "source": order.order_no, "article": "Customer order", "color": None, "size": None,
                "batch": None, "eligible": 0, "blocking": "Waiting for reserved warehouse stock. Existing saleable stock does not need a new production TNA.",
                "partial": bool(order.allow_partial_shipments), "savedDestination": saved,
            })
            continue
        if current or not reserved:
            continue
        if not order.allow_partial_shipments and missing:
            continue
        for row in summary:
            quantity = min(row["reserved"], row["outstanding"])
            if not quantity:
                continue
            rows.append({
                "key": f"oout:{order.public_id}:{row['lineId']}", "direction": "Outgoing", "party": party,
                "source": order.order_no, "article": row["name"], "color": row.get("color"),
                "size": row.get("size"), "batch": None, "eligible": quantity, "blocking": None,
                "partial": bool(order.allow_partial_shipments), "savedDestination": saved,
            })
    return rows


def load(session, *, tenant_id, view, direction="", party="", source="", status=""):
    tid = UUID(str(tenant_id))
    direction = {"Incoming": "Incoming", "Outgoing": "Outgoing"}.get(direction, "")
    if view == "upcoming":
        rows = _incoming_batch_sources(session, tid, True) + _incoming_ordinary(session, tid, True) + _outgoing(session, tid, True)
        rows = [row for row in rows if _match(row, direction, party, source, status)]
        return {"view": view, "sources": rows, "shipments": [], "locations": _locations(session, tid)}
    if view == "ready":
        rows = _incoming_batch_sources(session, tid, False) + _incoming_ordinary(session, tid, False) + _outgoing(session, tid, False)
        rows = [row for row in rows if _match(row, direction, party, source, status)]
        return {"view": view, "sources": rows, "shipments": [], "locations": _locations(session, tid)}
    wanted = ACTIVE if view == "active" else DONE
    rows = [row for row in _shipment_rows(session, tid) if row["status"] in wanted and _match(row, direction, party, source, status)]
    return {"view": view if view in ("active", "completed") else "active", "sources": [], "shipments": rows,
        "locations": _locations(session, tid)}

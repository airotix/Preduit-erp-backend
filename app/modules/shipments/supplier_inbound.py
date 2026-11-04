"""Supplier shipments for manually grouped purchasing; stock posts on receipt only."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.production import ProductionOrder
from app.models.shipments import Shipment, ShipmentLine

IN_FLIGHT = {"Dispatched", "In transit", "Customs", "Out for delivery"}


def _query(po):
    return select(Shipment).where(Shipment.tenant_id == po.tenant_id,
        Shipment.purchase_order_id == po.id, Shipment.batch_managed.is_(False),
        Shipment.is_deleted.is_(False), Shipment.status != "Cancelled")


def all_for_po(db, po, *, lock=False):
    """Every live supplier shipment of this PO, oldest first. A PO can ship in several parts."""
    query = _query(po).order_by(Shipment.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list(db.scalars(query))


def for_po(db, po, *, lock=False):
    """The most relevant shipment to show: the newest one still open, else the newest."""
    rows = all_for_po(db, po, lock=lock)
    open_rows = [s for s in rows if s.status != "Delivered"]
    return (open_rows or rows or [None])[-1]


def dispatched_for_po(db, po):
    return next((s for s in all_for_po(db, po) if s.dispatched_at and s.status != "Delivered"), None)


def committed(db, po) -> dict[int, int]:
    """Pieces per PO line already on a supplier shipment (a delivered shipment counts what really arrived)."""
    shipments = all_for_po(db, po)
    if not shipments:
        return {}
    done = {s.id for s in shipments if s.status == "Delivered"}
    totals: dict[int, int] = {}
    for line in db.scalars(select(ShipmentLine).where(ShipmentLine.tenant_id == po.tenant_id,
            ShipmentLine.shipment_id.in_([s.id for s in shipments]), ShipmentLine.po_line_id.is_not(None))):
        qty = (line.delivered_qty if line.delivered_qty is not None else line.qty) if line.shipment_id in done else line.qty
        totals[line.po_line_id] = totals.get(line.po_line_id, 0) + qty
    return totals


def remaining_lines(db, po, release=None) -> list[dict]:
    """QC-released pieces that are not on any shipment yet."""
    from app.modules.quality.workflow import supplier_release
    release = release or supplier_release(db, po)
    used = committed(db, po)
    rows = []
    for line in release["lines"]:
        left = line["qty"] - used.get(line["poLineId"], 0)
        if left > 0:
            rows.append({**line, "qty": left})
    return rows


def ready(db, po):
    from app.modules.sales.fulfillment import qc_cleared
    return (not po.is_deleted and po.status in ("Approved", "Partially received", "Received")
        and qc_cleared(db, po))


def ensure(db, po):
    """An existing not-yet-dispatched shipment, or a new one holding everything still unshipped."""
    pending = next((s for s in all_for_po(db, po) if not s.dispatched_at and s.status != "Delivered"), None)
    if pending:
        return pending
    return create(db, po, None)


def create(db, po, quantities):
    """Create one supplier shipment. quantities maps PO line id to pieces; None means all unshipped pieces."""
    from app.modules.inventory.receiving import _batch_managed
    if not po.supplier_shipment_required or _batch_managed(db, po):
        return None
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po.id,
        PurchaseOrder.tenant_id == po.tenant_id).with_for_update().execution_options(populate_existing=True))
    from app.modules.quality.workflow import supplier_release
    release = supplier_release(db, po)
    if not release["ready"]:
        return None
    left = {row["poLineId"]: row for row in remaining_lines(db, po, release)}
    if quantities is None:
        chosen = {key: row["qty"] for key, row in left.items()}
    else:
        chosen = {key: qty for key, qty in quantities.items() if qty > 0}
        if any(key not in left or qty > left[key]["qty"] for key, qty in chosen.items()):
            raise HTTPException(409, "Ship no more than the QC-released pieces that are not on a shipment yet.")
    if not chosen:
        return None
    shipment = Shipment(tenant_id=po.tenant_id, purchase_order_id=po.id,
        direction="Inbound", batch_managed=False, order_ref=po.po_no, status="Pending", eta=po.expected)
    db.add(shipment)
    db.flush()
    shipment.shipment_no = f"SHP-{9900 + shipment.id}"
    for key, qty in chosen.items():
        line = left[key]
        db.add(ShipmentLine(tenant_id=po.tenant_id, shipment_id=shipment.id, po_line_id=key,
            sku=line["sku"], description=line["description"], qty=qty))
    db.flush()
    return shipment


def transition(db, shipment, status, actor=None):
    from app.core.audit import write_audit
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == shipment.purchase_order_id,
        PurchaseOrder.tenant_id == shipment.tenant_id).with_for_update().execution_options(populate_existing=True))
    shipment = db.scalar(select(Shipment).where(Shipment.id == shipment.id,
        Shipment.tenant_id == shipment.tenant_id, Shipment.is_deleted.is_(False))
        .with_for_update().execution_options(populate_existing=True)) if po else None
    if shipment is None:
        raise HTTPException(409, "The supplier shipment is cancelled or its source PO is unavailable.")
    if shipment.status == "Cancelled":
        if status == "Cancelled":
            return shipment
        raise HTTPException(409, "A cancelled supplier shipment cannot be reopened.")
    if status == "Cancelled":
        if shipment.dispatched_at:
            raise HTTPException(409, "Reconcile goods already dispatched before cancelling their shipment.")
        shipment.status = status
        write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="shipment",
            entity_id=str(shipment.public_id), detail=f"{shipment.shipment_no}: supplier shipment cancelled before dispatch; {actor or 'user'}")
        db.flush()
        return shipment
    if shipment.status == "Delivered":
        if status != "Delivered":
            raise HTTPException(409, "A received supplier shipment cannot be reopened.")
        return shipment
    if not ready(db, po):
        raise HTTPException(409, "The supplier PO must be approved, its TNA complete and every article's final QC passed.")
    if status == "Delivered":
        raise HTTPException(409, "Confirm physical arrival in Inventory → Warehouse receiving. Stock and delivery are recorded together.")
    if shipment.dispatched_at and status in ("Pending", "Label created"):
        raise HTTPException(409, "Dispatched supplier goods cannot be moved back to planning.")
    if status in IN_FLIGHT:
        if not (shipment.carrier or "").strip() or not (shipment.destination or "").strip():
            raise HTTPException(409, "Save a carrier and warehouse destination before dispatching this supplier shipment.")
        if status != "Dispatched" and not shipment.dispatched_at:
            raise HTTPException(409, "Confirm Dispatched before recording transit.")
        if shipment.dispatched_at and status == "Dispatched":
            return shipment
        if not shipment.dispatched_at:
            shipment.dispatched_at = datetime.now(timezone.utc)
            write_audit(db, tenant_id=po.tenant_id, action="DISPATCH", entity_type="shipment",
                entity_id=str(shipment.public_id), detail=f"{shipment.shipment_no}: supplier dispatch for {po.po_no}; {actor or 'user'}")
    shipment.status = status
    db.flush()
    return shipment

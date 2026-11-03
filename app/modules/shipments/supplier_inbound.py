"""Supplier shipments for manually grouped purchasing; stock posts on receipt only."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.production import ProductionOrder
from app.models.shipments import Shipment, ShipmentLine

IN_FLIGHT = {"Dispatched", "In transit", "Customs", "Out for delivery"}


def for_po(db, po, *, lock=False):
    query = select(Shipment).where(Shipment.tenant_id == po.tenant_id,
        Shipment.purchase_order_id == po.id, Shipment.batch_managed.is_(False),
        Shipment.is_deleted.is_(False), Shipment.status != "Cancelled")
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def ready(db, po):
    from app.modules.sales.fulfillment import qc_cleared
    return (not po.is_deleted and po.status in ("Approved", "Partially received", "Received")
        and qc_cleared(db, po))


def ensure(db, po):
    from app.modules.inventory.receiving import _batch_managed
    if not po.supplier_shipment_required or _batch_managed(db, po):
        return None
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po.id,
        PurchaseOrder.tenant_id == po.tenant_id).with_for_update().execution_options(populate_existing=True))
    from app.modules.quality.workflow import supplier_release
    release = supplier_release(db, po)
    if not release["ready"]:
        return None
    existing = for_po(db, po)
    if existing:
        return existing
    if not release["lines"]:
        return None
    shipment = Shipment(tenant_id=po.tenant_id, purchase_order_id=po.id,
        direction="Inbound", batch_managed=False, order_ref=po.po_no, status="Pending", eta=po.expected)
    db.add(shipment)
    db.flush()
    shipment.shipment_no = f"SHP-{9900 + shipment.id}"
    for line in release["lines"]:
        db.add(ShipmentLine(tenant_id=po.tenant_id, shipment_id=shipment.id, po_line_id=line["poLineId"],
            sku=line["sku"], description=line["description"], qty=line["qty"]))
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

"""Withdraw whole documents without discarding commercial or execution history."""
from uuid import UUID, uuid4
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.core.audit import write_audit
from app.models.sales import SalesOrder, SalesOrderLine, Invoice, SalesInvoice
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, GoodsReceipt, PoInvoice
from app.models.finance import SupplierBill
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.quality import Inspection
from app.models.shipments import Shipment
from app.models.batches import ProductionBatch, BatchLine, BatchStage, BatchLot, BatchAllocation, InventoryAllocation, BatchEvent, InboundAllocation
from app.modules.sales import fulfillment


def _reason(value):
    reason = (value or "").strip()
    if len(reason) < 3 or len(reason) > 400:
        raise HTTPException(400, "Enter a reason between 3 and 400 characters.")
    return reason


def pending_shipments(db, tid, *, order_id=None, work_ids=(), lock=False):
    batch_ids = select(ProductionBatch.id).where(ProductionBatch.tenant_id == tid,
                                                ProductionBatch.order_id.in_(work_ids))
    lot_ids = select(BatchLot.id).join(BatchLine, BatchLine.id == BatchLot.line_id).where(
        BatchLot.tenant_id == tid, BatchLine.batch_id.in_(batch_ids))
    shipment_ids = select(BatchAllocation.shipment_id).where(
        BatchAllocation.tenant_id == tid, BatchAllocation.lot_id.in_(lot_ids))
    inbound_ids = select(InboundAllocation.shipment_id).join(BatchLine, BatchLine.id == InboundAllocation.line_id).where(
        InboundAllocation.tenant_id == tid, BatchLine.batch_id.in_(batch_ids))
    condition = Shipment.id.in_(shipment_ids) | Shipment.id.in_(inbound_ids)
    if order_id is not None:
        condition = condition | (Shipment.sales_order_id == order_id)
    query = select(Shipment).where(Shipment.tenant_id == tid,
        Shipment.is_deleted.is_(False), Shipment.status.not_in(["Cancelled", "Returned"]),
        condition).order_by(Shipment.id)
    return db.scalars(query.with_for_update() if lock else query).all()


def cancel_pending(db, tid, ships, reason):
    # Validate every shipment before changing any allocation.
    for ship in ships:
        if ship.status not in ("Pending", "Label created"):
            raise HTTPException(409, "Dispatched shipments must be reconciled through returns before withdrawing this document.")
        for model in (BatchAllocation, InventoryAllocation):
            if db.scalar(select(model.id).where(model.tenant_id == tid,
                    model.shipment_id == ship.id, model.status == "Dispatched").limit(1)):
                raise HTTPException(409, "Dispatched inventory cannot be withdrawn without a return.")
    for ship in ships:
        allocations = db.scalars(select(BatchAllocation).where(BatchAllocation.tenant_id == tid,
            BatchAllocation.shipment_id == ship.id, BatchAllocation.status == "Reserved").with_for_update()).all()
        for allocation in allocations:
            lot = db.scalar(select(BatchLot).where(BatchLot.id == allocation.lot_id,
                BatchLot.tenant_id == tid).with_for_update().execution_options(populate_existing=True))
            if lot.allocated < allocation.qty:
                raise HTTPException(409, "Reconcile the batch shipment allocation before withdrawal.")
            lot.allocated -= allocation.qty
            allocation.status = "Cancelled"
        for allocation in db.scalars(select(InventoryAllocation).where(InventoryAllocation.tenant_id == tid,
                InventoryAllocation.shipment_id == ship.id, InventoryAllocation.status == "Reserved").with_for_update()).all():
            allocation.status = "Cancelled"
        for allocation in db.scalars(select(InboundAllocation).where(InboundAllocation.tenant_id == tid,
                InboundAllocation.shipment_id == ship.id, InboundAllocation.status == "Reserved").with_for_update()).all():
            allocation.status = "Cancelled"
        ship.status = "Cancelled"
        write_audit(db, tenant_id=tid, action="UPDATE", entity_type="shipment",
                    entity_id=str(ship.public_id), detail=f"Cancelled pending shipment on document withdrawal: {reason}")
    db.flush()


def stop_work(db, tid, works, reason):
    for work in works:
        for stage in db.scalars(select(ProductionStage).where(ProductionStage.tenant_id == tid,
                ProductionStage.order_id == work.id, ProductionStage.is_deleted.is_(False))).all():
            if stage.status not in ("Completed", "Received", "Cancelled"):
                stage.status = "Cancelled"
                stage.notes = f"Document made inactive: {reason}"[:400]
                stage.is_deleted = True
        for ins in db.scalars(select(Inspection).where(Inspection.tenant_id == tid,
                Inspection.order_ref == work.order_no, Inspection.is_deleted.is_(False))).all():
            if ins.result not in ("Pass", "Fail", "Cancelled"):
                ins.result = "Cancelled"
                ins.disposition_notes = f"Document made inactive: {reason}"
                ins.is_deleted = True
        for batch in db.scalars(select(ProductionBatch).where(ProductionBatch.tenant_id == tid,
                ProductionBatch.order_id == work.id, ProductionBatch.status != "Cancelled").with_for_update()).all():
            batch.status = "Cancelled"
            for stage in db.scalars(select(BatchStage).where(BatchStage.tenant_id == tid,
                    BatchStage.batch_id == batch.id)).all():
                if stage.status != "Completed":
                    stage.status = "Cancelled"
                    stage.notes = f"Document made inactive: {reason}"[:400]
            db.add(BatchEvent(tenant_id=tid, batch_id=batch.id, request_id=uuid4(),
                kind="Withdraw batch", payload=json.dumps({"reason": reason}), result='{"status":"Cancelled"}'))
        work.stage = "Inactive"
        work.is_deleted = True
        for line in db.scalars(select(ProductionOrderLine).where(ProductionOrderLine.tenant_id == tid,
                ProductionOrderLine.order_id == work.id)).all():
            line.is_deleted = True
    db.flush()


def inactive_order(db, *, public_id, reason):
    reason = _reason(reason)
    order = db.scalar(select(SalesOrder).where(SalesOrder.public_id == UUID(str(public_id)),
        SalesOrder.is_deleted.is_(False)).with_for_update().execution_options(populate_existing=True))
    if order is None:
        return None
    if order.status == "Inactive":
        return order
    if order.status in ("Shipped", "Partially shipped", "Cancelled"):
        raise HTTPException(409, "A shipped or cancelled order cannot be made inactive.")
    linked_ids = select(PurchaseOrderLine.po_id).join(SalesOrderLine,
        SalesOrderLine.id == PurchaseOrderLine.sales_order_line_id).where(
            SalesOrderLine.tenant_id == order.tenant_id, SalesOrderLine.order_id == order.id)
    if db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.tenant_id == order.tenant_id,
            PurchaseOrder.is_deleted.is_(False), PurchaseOrder.status.not_in(["Received", "Rejected", "Cancelled", "Inactive"]),
            (PurchaseOrder.sales_order_id == order.id) | PurchaseOrder.id.in_(linked_ids)).limit(1)):
        raise HTTPException(409, "Resolve this order's active purchase orders before marking it inactive.")
    for model in (Invoice, SalesInvoice):
        if order.order_no and db.scalar(select(model.id).where(model.tenant_id == order.tenant_id,
                model.order_no == order.order_no, model.is_deleted.is_(False), model.status == "Paid").limit(1)):
            raise HTTPException(409, "This order has a paid invoice. Resolve its payment before making it inactive.")
    ships = pending_shipments(db, order.tenant_id, order_id=order.id, lock=True)
    cancel_pending(db, order.tenant_id, ships, reason)
    fulfillment.release_holds(db, order)
    works = db.scalars(select(ProductionOrder).where(ProductionOrder.tenant_id == order.tenant_id,
        ProductionOrder.sales_order_id == order.id, ProductionOrder.is_deleted.is_(False)).with_for_update()).all()
    stop_work(db, order.tenant_id, works, reason)
    order.status = "Inactive"
    write_audit(db, tenant_id=order.tenant_id, action="UPDATE", entity_type="sales_order",
                entity_id=str(order.public_id), detail=f"Made inactive: {reason}")
    db.flush()
    return order


def inactive_po(db, *, public_id, reason):
    reason = _reason(reason)
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.public_id == UUID(str(public_id)),
        PurchaseOrder.is_deleted.is_(False)))
    if po is None:
        return None
    order = fulfillment.lock_order(db, tenant_id=po.tenant_id, order_id=po.sales_order_id) if po.sales_order_id else None
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po.id).with_for_update().execution_options(populate_existing=True))
    if po.status == "Inactive":
        return po
    if po.status in ("Received", "Rejected", "Cancelled", "Partially received") or po.inventory_received:
        raise HTTPException(409, "Received or closed POs cannot be made inactive. Reconcile received goods first.")
    if po.po_no and db.scalar(select(GoodsReceipt.id).where(GoodsReceipt.tenant_id == po.tenant_id,
            GoodsReceipt.po_ref == po.po_no, GoodsReceipt.is_deleted.is_(False), GoodsReceipt.received_count > 0).limit(1)):
        raise HTTPException(409, "This PO has received goods. Reconcile its receipt first.")
    if po.po_no and db.scalar(select(SupplierBill.id).join(PoInvoice, PoInvoice.supplier_bill_id == SupplierBill.id).where(
            PoInvoice.tenant_id == po.tenant_id, PoInvoice.po_no == po.po_no, PoInvoice.is_deleted.is_(False),
            SupplierBill.tenant_id == po.tenant_id, SupplierBill.is_deleted.is_(False), SupplierBill.status == "Paid").limit(1)):
        raise HTTPException(409, "This PO has a paid supplier bill. Resolve its payment before making it inactive.")
    works = db.scalars(select(ProductionOrder).where(ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.purchase_order_id == po.id, ProductionOrder.is_deleted.is_(False)).with_for_update()).all()
    received = db.scalar(select(BatchLine.id).join(ProductionBatch, ProductionBatch.id == BatchLine.batch_id).where(
        BatchLine.tenant_id == po.tenant_id, ProductionBatch.order_id.in_([w.id for w in works]), BatchLine.received > 0).limit(1))
    if received:
        raise HTTPException(409, "This PO has warehouse receipts. Reconcile its inventory before withdrawal.")
    cancel_pending(db, po.tenant_id, pending_shipments(db, po.tenant_id, work_ids=[w.id for w in works], lock=True), reason)
    stop_work(db, po.tenant_id, works, reason)
    po.status = "Inactive"
    db.flush()
    if order is not None and order.status == "Awaiting stock" and not fulfillment.purchase_orders(db, order):
        order.status = "PO needed"
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="purchase_order",
                entity_id=str(po.public_id), detail=f"Made inactive: {reason}")
    db.flush()
    return po

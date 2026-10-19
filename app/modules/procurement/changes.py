"""Review PO changes without cascading into production, stock, or accounting."""
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.core.audit import write_audit
from app.models.procurement import PurchaseOrder, GoodsReceipt, PoInvoice
from app.models.production import ProductionOrder
from app.models.finance import SupplierBill, Payment
from app.models.quality import Inspection
from app.models.shipments import Shipment
from app.models.sales import SalesOrder
from app.models.batches import ProductionBatch, BatchLine

CLOSED = {"Rejected", "Cancelled", "Inactive"}


def get_po(db, public_id, tenant_id, lock=False):
    query = select(PurchaseOrder).where(PurchaseOrder.public_id == UUID(str(public_id)),
        PurchaseOrder.tenant_id == UUID(str(tenant_id)), PurchaseOrder.is_deleted.is_(False))
    po = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if po is None:
        raise HTTPException(404, "Purchase order not found")
    return po


def review(db, po, *, status=None, expected=None, lock=False, permissions=None):
    from app.modules.procurement.performance import iso_date
    closing = status in CLOSED
    blockers, issues = [], []
    if status is not None and status not in CLOSED:
        raise HTTPException(422, "Choose Cancelled, Rejected, or Inactive for a withdrawal review.")
    if status is None and expected is None:
        # Also used to display outstanding dependencies after cancellation.
        closing = po.status in CLOSED
    if (status is not None or expected is not None) and po.status in CLOSED:
        blockers.append("This PO is closed. Create a replacement PO instead of reopening or editing it.")
    if expected is not None and not expected.strip():
        raise HTTPException(422, "Expected delivery is required.")
    if expected is not None and iso_date(expected.strip()) is None:
        raise HTTPException(422, "Expected delivery must be a complete date in YYYY-MM-DD format.")
    if closing and (po.inventory_received or po.status in ("Received", "Partially received")):
        blockers.append("Warehouse receipts exist. Resolve received stock through Inventory before withdrawal.")

    allowed = set(permissions or [])

    def add(module, ref, state, href, action, *, key=None, resolve_label=None, permission=None):
        can = bool(key) and (permission is None or "*" in allowed or permission in allowed)
        issues.append({"module": module, "reference": ref, "status": state, "href": href, "action": action,
                       "resolveKey": key if can else None, "canResolve": can, "resolveLabel": resolve_label if can else None})

    works = db.scalars(select(ProductionOrder).where(ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.purchase_order_id == po.id).order_by(ProductionOrder.id)).all()
    from sqlalchemy import or_
    active = [w for w in works if not w.is_deleted and w.stage not in ("Cancelled", "Inactive")]
    goods_posted = db.scalar(select(GoodsReceipt.id).where(GoodsReceipt.tenant_id == po.tenant_id,
        GoodsReceipt.po_ref == po.po_no, GoodsReceipt.is_deleted.is_(False), GoodsReceipt.received_count > 0).limit(1))
    for work in active:
        produced = db.scalar(select(BatchLine.id).join(ProductionBatch, ProductionBatch.id == BatchLine.batch_id).where(
            ProductionBatch.tenant_id == po.tenant_id, ProductionBatch.order_id == work.id,
            or_(BatchLine.completed > 0, BatchLine.received > 0)).limit(1))
        add("Production", work.order_no, work.stage, f"/production/porders/{work.public_id}",
            "Cancel this production order here when no pieces have been produced or received." if not produced and not goods_posted else
            "Arrange manual TNA closure and reconcile work already performed.",
            key=None if produced or goods_posted else f"production:{work.public_id}",
            resolve_label="Cancel production order", permission="production.write")
    batch_lines = db.scalars(select(BatchLine).join(ProductionBatch, ProductionBatch.id == BatchLine.batch_id).where(
        BatchLine.tenant_id == po.tenant_id, ProductionBatch.order_id.in_([w.id for w in works])).order_by(BatchLine.id)).all()
    if closing and any(line.completed > 0 or line.received > 0 for line in batch_lines):
        blockers.append("Batch output or warehouse receipts exist. Reconcile production and inventory before withdrawal.")
    refs = [w.order_no for w in works if w.order_no]
    for ins in db.scalars(select(Inspection).where(Inspection.tenant_id == po.tenant_id,
            Inspection.order_ref.in_(refs), Inspection.is_deleted.is_(False)).order_by(Inspection.id)).all():
        if ins.result not in ("Cancelled", "Pass", "Fail"):
            released = (ins.approved_qty or 0) + (ins.held_qty or 0) + (ins.rejected_qty or 0)
            add("Quality", ins.order_ref, ins.result, f"/quality/inspections/{ins.public_id}",
                "Cancel this open inspection here. A completed inspection stays in its history." if not released else
                "This inspection already recorded quantities. Resolve it in Quality.",
                key=None if released else f"quality:{ins.public_id}",
                resolve_label="Cancel inspection", permission="quality.write")
    from app.modules.catalog.commitments import pending_shipments
    ships = {s.id: s for s in pending_shipments(db, po.tenant_id, work_ids=[w.id for w in works])}
    direct_ship_ids = set(ships)
    for ship in db.scalars(select(Shipment).where(Shipment.tenant_id == po.tenant_id,
            Shipment.is_deleted.is_(False), Shipment.status.not_in(["Cancelled", "Returned"]),
            (Shipment.order_ref.in_(refs + [po.po_no])) |
            ((Shipment.sales_order_id == po.sales_order_id) if po.sales_order_id else False))).all():
        ships[ship.id] = ship
        if ship.order_ref in refs + [po.po_no]:
            direct_ship_ids.add(ship.id)
    for ship in sorted(ships.values(), key=lambda s: s.id):
        preparing = ship.id in direct_ship_ids and ship.status in ("Pending", "Label created") and not ship.dispatched_at and not ship.inventory_dispatched
        add("Shipments", ship.order_ref, ship.status, f"/shipments/shipments/{ship.public_id}",
            "Cancel this shipment here while it is still being prepared." if preparing else
            "Review source allocations and cancel or reallocate pending shipment quantities manually.",
            key=f"shipment:{ship.public_id}" if preparing else None,
            resolve_label="Cancel shipment", permission="shipments.write")
        if closing and ship.id in direct_ship_ids and ship.status not in ("Pending", "Label created"):
            blockers.append("A linked shipment has progressed beyond preparation. Reconcile its dispatch or return first.")
    receipts = db.scalars(select(GoodsReceipt).where(GoodsReceipt.tenant_id == po.tenant_id,
        GoodsReceipt.po_ref == po.po_no, GoodsReceipt.is_deleted.is_(False)).order_by(GoodsReceipt.id)).all()
    for receipt in receipts:
        empty = receipt.received_count == 0
        add("Inventory", receipt.grn_no, receipt.status, "/inventory/stock",
            "Remove this receipt here because no goods have been received." if empty else
            "Reconcile received quantities and outstanding warehouse commitments manually.",
            key=f"inventory:{receipt.public_id}" if empty else None,
            resolve_label="Remove empty receipt", permission="inventory.write")
        if closing and receipt.received_count > 0:
            blockers.append(f"{receipt.grn_no} has received goods; reconcile its stock before withdrawal.")
    invoices = db.scalars(select(PoInvoice).where(PoInvoice.tenant_id == po.tenant_id,
        PoInvoice.po_no == po.po_no, PoInvoice.is_deleted.is_(False)).order_by(PoInvoice.id)).all()
    for invoice in invoices:
        draft = invoice.status == "Draft"
        add("Procurement invoices", invoice.invoice_no, invoice.status, "/procurement/receipts",
            "Remove this draft invoice here. A posted payable is handled on its own row." if draft else
            "Review the commercial invoice with Finance; its saved document is retained.",
            key=f"invoice:{invoice.public_id}" if draft else None,
            resolve_label="Remove draft invoice", permission="procurement.write")
    bill_ids = [inv.supplier_bill_id for inv in invoices if inv.supplier_bill_id]
    bill_query = select(SupplierBill).where(SupplierBill.tenant_id == po.tenant_id,
        SupplierBill.is_deleted.is_(False),
        (SupplierBill.po_ref == po.po_no) | SupplierBill.id.in_(bill_ids)).order_by(SupplierBill.id)
    bills = db.scalars(bill_query.with_for_update().execution_options(populate_existing=True) if lock else bill_query).all()
    for bill in bills:
        paid = bill.status == "Paid" or db.scalar(select(Payment.id).where(Payment.tenant_id == po.tenant_id,
            Payment.is_deleted.is_(False), Payment.allocated_to == bill.bill_no,
            Payment.status.not_in(["Failed", "Cancelled", "Reversed"])).limit(1))
        add("Finance", bill.bill_no, bill.status, "/finance/supplierledger",
            "Void this unpaid payable here. A posted journal is reversed and kept in Finance." if not paid else
            "Reconcile the payable and any posted journal manually in Finance. Preserve accounting history.",
            key=None if paid else f"bill:{bill.public_id}",
            resolve_label="Void payable", permission="finance.write")
        if closing and bill.status == "Paid":
            blockers.append(f"{bill.bill_no} is paid. Resolve its payment with Finance before withdrawal.")
    for payment in db.scalars(select(Payment).where(Payment.tenant_id == po.tenant_id,
            Payment.is_deleted.is_(False), Payment.allocated_to.in_([b.bill_no for b in bills]),
            Payment.status.not_in(["Failed", "Cancelled", "Reversed"])).order_by(Payment.id)).all():
        add("Finance payments", payment.payment_no, payment.status, "/finance/supplierledger",
            "Reconcile or reallocate this payment manually with Finance.")
        if closing:
            blockers.append(f"Payment {payment.payment_no} is allocated to this PO's payable. Resolve it before withdrawal.")
    if po.sales_order_id:
        order = db.scalar(select(SalesOrder).where(SalesOrder.tenant_id == po.tenant_id, SalesOrder.id == po.sales_order_id))
        if order:
            add("Sales", order.order_no, order.status, f"/sales/orders/{order.public_id}",
                "Review the customer's delivery commitment and arrange replacement supply manually.")
    from app.models.supplier_returns import SupplierReturn
    from app.models.procurement import PurchaseOrderLine
    for ret in db.scalars(select(SupplierReturn).join(PurchaseOrderLine,
            PurchaseOrderLine.id == SupplierReturn.po_line_id).where(SupplierReturn.tenant_id == po.tenant_id,
            PurchaseOrderLine.po_id == po.id, SupplierReturn.status != "Closed").order_by(SupplierReturn.id)).all():
        add("Supplier returns", f"SR-{ret.id}", ret.status, "/procurement/returns",
            "Resolve the supplier credit, refund or replacement separately; original receipts and accounting history are retained.")
    result = {"po": po.po_no, "status": po.status, "expected": po.expected or "",
        "proposedStatus": status, "proposedExpected": expected,
        "issues": issues, "blockers": list(dict.fromkeys(blockers))}
    # The token ignores resolve buttons, so permission differences cannot invalidate a review.
    stable_issues = [{key: issue[key] for key in ("module", "reference", "status", "href", "action")} for issue in issues]
    snapshot = {**result, "issues": stable_issues, "batchQuantities": [(l.id, l.completed, l.received) for l in batch_lines],
        "bills": [(b.id, str(b.amount), b.posted, b.gl_journal_id) for b in bills]}
    result["reviewToken"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return result


def confirm(db, po, *, status=None, expected=None, reason=None, review_token=None, acknowledged=False, actor_id=None):
    impact = review(db, po, status=status, expected=expected, lock=True)
    if not acknowledged or not review_token or review_token != impact["reviewToken"]:
        raise HTTPException(409, "Review the latest PO impact and acknowledge the manual follow-up before saving.")
    if impact["blockers"]:
        raise HTTPException(409, "; ".join(impact["blockers"]))
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 400:
        raise HTTPException(422, "Enter a reason between 3 and 400 characters.")
    before = {"status": po.status, "expected": po.expected}
    if status is not None:
        po.status = status
    if expected is not None:
        if not po.due_baseline_recorded:
            # An older PO's current label does not prove its original promise.
            po.original_due_on = None
            po.due_baseline_recorded = True
        po.expected = expected.strip()
    from app.models.core import User
    user_id = db.scalar(select(User.id).where(User.tenant_id == po.tenant_id,
        User.public_id == UUID(str(actor_id)))) if actor_id else None
    write_audit(db, tenant_id=po.tenant_id, actor_id=user_id, action="UPDATE",
        entity_type="purchase_order", entity_id=str(po.public_id), detail=json.dumps({
            "reason": reason, "before": before, "after": {"status": po.status, "expected": po.expected},
            "manualFollowUp": impact["issues"], "reviewToken": review_token}))
    db.flush()
    return po


def resolve_issue(db, po, *, key, reason, actor=None, permissions=None):
    """Clear one linked record from the PO review when it has not progressed."""
    from app.core.roles import has_permission
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 400:
        raise HTTPException(422, "Enter a reason between 3 and 400 characters.")
    kind, _, raw = (key or "").partition(":")
    try:
        public_id = UUID(raw)
    except ValueError:
        raise HTTPException(422, "Choose one of the linked records shown in this review.")
    needs = {
        "production": "production.write", "quality": "quality.write", "shipment": "shipments.write",
        "inventory": "inventory.write", "invoice": "procurement.write", "bill": "finance.write",
    }.get(kind)
    if needs is None or not has_permission(permissions or [], needs):
        raise HTTPException(403, "Your role cannot change that linked record.")
    if kind == "production":
        _cancel_production(db, po, public_id, reason, actor)
    elif kind == "quality":
        _cancel_inspection(db, po, public_id, reason, actor)
    elif kind == "shipment":
        _cancel_shipment(db, po, public_id, reason, actor)
    elif kind == "inventory":
        _remove_receipt(db, po, public_id, reason, actor)
    elif kind == "invoice":
        _remove_invoice(db, po, public_id, reason, actor)
    else:
        _void_bill(db, po, public_id, reason, actor)
    db.flush()


def _cancel_production(db, po, public_id, reason, actor):
    from sqlalchemy import or_
    from sqlalchemy.orm import noload
    from app.models.production import ProductionStage
    work = db.scalar(select(ProductionOrder).where(ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.public_id == public_id, ProductionOrder.purchase_order_id == po.id,
        ProductionOrder.is_deleted.is_(False)).with_for_update())
    if work is None:
        raise HTTPException(404, "Linked production order not found")
    if work.stage in ("Cancelled", "Inactive"):
        return
    produced = db.scalar(select(BatchLine.id).join(ProductionBatch, ProductionBatch.id == BatchLine.batch_id).where(
        ProductionBatch.tenant_id == po.tenant_id, ProductionBatch.order_id == work.id,
        or_(BatchLine.completed > 0, BatchLine.received > 0)).limit(1))
    received = db.scalar(select(GoodsReceipt.id).where(GoodsReceipt.tenant_id == po.tenant_id,
        GoodsReceipt.po_ref == po.po_no, GoodsReceipt.is_deleted.is_(False), GoodsReceipt.received_count > 0).limit(1))
    if produced or received:
        raise HTTPException(409, "Pieces have already been produced or received. Reconcile them in Production and Inventory.")
    stages = db.scalars(select(ProductionStage).options(
        noload(ProductionStage.order), noload(ProductionStage.line)).where(
        ProductionStage.tenant_id == po.tenant_id, ProductionStage.order_id == work.id,
        ProductionStage.is_deleted.is_(False)).with_for_update(of=ProductionStage)).all()
    for stage in stages:
        if stage.status not in ("Completed", "Received", "Cancelled"):
            stage.status = "Cancelled"
            stage.notes = f"Cancelled with {po.po_no}: {reason}"[:400]
    from app.models.batches import BatchStage
    for batch in db.scalars(select(ProductionBatch).where(ProductionBatch.tenant_id == po.tenant_id,
            ProductionBatch.order_id == work.id).with_for_update()).all():
        if batch.status != "Cancelled":
            batch.status = "Cancelled"
        for step in db.scalars(select(BatchStage).where(BatchStage.tenant_id == po.tenant_id,
                BatchStage.batch_id == batch.id).with_for_update()).all():
            if step.status not in ("Completed", "Received", "Cancelled"):
                step.status = "Cancelled"
                step.notes = f"Cancelled with {po.po_no}: {reason}"[:400]
    work.stage = "Cancelled"
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="production_order",
        entity_id=str(work.public_id), detail=f"{actor or 'Procurement'} cancelled {work.order_no} while reviewing {po.po_no}: {reason}")


def _cancel_inspection(db, po, public_id, reason, actor):
    from app.models.quality import QcReservation
    refs = [row for row in db.scalars(select(ProductionOrder.order_no).where(ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.purchase_order_id == po.id)).all() if row]
    ins = db.scalar(select(Inspection).where(Inspection.tenant_id == po.tenant_id,
        Inspection.public_id == public_id, Inspection.is_deleted.is_(False)).with_for_update())
    if ins is None or ins.order_ref not in refs:
        raise HTTPException(404, "Linked inspection not found")
    if ins.result == "Cancelled":
        return
    if ins.result in ("Pass", "Fail") or (ins.approved_qty or 0) or (ins.held_qty or 0) or (ins.rejected_qty or 0):
        raise HTTPException(409, "This inspection already has a result or recorded quantities. Resolve it in Quality.")
    for reserved in db.scalars(select(QcReservation).where(QcReservation.inspection_id == ins.id)).all():
        db.delete(reserved)
    ins.result = "Cancelled"
    ins.disposition_notes = f"Cancelled with {po.po_no}: {reason}"[:400]
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="inspection",
        entity_id=str(ins.public_id), detail=f"{actor or 'Procurement'} cancelled {ins.inspection_no} while reviewing {po.po_no}: {reason}")


def _cancel_shipment(db, po, public_id, reason, actor):
    ship = db.scalar(select(Shipment).where(Shipment.tenant_id == po.tenant_id,
        Shipment.public_id == public_id, Shipment.is_deleted.is_(False)).with_for_update())
    refs = [row for row in db.scalars(select(ProductionOrder.order_no).where(ProductionOrder.tenant_id == po.tenant_id,
        ProductionOrder.purchase_order_id == po.id)).all() if row]
    if ship is None or ship.order_ref not in refs + [po.po_no]:
        raise HTTPException(404, "Linked shipment not found")
    if ship.status == "Cancelled":
        return
    if ship.dispatched_at or ship.inventory_dispatched or ship.status not in ("Pending", "Label created"):
        raise HTTPException(409, "This shipment has already moved. Reconcile it in Shipments.")
    ship.status = "Cancelled"
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="shipment",
        entity_id=str(ship.public_id), detail=f"{actor or 'Procurement'} cancelled {ship.shipment_no} while reviewing {po.po_no}: {reason}")


def _remove_receipt(db, po, public_id, reason, actor):
    receipt = db.scalar(select(GoodsReceipt).where(GoodsReceipt.tenant_id == po.tenant_id,
        GoodsReceipt.public_id == public_id, GoodsReceipt.po_ref == po.po_no,
        GoodsReceipt.is_deleted.is_(False)).with_for_update())
    if receipt is None:
        raise HTTPException(404, "Linked receipt not found")
    if receipt.received_count > 0:
        raise HTTPException(409, "Goods were received on this receipt. Reconcile the stock in Inventory.")
    receipt.is_deleted = True
    receipt.status = "Cancelled"
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="goods_receipt",
        entity_id=str(receipt.public_id), detail=f"{actor or 'Procurement'} removed empty {receipt.grn_no} while reviewing {po.po_no}: {reason}")


def _remove_invoice(db, po, public_id, reason, actor):
    invoice = db.scalar(select(PoInvoice).where(PoInvoice.tenant_id == po.tenant_id,
        PoInvoice.public_id == public_id, PoInvoice.po_no == po.po_no,
        PoInvoice.is_deleted.is_(False)).with_for_update())
    if invoice is None:
        raise HTTPException(404, "Linked invoice not found")
    if invoice.is_deleted:
        return
    if invoice.status != "Draft":
        raise HTTPException(409, "Only a draft invoice can be removed from this review.")
    invoice.is_deleted = True
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="po_invoice",
        entity_id=str(invoice.public_id), detail=f"{actor or 'Procurement'} removed draft {invoice.invoice_no} while reviewing {po.po_no}: {reason}")


def _void_bill(db, po, public_id, reason, actor):
    from app.models.finance import JournalEntry
    from app.modules.finance.repository import reverse_journal
    bill = db.scalar(select(SupplierBill).where(SupplierBill.tenant_id == po.tenant_id,
        SupplierBill.public_id == public_id, SupplierBill.is_deleted.is_(False)).with_for_update())
    linked_here = bill and db.scalar(select(PoInvoice.id).where(PoInvoice.tenant_id == po.tenant_id,
        PoInvoice.po_no == po.po_no, PoInvoice.supplier_bill_id == bill.id, PoInvoice.is_deleted.is_(False)).limit(1))
    if bill is None or (bill.po_ref != po.po_no and not linked_here):
        raise HTTPException(404, "Linked payable not found")
    if bill.status == "Paid" or db.scalar(select(Payment.id).where(Payment.tenant_id == po.tenant_id,
            Payment.is_deleted.is_(False), Payment.allocated_to == bill.bill_no,
            Payment.status.not_in(["Failed", "Cancelled", "Reversed"])).limit(1)):
        raise HTTPException(409, "This payable has a payment. Resolve it in Finance.")
    linked = db.scalars(select(PoInvoice).where(PoInvoice.tenant_id == po.tenant_id,
        PoInvoice.supplier_bill_id == bill.id, PoInvoice.is_deleted.is_(False))).all()
    if any(invoice.status != "Draft" for invoice in linked):
        raise HTTPException(409, "Reconcile the commercial invoice before voiding this payable.")
    if bill.posted and bill.gl_journal_id:
        journal = db.get(JournalEntry, bill.gl_journal_id)
        if journal is not None and not journal.is_deleted:
            reverse_journal(db, public_id=str(journal.public_id))
    for invoice in linked:
        invoice.supplier_bill_id = None
    bill.status = "Void"
    bill.is_deleted = True
    write_audit(db, tenant_id=po.tenant_id, action="UPDATE", entity_type="supplier_bill",
        entity_id=str(bill.public_id), detail=f"{actor or 'Procurement'} voided {bill.bill_no} while reviewing {po.po_no}: {reason}")

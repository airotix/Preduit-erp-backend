"""PO approval is required before starting a production timeline or stage."""
from fastapi import HTTPException
from sqlalchemy import select
from app.models.procurement import PurchaseOrder


def start_block_reason(work, po):
    # Legacy production without a source PO retains its original flow.
    if work.purchase_order_id is None:
        return None
    if po is None or po.is_deleted:
        return "The source PO is unavailable. Resolve it in Procurement before starting production TNA."
    # "Partially received" is an approved PO that already took delivery of an earlier batch; its
    # remaining batches must still be able to start production.
    if po.status not in ("Approved", "Partially received"):
        return (f"Production TNA cannot start until {po.po_no or 'the source PO'} is Approved. "
                f"Current PO status: {po.status}. Approve the PO in Procurement first.")
    return None


def require_approved_po(session, work):
    if work.purchase_order_id is None:
        return
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == work.purchase_order_id,
        PurchaseOrder.tenant_id == work.tenant_id).with_for_update().execution_options(populate_existing=True))
    reason = start_block_reason(work, po)
    if reason:
        raise HTTPException(409, reason)
    if po and po.supplier_shipment_required:
        from app.modules.shipments.supplier_inbound import for_po
        shipment = for_po(session, po)
        if shipment and shipment.dispatched_at:
            raise HTTPException(409, "Reconcile the dispatched supplier shipment before changing production work.")

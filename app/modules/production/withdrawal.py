"""Explicit production-team closure after a PO is withdrawn; retain history."""
from uuid import UUID, uuid4
import json
from fastapi import HTTPException
from sqlalchemy import select
from app.core.audit import write_audit
from app.models.production import ProductionOrder, ProductionStage
from app.models.batches import ProductionBatch, BatchLine, BatchStage, BatchEvent
from app.models.procurement import PurchaseOrder


def stop(db, tid, public_id, reason, actor=None):
    tid = UUID(str(tid))
    work = db.scalar(select(ProductionOrder).where(ProductionOrder.tenant_id == tid,
        ProductionOrder.public_id == UUID(public_id), ProductionOrder.is_deleted.is_(False)))
    if not work or not work.purchase_order_id:
        raise HTTPException(404, "Linked production order not found")
    # Source PO first, as for production stage starts and warehouse receipt.
    source = db.scalar(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tid,
        PurchaseOrder.id == work.purchase_order_id).with_for_update().execution_options(populate_existing=True))
    if not source or source.status not in ("Cancelled", "Rejected", "Inactive"):
        raise HTTPException(409, "Use Procurement's impact review to withdraw the source PO before closing this TNA.")
    work = db.scalar(select(ProductionOrder).where(ProductionOrder.id == work.id,
        ProductionOrder.tenant_id == tid).with_for_update().execution_options(populate_existing=True))
    if work.stage == "Cancelled":
        return {"public_id": str(work.public_id), "stage": work.stage}
    reason = reason.strip()
    if len(reason) < 3:
        raise HTTPException(422, "Enter a reason for stopping production.")
    batches = db.scalars(select(ProductionBatch).where(ProductionBatch.tenant_id == tid,
        ProductionBatch.order_id == work.id).order_by(ProductionBatch.id).with_for_update()).all()
    lines = db.scalars(select(BatchLine).where(BatchLine.tenant_id == tid,
        BatchLine.batch_id.in_([b.id for b in batches])).with_for_update()).all()
    if any(line.completed or line.received for line in lines):
        raise HTTPException(409, "Reconcile completed pieces and warehouse receipts before closing production.")
    for stage in db.scalars(select(ProductionStage).where(ProductionStage.tenant_id == tid,
        ProductionStage.order_id == work.id, ProductionStage.is_deleted.is_(False)).with_for_update()).all():
        if stage.status not in ("Completed", "Received", "Cancelled"):
            stage.status = "Cancelled"
            stage.notes = f"Production stopped: {reason}"[:400]
    for batch in batches:
        if batch.status == "Cancelled":
            continue
        batch.status = "Cancelled"
        for stage in db.scalars(select(BatchStage).where(BatchStage.tenant_id == tid,
                BatchStage.batch_id == batch.id).with_for_update()).all():
            if stage.status not in ("Completed", "Received"):
                stage.status = "Cancelled"
                stage.notes = f"Production stopped: {reason}"[:400]
        db.add(BatchEvent(tenant_id=tid, batch_id=batch.id, request_id=uuid4(), kind="Stop production",
            payload=json.dumps({"reason": reason}), result='{"status":"Cancelled"}'))
    work.stage = "Cancelled"
    write_audit(db, tenant_id=tid, action="UPDATE", entity_type="production_order",
        entity_id=str(work.public_id), detail=f"{actor or 'Production team'} manually stopped TNA after {source.po_no} withdrawal: {reason}")
    db.flush()
    return {"public_id": str(work.public_id), "stage": work.stage}

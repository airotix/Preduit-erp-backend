"""Batch TNA and inbound delivery. Outbound shipping keeps its stock ledger."""
from datetime import date, timedelta
from fastapi import HTTPException
from app.models.batches import BatchLine, BatchStage, InboundAllocation, ProductionBatch
from app.models.shipments import Shipment, ShipmentLine
from app.models.inventory import Location
from app.models.procurement import PurchaseOrderLine
from app.modules.batches import service as s


def tna_complete(db, tid, batch_id):
    stages = s.rows(db, BatchStage, tid, BatchStage.batch_id == batch_id)
    return bool(stages) and all(stage.status in ("Completed", "Received") for stage in stages)


def style_complete(db, tid, line):
    source = db.get(PurchaseOrderLine, line.po_line_id)
    if source is None or source.production_line_id is None:
        return False
    stages = s.rows(db, BatchStage, tid, BatchStage.batch_id == line.batch_id,
        BatchStage.production_line_id == source.production_line_id)
    return bool(stages) and all(stage.status in ("Completed", "Received") for stage in stages)


def ready(db, tid, line):
    if not style_complete(db, tid, line):
        return 0
    allocations = s.rows(db, InboundAllocation, tid, InboundAllocation.line_id == line.id)
    committed = sum(a.qty for a in allocations if a.status in ("Reserved", "In transit"))
    # Missing units stay unresolved. They are not received and not available to ship again.
    unresolved = sum(a.discrepancy_qty or 0 for a in allocations if a.status == "Delivered")
    return max(0, line.approved - line.received - committed - unresolved)


def states(db, tid, batch, lines):
    if batch.status == "Cancelled":
        return {"productionStatus": "Cancelled", "qualityStatus": "Cancelled", "shipmentStatus": "Cancelled"}
    complete = tna_complete(db, tid, batch.id)
    produced = sum(l.completed for l in lines)
    planned = sum(l.planned for l in lines)
    held = sum(l.held for l in lines)
    awaiting = sum(l.completed-l.approved-l.held-l.rejected for l in lines)
    approved = sum(l.approved for l in lines)
    received = sum(l.received for l in lines)
    allocations = s.rows(db, InboundAllocation, tid, InboundAllocation.line_id.in_([l.id for l in lines]))
    production = "Complete" if complete and produced == planned else "Record produced quantities" if complete else "TNA in progress"
    quality = "QC hold / rework" if held else "Awaiting final QC" if awaiting else "QC complete" if produced == planned and produced else "Partially inspected" if approved else "Inline QC / awaiting production"
    shipping = "Inbound in transit" if any(a.status == "In transit" for a in allocations) else "Inbound pending" if any(a.status == "Reserved" for a in allocations) else "Ready for inbound shipment" if any(ready(db, tid, l) for l in lines) else "Delivered to warehouse" if received and received == approved and produced == planned and not held and not awaiting else "Partially delivered" if received else "Awaiting TNA / QC"
    return {"productionStatus": production, "qualityStatus": quality, "shipmentStatus": shipping}


def schedule(db, tid, actor, batch_id, stage_id, command, extend=False):
    batch, work, _ = s.locked_batch(db, tid, batch_id)
    action = "Extend TNA" if extend else "Plan TNA"
    prior, payload = s.begin(db, tid, command, action, stage_id)
    if prior is not None: return prior
    if batch.status == "Cancelled": raise HTTPException(409, "This batch is cancelled")
    stages = s.rows(db, BatchStage, tid, BatchStage.batch_id == batch.id)
    found = next((stage for stage in stages if stage.id == stage_id), None)
    if found is None: raise HTTPException(404, "TNA stage not found")
    stages = [stage for stage in stages if stage.production_line_id == found.production_line_id]
    stages.sort(key=lambda stage: stage.seq)
    if found.status == "Completed": raise HTTPException(409, "A completed TNA stage retains its schedule")
    if extend:
        if not found.start_on or not found.end_on: raise HTTPException(409, "Plan this TNA stage before extending it")
        found.duration_days += command.days
        found.end_on += timedelta(days=command.days)
    else:
        previous = next((stage for stage in stages if stage.seq == found.seq-1), None)
        if previous and previous.end_on and command.startOn <= previous.end_on:
            raise HTTPException(409, "Start this stage after the preceding TNA stage ends")
        found.start_on = command.startOn
        found.duration_days = command.durationDays
        found.end_on = command.startOn + timedelta(days=command.durationDays-1)
        found.worker = command.worker.strip()
    found.notes = command.reason
    last_end = found.end_on
    for stage in stages:
        if stage.seq > found.seq and stage.status != "Completed":
            stage.start_on = last_end + timedelta(days=1)
            stage.end_on = stage.start_on + timedelta(days=stage.duration_days-1)
            last_end = stage.end_on
    batch.due_on = stages[-1].end_on.isoformat() if stages[-1].end_on else batch.due_on
    return s.end(db, tid, actor, command, action, payload, {"stageId": found.id}, batch.id)


def create_inbound(db, tid, actor, order_id, command):
    obj, works, pos = s.parent(db, tid, order_id, lock=True)
    prior, payload = s.begin(db, tid, command, "Plan inbound shipment", order_id)
    if prior is not None: return prior
    batches = s.rows(db, ProductionBatch, tid, ProductionBatch.order_id == obj.id, ProductionBatch.status != "Cancelled")
    lines = {line.id: line for line in s.rows(db, BatchLine, tid, BatchLine.batch_id.in_([b.id for b in batches]), lock=True)}
    if len({a.lineId for a in command.allocations}) != len(command.allocations):
        raise HTTPException(400, "Select each batch variant once per shipment")
    locations = {l.id: l for l in s.rows(db, Location, tid, Location.is_deleted.is_(False))}
    for allocation in command.allocations:
        line = lines.get(allocation.lineId)
        if line is None or allocation.qty > ready(db, tid, line):
            raise HTTPException(409, "Inbound shipment requires completed TNA and available QC-approved quantities")
        if allocation.locationId not in locations:
            raise HTTPException(400, "Select an active receiving warehouse")
        s.active_article(db, line)
    if not command.carrier.strip(): raise HTTPException(400, "Enter a carrier")
    if len(pos) != 1:
        raise HTTPException(409, "Plan the inbound shipment from one supplier PO's production workspace.")
    if pos[0].status not in ("Approved", "Partially received", "Received"):
        raise HTTPException(409, "The supplier purchase order must be approved before its goods can be shipped.")
    ship = Shipment(tenant_id=s.uid(tid), batch_managed=True, direction="Inbound", purchase_order_id=pos[0].id, order_ref=pos[0].po_no,
        carrier=command.carrier.strip(), destination=", ".join(dict.fromkeys(locations[a.locationId].name for a in command.allocations)),
        eta=command.eta, status="Pending")
    db.add(ship); db.flush(); ship.shipment_no = f"SHP-{9900 + ship.id}"
    for allocation in command.allocations:
        line = lines[allocation.lineId]; source = db.get(PurchaseOrderLine, line.po_line_id)
        ship_line = ShipmentLine(tenant_id=s.uid(tid), shipment_id=ship.id, po_line_id=source.id, sku=source.sku,
            description=" / ".join(v for v in [source.name, source.color, source.size] if v), qty=allocation.qty)
        db.add(ship_line); db.flush()
        db.add(InboundAllocation(tenant_id=s.uid(tid), shipment_id=ship.id, shipment_line_id=ship_line.id,
            line_id=line.id, location_id=allocation.locationId, qty=allocation.qty))
    return s.end(db, tid, actor, command, "Plan inbound shipment", payload,
        {"publicId": str(ship.public_id), "shipmentNo": ship.shipment_no})


def plan_consolidated(db, tid, actor, command):
    """One incoming shipment for compatible batches from the same supplier."""
    from app.models.procurement import PurchaseOrder
    ids = [a.lineId for a in command.allocations]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Select each article once per shipment.")
    preview = {line.id: line for line in s.rows(db, BatchLine, tid, BatchLine.id.in_(ids))}
    if len(preview) != len(ids):
        raise HTTPException(404, "A selected production line is no longer available.")
    order_ids = sorted({db.get(ProductionBatch, line.batch_id).order_id for line in preview.values()})
    for order_id in order_ids:
        work = db.get(s.ProductionOrder, order_id)
        s.parent(db, tid, work.public_id, lock=True)
    prior, payload = s.begin(db, tid, command, "Plan inbound shipment", "consolidated")
    if prior is not None:
        return prior
    lines = {line.id: line for line in s.rows(db, BatchLine, tid, BatchLine.id.in_(ids), lock=True)}
    locations = {loc.id: loc for loc in s.rows(db, Location, tid, Location.is_deleted.is_(False))}
    if len({a.locationId for a in command.allocations}) != 1:
        raise HTTPException(409, "Goods travelling together need the same destination warehouse.")
    pos = []
    for allocation in command.allocations:
        line = lines.get(allocation.lineId)
        if line is None or allocation.qty > ready(db, tid, line):
            raise HTTPException(409, "Inbound shipment requires completed TNA and available QC-approved quantities.")
        if allocation.locationId not in locations:
            raise HTTPException(400, "Select an active receiving warehouse.")
        s.active_article(db, line)
        batch = db.get(ProductionBatch, line.batch_id)
        work = db.get(s.ProductionOrder, batch.order_id)
        po = db.get(PurchaseOrder, work.purchase_order_id)
        if po is None or po.status not in ("Approved", "Partially received", "Received"):
            raise HTTPException(409, "The supplier purchase order must be approved before its goods can be shipped.")
        pos.append(po)
    if len({po.supplier_id or po.supplier_name for po in pos}) != 1:
        raise HTTPException(409, "Keep different suppliers in separate shipments.")
    if not command.carrier.strip():
        raise HTTPException(400, "Enter a carrier.")
    unique_pos = {po.id: po for po in pos}
    primary = next(iter(unique_pos.values()))
    ship = Shipment(tenant_id=s.uid(tid), batch_managed=True, direction="Inbound",
        purchase_order_id=primary.id if len(unique_pos) == 1 else None,
        order_ref=primary.po_no if len(unique_pos) == 1 else "Multiple POs",
        carrier=command.carrier.strip(), destination=locations[command.allocations[0].locationId].name,
        eta=command.eta, status="Pending")
    db.add(ship)
    db.flush()
    ship.shipment_no = f"SHP-{9900 + ship.id}"
    for allocation in command.allocations:
        line = lines[allocation.lineId]
        source = db.get(PurchaseOrderLine, line.po_line_id)
        ship_line = ShipmentLine(tenant_id=s.uid(tid), shipment_id=ship.id, po_line_id=source.id, sku=source.sku,
            description=" / ".join(v for v in [source.name, source.color, source.size] if v), qty=allocation.qty)
        db.add(ship_line)
        db.flush()
        db.add(InboundAllocation(tenant_id=s.uid(tid), shipment_id=ship.id, shipment_line_id=ship_line.id,
            line_id=line.id, location_id=allocation.locationId, qty=allocation.qty))
    return s.end(db, tid, actor, command, "Plan inbound shipment", payload,
        {"publicId": str(ship.public_id), "shipmentNo": ship.shipment_no})


def inbound_status(db, tid, actor, ship, command):
    allocations = s.rows(db, InboundAllocation, tid, InboundAllocation.shipment_id == ship.id)
    if not allocations: raise HTTPException(409, "Inbound shipment has no batch sources")
    # Serialize against planning, QC and every other delivery of this PO.
    batch_ids = sorted({db.get(BatchLine, a.line_id).batch_id for a in allocations})
    for batch_id in batch_ids:
        s.locked_batch(db, tid, db.get(ProductionBatch, batch_id).public_id)
    ship = s.rows(db, Shipment, tid, Shipment.id == ship.id, lock=True)[0]
    allocations = s.rows(db, InboundAllocation, tid, InboundAllocation.shipment_id == ship.id, lock=True)
    prior, payload = s.begin(db, tid, command, "Shipment status", ship.public_id)
    if prior is not None: return prior
    status = command.status
    if ship.status in ("Delivered", "Cancelled"):
        raise HTTPException(409, "This inbound shipment is closed; its delivery cannot be posted again")
    sequence = ["Pending", "In transit", "Customs", "Out for delivery", "Delivered"]
    if status not in sequence + ["Cancelled"]:
        raise HTTPException(409, "Use the warehouse returns process for a delivered inbound shipment")
    if status == "Cancelled":
        if ship.status != "Pending": raise HTTPException(409, "Reconcile a shipment already dispatched from the factory before cancellation")
        for a in allocations: a.status = "Cancelled"
    else:
        if sequence.index(status) <= sequence.index(ship.status): raise HTTPException(409, "Shipment status must move forward")
        if ship.status == "Pending" and status != "In transit": raise HTTPException(409, "Confirm factory dispatch before recording transit")
        if status == "Delivered":
            raise HTTPException(409, "Confirm physical arrival in Inventory → Warehouse receiving. Stock is posted there, once.")
        if not (ship.carrier or "").strip() or not (ship.destination or "").strip():
            raise HTTPException(409, "Enter the carrier and destination before dispatch.")
        for a in allocations: a.status = "In transit"
    ship.status = status
    if status == "In transit" and ship.dispatched_at is None:
        from datetime import datetime, timezone
        ship.dispatched_at = datetime.now(timezone.utc)
    return s.end(db, tid, actor, command, "Shipment status", payload,
        {"publicId": str(ship.public_id), "status": status})


def receive_inbound(db, tid, actor, ship, command):
    """Post warehouse stock for a dispatched batch inbound shipment. One receipt per retry."""
    allocations = s.rows(db, InboundAllocation, tid, InboundAllocation.shipment_id == ship.id)
    if not allocations:
        raise HTTPException(409, "Inbound shipment has no batch sources")
    batch_ids = sorted({db.get(BatchLine, a.line_id).batch_id for a in allocations})
    for batch_id in batch_ids:
        s.locked_batch(db, tid, db.get(ProductionBatch, batch_id).public_id)
    ship = s.rows(db, Shipment, tid, Shipment.id == ship.id, lock=True)[0]
    allocations = s.rows(db, InboundAllocation, tid, InboundAllocation.shipment_id == ship.id, lock=True)
    prior, payload = s.begin(db, tid, command, "Warehouse receipt", ship.public_id)
    if prior is not None:
        return prior
    if ship.direction != "Inbound" or not ship.batch_managed:
        raise HTTPException(409, "This receipt applies to a dispatched incoming batch shipment.")
    if ship.status == "Delivered":
        raise HTTPException(409, "This shipment was already received. Reload the receipt history.")
    if ship.status == "Cancelled":
        raise HTTPException(409, "A cancelled shipment cannot be received.")
    if ship.status not in ("In transit", "Customs", "Out for delivery", "Dispatched"):
        raise HTTPException(409, "Dispatch this supplier shipment in Shipments before warehouse receipt.")
    accepted = command.accepted
    if len({a.allocationId for a in accepted}) != len(accepted) or {a.allocationId for a in accepted} != {a.id for a in allocations}:
        raise HTTPException(422, "Confirm the physically received quantity for every shipment line.")
    quantities = {a.allocationId: a.qty for a in accepted}
    found = s.rows(db, Location, tid, Location.id == command.locationId, Location.is_deleted.is_(False))
    if not found:
        raise HTTPException(409, "Choose an active warehouse location.")
    location = found[0]
    from uuid import uuid5
    from datetime import datetime, timezone
    receipts = []
    for a in allocations:
        if quantities[a.id] > a.qty:
            raise HTTPException(409, "Received quantity cannot exceed the quantity dispatched.")
        line = db.get(BatchLine, a.line_id)
        s.active_article(db, line)
        batch = db.get(ProductionBatch, line.batch_id)
        work = db.get(s.ProductionOrder, batch.order_id)
        if quantities[a.id]:
            receipts.append(s.receive_stock(db, tid, work, line, quantities[a.id], location.id,
                actor=actor, request_id=uuid5(command.requestId, f"receipt-{a.id}")))
        shortfall = a.qty - quantities[a.id]
        a.accepted = quantities[a.id]
        a.status = "Delivered"
        a.discrepancy_qty = shortfall
        a.discrepancy_status = "Open" if shortfall else None
        db.flush()
        s.refresh(db, tid, work)
    ship.status = "Delivered"
    ship.delivered_at = datetime.now(timezone.utc)
    db.flush()
    receipt = next((item for item in receipts if item is not None), None)
    if receipt is not None and receipt.public_id is None:
        db.refresh(receipt)
    return s.end(db, tid, actor, command, "Warehouse receipt", payload,
        {"publicId": str(receipt.public_id) if receipt else str(ship.public_id),
         "grnNo": receipt.grn_no if receipt else ship.shipment_no, "status": ship.status})

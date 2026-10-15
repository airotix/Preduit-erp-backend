"""Transactional batch ledgers. Quantity changes are commands, never editable totals."""
import json
from datetime import date, timedelta
from uuid import UUID, uuid5
from sqlalchemy import select, func
from fastapi import HTTPException
from app.models.batches import ProductionBatch, BatchLine, BatchStage, BatchLot, BatchAllocation, BatchEvent, InventoryAllocation, InboundAllocation
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, GoodsReceipt, GoodsReceiptLine
from app.models.sales import SalesOrder, SalesOrderLine
from app.models.inventory import Location, StockLevel, OrderStockReservation
from app.models.shipments import Shipment, ShipmentLine
from app.modules.sales import fulfillment


def uid(value):
    return UUID(str(value))


def rows(db, model, tid, *conditions, lock=False):
    query = select(model).where(model.tenant_id == uid(tid), *conditions).order_by(model.id)
    if lock:
        db.flush()
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalars(query).all()


def one(db, model, tid, public_id):
    try:
        record_id = uid(public_id)
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid record ID")
    found = rows(db, model, tid, model.public_id == record_id)
    if not found:
        raise HTTPException(404, "Record not found")
    return found[0]


def parent(db, tid, public_id, kind="production", lock=False):
    model = {"production": ProductionOrder, "sales": SalesOrder, "procurement": PurchaseOrder}.get(kind)
    if model is None:
        raise HTTPException(400, "Invalid order type")
    obj = one(db, model, tid, public_id)
    if obj.is_deleted or getattr(obj, "status", None) in ("Cancelled", "Rejected", "Inactive"):
        raise HTTPException(409, "This order is no longer active")
    if kind == "production":
        works = [obj]
    elif kind == "procurement":
        works = rows(db, ProductionOrder, tid, ProductionOrder.purchase_order_id == obj.id, ProductionOrder.is_deleted.is_(False))
    else:
        pos = rows(db, PurchaseOrder, tid, PurchaseOrder.sales_order_id == obj.id, PurchaseOrder.is_deleted.is_(False), PurchaseOrder.status.not_in(["Rejected", "Cancelled", "Inactive"]))
        works = rows(db, ProductionOrder, tid, ProductionOrder.purchase_order_id.in_([p.id for p in pos]), ProductionOrder.is_deleted.is_(False))
    pos = rows(db, PurchaseOrder, tid, PurchaseOrder.id.in_([w.purchase_order_id for w in works]))
    sales_ids = sorted({p.sales_order_id for p in pos if p.sales_order_id})
    if kind == "sales":
        sales_ids = [obj.id]
    if lock:
        sales = rows(db, SalesOrder, tid, SalesOrder.id.in_(sales_ids), lock=True)
        if any(s.status in ("Cancelled", "Inactive") for s in sales):
            raise HTTPException(409, "The sales order is cancelled")
        works = rows(db, ProductionOrder, tid, ProductionOrder.id.in_([w.id for w in works]), lock=True)
        pos = rows(db, PurchaseOrder, tid, PurchaseOrder.id.in_([p.id for p in pos]), lock=True)
        if any(p.status in ("Rejected", "Cancelled", "Inactive") for p in pos) or any(w.is_deleted for w in works):
            raise HTTPException(409, "Production is no longer active")
    return obj, works, pos


def begin(db, tid, command, kind, scope):
    payload = json.dumps({"scope": str(scope), **command.model_dump(mode="json")}, sort_keys=True)
    prior = rows(db, BatchEvent, tid, BatchEvent.request_id == command.requestId)
    if prior:
        if prior[0].kind != kind or prior[0].payload != payload:
            raise HTTPException(409, "This request ID was already used for a different operation")
        return json.loads(prior[0].result), payload
    return None, payload


def end(db, tid, actor, command, kind, payload, result, batch_id=None):
    from app.models.core import User
    try:
        user = db.scalar(select(User).where(User.tenant_id == uid(tid), User.public_id == uid(actor)))
        if user:
            actor = f"{user.display_name or user.email} ({user.email})"[:120]
    except (ValueError, TypeError):
        pass
    db.add(BatchEvent(tenant_id=uid(tid), batch_id=batch_id, request_id=command.requestId,
        kind=kind, actor=actor, payload=payload, result=json.dumps(result)))
    db.flush()
    return result


def managed_sales(db, order):
    return bool(db.scalar(select(ProductionBatch.id).join(ProductionOrder, ProductionOrder.id == ProductionBatch.order_id)
        .join(PurchaseOrder, PurchaseOrder.id == ProductionOrder.purchase_order_id).where(
            ProductionBatch.tenant_id == order.tenant_id, PurchaseOrder.tenant_id == order.tenant_id,
            PurchaseOrder.sales_order_id == order.id, ProductionOrder.is_deleted.is_(False)).limit(1)))


def create_batch(db, tid, actor, order_id, command):
    obj, works, pos = parent(db, tid, order_id, lock=True)
    prior, payload = begin(db, tid, command, "Plan batch", order_id)
    if prior is not None:
        return prior
    if not pos or pos[0].inventory_received:
        raise HTTPException(409, "Batch planning requires an unreceived source PO")
    if rows(db, ProductionStage, tid, ProductionStage.order_id == obj.id,
            ProductionStage.is_deleted.is_(False), ProductionStage.name != "Tech Pack"):
        raise HTTPException(409, "This order already has a production timeline; reconcile its work before planning batches")
    from app.models.quality import Inspection
    if rows(db, Inspection, tid, Inspection.order_ref == obj.order_no,
            Inspection.batch_line_id.is_(None), Inspection.is_deleted.is_(False)):
        raise HTTPException(409, "This order already has whole-order inspections; reconcile them before planning batches")
    # Legacy completed orders cannot be relabelled as new lots without reconciliation.
    if pos[0].sales_order_id:
        legacy = rows(db, Shipment, tid, Shipment.sales_order_id == pos[0].sales_order_id, Shipment.is_deleted.is_(False))
        for ship in legacy:
            if not ship.batch_managed:
                if ship.inventory_dispatched:
                    order = db.get(SalesOrder, pos[0].sales_order_id)
                    if order.status == "Shipped" or db.scalar(select(ShipmentLine.id).where(
                        ShipmentLine.tenant_id == uid(tid), ShipmentLine.shipment_id == ship.id,
                        ShipmentLine.order_line_id.is_(None)).limit(1)):
                        raise HTTPException(409, "Reconcile the completed legacy whole-order shipment before enabling batches")
                elif ship.packed_at and ship.status != "Cancelled":
                    raise HTTPException(409, "Dispatch the physically packed ordinary shipment before planning production batches")
                else:
                    ship.status = "Cancelled"
    sources = {s.id: s for s in rows(db, PurchaseOrderLine, tid, PurchaseOrderLine.po_id == pos[0].id)}
    active_batches = rows(db, ProductionBatch, tid, ProductionBatch.order_id == obj.id, ProductionBatch.status != "Cancelled")
    existing = rows(db, BatchLine, tid, BatchLine.po_line_id.in_(sources), BatchLine.batch_id.in_([b.id for b in active_batches]))
    planned, rejected = {}, {}
    for line in existing:
        planned[line.po_line_id] = planned.get(line.po_line_id, 0) + line.planned
        rejected[line.po_line_id] = rejected.get(line.po_line_id, 0) + line.rejected
    if len({l.sourceId for l in command.lines}) != len(command.lines):
        raise HTTPException(400, "Select each order variant once")
    variants = {}
    for line in command.lines:
        source = sources.get(line.sourceId)
        if not source or line.qty > source.qty + rejected.get(source.id, 0) - planned.get(source.id, 0):
            raise HTTPException(409, "Batch quantity exceeds unplanned order quantity plus scrapped replacements")
        variants[source.id] = fulfillment.variant_for(db, obj, source)
        if variants[source.id] is None:
            raise HTTPException(409, "Set a catalog variant for every batch line")
    stages = [s.strip() for s in command.stages]
    if any(not s or len(s) > 80 for s in stages) or len(set(stages)) != len(stages):
        raise HTTPException(400, "Use distinct stage names of 1–80 characters")
    batch = ProductionBatch(tenant_id=uid(tid), order_id=obj.id, name=command.name.strip(),
        factory=command.factory.strip() or obj.factory, due_on=command.dueOn.isoformat() if command.dueOn else None)
    if not batch.name:
        raise HTTPException(400, "Enter a batch name")
    db.add(batch); db.flush()
    for line in command.lines:
        db.add(BatchLine(tenant_id=uid(tid), batch_id=batch.id, po_line_id=line.sourceId,
            variant_id=variants[line.sourceId].id, planned=line.qty))
    start = command.startOn
    style_ids = sorted({source.production_line_id for source in (sources[line.sourceId] for line in command.lines)
                        if source.production_line_id is not None})
    if len(style_ids) != len({source.name for source in (sources[line.sourceId] for line in command.lines)}):
        raise HTTPException(409, "Link every purchase-order style to its production item before planning a batch")
    for style_id in style_ids:
        start = command.startOn
        for i, name in enumerate(stages):
            duration = command.durations[i] if command.durations else 1
            finish = start + timedelta(days=max(1, duration)-1)
            db.add(BatchStage(tenant_id=uid(tid), batch_id=batch.id, production_line_id=style_id,
                seq=i+1, name=name, start_on=start, end_on=finish, duration_days=duration))
            start = finish + timedelta(days=1)
    if command.dueOn and command.dueOn < finish:
        raise HTTPException(422, "The target completion date cannot precede the TNA schedule")
    batch.due_on = (command.dueOn or finish).isoformat()
    db.flush()
    from app.modules import techpacks
    techpacks.sync_order(db, obj)
    obj.stage = stages[0] if stages[0] in ("Cutting", "Sewing", "Finishing", "Packed") else "Cutting"
    return end(db, tid, actor, command, "Plan batch", payload, {"publicId": str(batch.public_id)}, batch.id)


def locked_batch(db, tid, batch_id):
    batch = one(db, ProductionBatch, tid, batch_id)
    work = db.get(ProductionOrder, batch.order_id)
    if not work or work.tenant_id != uid(tid):
        raise HTTPException(404, "Production order not found")
    parent(db, tid, work.public_id, lock=True)
    batch = rows(db, ProductionBatch, tid, ProductionBatch.id == batch.id, lock=True)[0]
    lines = rows(db, BatchLine, tid, BatchLine.batch_id == batch.id, lock=True)
    return batch, work, lines


def refresh(db, tid, work):
    batches = rows(db, ProductionBatch, tid, ProductionBatch.order_id == work.id, ProductionBatch.status != "Cancelled")
    for batch in batches:
        lines = rows(db, BatchLine, tid, BatchLine.batch_id == batch.id)
        if any(l.held for l in lines): batch.status = "QC hold"
        elif all(l.approved + l.rejected == l.planned for l in lines): batch.status = "QC complete"
        elif all(l.completed == l.planned for l in lines): batch.status = "Awaiting QC"
        elif any(l.completed for l in lines): batch.status = "In progress"
    all_lines = rows(db, BatchLine, tid, BatchLine.batch_id.in_([b.id for b in batches]))
    work.progress = min(100, round(100 * sum(l.completed for l in all_lines) / max(1, work.qty)))
    stages = rows(db, BatchStage, tid, BatchStage.batch_id.in_([b.id for b in batches]))
    active = next((s.name for s in stages if s.status != "Completed"), None)
    work.stage = "Packed" if work.progress == 100 else active if active in ("Cutting", "Sewing", "Finishing") else "Sewing"
    db.flush()
    if work.purchase_order_id:
        po = db.get(PurchaseOrder, work.purchase_order_id)
        if po.sales_order_id:
            update_sales(db, tid, po.sales_order_id)


def stage(db, tid, actor, batch_id, stage_id, command):
    batch, work, _ = locked_batch(db, tid, batch_id)
    prior, payload = begin(db, tid, command, "Production stage", stage_id)
    if prior is not None: return prior
    if batch.status == "Cancelled": raise HTTPException(409, "This batch is cancelled")
    stages = rows(db, BatchStage, tid, BatchStage.batch_id == batch.id)
    found = next((s for s in stages if s.id == stage_id), None)
    if found is None: raise HTTPException(404, "Batch stage not found")
    if found.name == "Tech Pack": raise HTTPException(409, "Upload the article's tech pack to receive this stage")
    if found.status == "Completed" and command.status != "Completed":
        raise HTTPException(409, "A completed stage cannot be reopened")
    if any(s.production_line_id == found.production_line_id and s.seq < found.seq
           and s.status not in ("Completed", "Received") for s in stages):
        raise HTTPException(409, "Complete earlier production stages first")
    if found.status in ("Completed", "Received"):
        raise HTTPException(409, "This TNA stage is already complete")
    if not found.start_on or not found.end_on:
        raise HTTPException(409, "Plan this batch's TNA dates before recording progress")
    found.actual_start_on = found.actual_start_on or date.today()
    if command.status == "Completed": found.actual_end_on = date.today()
    found.status, found.worker, found.notes = command.status, command.worker.strip(), command.reason
    batch.status = "In progress"
    db.flush(); refresh(db, tid, work)
    return end(db, tid, actor, command, "Production stage", payload, {"status": found.status}, batch.id)


def start_style(db, tid, actor, batch_id, command):
    batch, work, lines = locked_batch(db, tid, batch_id)
    prior, payload = begin(db, tid, command, "Start batch style", batch_id)
    if prior is not None: return prior
    if batch.status == "Cancelled": raise HTTPException(409, "This batch is cancelled")
    source_lines = [db.get(PurchaseOrderLine, line.po_line_id) for line in lines]
    if command.productionLineId not in {source.production_line_id for source in source_lines}:
        raise HTTPException(404, "Style does not belong to this batch")
    stages = sorted(rows(db, BatchStage, tid, BatchStage.batch_id == batch.id,
        BatchStage.production_line_id == command.productionLineId), key=lambda item: item.seq)
    if not stages: raise HTTPException(409, "This style has no production timeline")
    if any(stage.status not in ("Pending", "Received") for stage in stages):
        raise HTTPException(409, "This style has already started production")
    requested = command.stages
    if [item.get("name") for item in requested] != [stage.name for stage in stages]:
        raise HTTPException(422, "Start the existing stages in their planned order")
    if any(type(item.get("days")) is not int or item["days"] < (0 if item["name"] == "Tech Pack" else 1)
           or item["days"] > 365 for item in requested):
        raise HTTPException(422, "Enter a valid duration for each stage")
    from app.modules import techpacks
    production_line = db.get(ProductionOrderLine, command.productionLineId)
    pack_received = techpacks.line_pack(db, production_line)["status"] == "Received" if production_line else False
    cursor = date.today()
    first_work = True
    for current, wanted in zip(stages, requested):
        current.duration_days = wanted["days"]
        current.start_on = cursor
        current.end_on = cursor + timedelta(days=max(1, wanted["days"])-1)
        cursor = current.end_on + timedelta(days=1)
        if current.name == "Tech Pack":
            current.status = "Received" if pack_received else "Pending"
        elif first_work:
            current.status = "In progress"
            current.actual_start_on = date.today()
            first_work = False
        else:
            current.status = "Pending"
    batch.status = "In progress"
    db.flush(); refresh(db, tid, work)
    return end(db, tid, actor, command, "Start batch style", payload,
        {"productionLineId": command.productionLineId}, batch.id)


def stage_action(db, tid, actor, batch_id, stage_id, action, command):
    if action not in ("start", "complete", "assign", "notes", "extend", "resolve"):
        raise HTTPException(404, "Unknown production stage action")
    batch, work, _ = locked_batch(db, tid, batch_id)
    prior, payload = begin(db, tid, command, "Batch stage " + action, stage_id)
    if prior is not None: return prior
    if batch.status == "Cancelled": raise HTTPException(409, "This batch is cancelled")
    all_stages = rows(db, BatchStage, tid, BatchStage.batch_id == batch.id)
    found = next((stage for stage in all_stages if stage.id == stage_id), None)
    if found is None: raise HTTPException(404, "Batch stage not found")
    if found.name == "Tech Pack":
        raise HTTPException(409, "Upload the article's tech pack to receive this stage")
    if found.status in ("Completed", "Received"):
        raise HTTPException(409, "A completed stage cannot be changed")
    stages = sorted((stage for stage in all_stages if stage.production_line_id == found.production_line_id), key=lambda stage: stage.seq)
    following = [stage for stage in stages if stage.seq > found.seq and stage.status != "Completed"]
    if action in ("start", "complete"):
        if any(stage.seq < found.seq and stage.status not in ("Completed", "Received") for stage in stages):
            raise HTTPException(409, "Complete earlier production stages first")
        if action == "start":
            found.status = "In progress"
            found.actual_start_on = found.actual_start_on or date.today()
        else:
            found.status = "Completed"
            found.actual_start_on = found.actual_start_on or date.today()
            found.actual_end_on = date.today()
    elif action == "assign":
        if not command.worker.strip(): raise HTTPException(422, "Enter a worker")
        found.worker = command.worker.strip()
    elif action == "notes":
        found.notes = command.notes.strip()
    else:
        if action == "extend":
            if command.days < 1: raise HTTPException(422, "Enter extra days")
            delta = command.days
            found.duration_days += delta
            found.end_on = (found.end_on or date.today()) + timedelta(days=delta)
        else:
            delta = (date.today() - (found.start_on or date.today())).days
            found.start_on = date.today()
            found.end_on = date.today() + timedelta(days=max(1, found.duration_days)-1)
        for later in following:
            if later.start_on: later.start_on += timedelta(days=delta)
            if later.end_on: later.end_on += timedelta(days=delta)
    db.flush(); refresh(db, tid, work)
    return end(db, tid, actor, command, "Batch stage " + action, payload,
        {"stageId": found.id, "status": found.status}, batch.id)


def line_action(db, tid, actor, batch_id, line_id, command, action):
    batch, work, lines = locked_batch(db, tid, batch_id)
    prior, payload = begin(db, tid, command, action, line_id)
    if prior is not None: return prior
    if batch.status == "Cancelled": raise HTTPException(409, "This batch is cancelled")
    line = next((l for l in lines if l.id == line_id), None)
    if line is None: raise HTTPException(404, "Batch variant not found")
    active_article(db, line)
    if action == "Complete production":
        from app.modules.batches.workflow import style_complete
        if not style_complete(db, tid, line):
            raise HTTPException(409, "Complete this style's production stages first")
        if command.qty > line.planned - line.completed:
            raise HTTPException(409, "Completion exceeds planned quantity")
        line.completed += command.qty
    elif action == "QC":
        from app.modules.batches.workflow import style_complete
        if command.kind != "Inline" and not style_complete(db, tid, line):
            raise HTTPException(409, "Complete this style's production stages before final QC or reinspection")
        limit = line.held if command.kind == "Reinspection" else line.completed - line.approved - line.held - line.rejected
        if command.kind == "Inline": limit = line.planned
        if command.assessed > limit: raise HTTPException(409, "Assessed quantity exceeds the eligible QC lot")
        if command.kind != "Inline":
            if command.kind == "Reinspection": line.held -= command.assessed
            line.approved += command.approved; line.held += command.held; line.rejected += command.rejected
    elif action == "Receive approved stock":
        raise HTTPException(409, "Confirm an inbound shipment delivery in Shipments to receive batch stock")
    else: raise HTTPException(400, "Unknown quantity operation")
    db.flush(); refresh(db, tid, work)
    result = {"lineId": line.id}
    if action == "QC":
        from datetime import datetime, timezone
        from app.modules.quality import repository as quality_repo, aql as quality_aql
        source = db.get(PurchaseOrderLine, line.po_line_id)
        inspection = quality_repo.create_inspection(db, tenant_id=uid(tid),
            order_ref=work.order_no, item=" / ".join(filter(None, [source.name, source.color, source.size])),
            stage="In-line" if command.kind == "Inline" else "Final", aql="2.5",
            sku=source.sku, product=source.name, batch_lot=batch.name[:60],
            inspection_type=command.kind, prod_qty=command.assessed, inspector=actor)
        inspection.batch_line_id = line.id
        inspection.sample_size = command.sampleSize
        inspection.max_defects = quality_aql.acceptance_for_sample(command.sampleSize, inspection.aql)
        inspection.approved_qty = command.approved if command.kind != "Inline" else 0
        inspection.held_qty = command.held if command.kind != "Inline" else 0
        inspection.rejected_qty = command.rejected if command.kind != "Inline" else 0
        inspection.disposition_notes = command.reason
        inspection.result = "Pass" if command.kind == "Inline" or not (command.held or command.rejected) else "Fail"
        inspection.finalized_at = datetime.now(timezone.utc)
        db.flush()
        result["inspectionId"] = str(inspection.public_id)
        result["inspectionNo"] = inspection.inspection_no
    return end(db, tid, actor, command, action, payload, result, batch.id)


def active_article(db, line):
    from app.models.catalog import Product, ProductVariant
    variant = db.get(ProductVariant, line.variant_id)
    if variant is None or db.get(Product, variant.product_id).is_deleted:
        raise HTTPException(409, "A retired article cannot record production, QC or warehouse receipts.")


def update_sales(db, tid, order_id):
    order = db.get(SalesOrder, order_id)
    if order is None or order.tenant_id != uid(tid): return
    if order.status in ("Cancelled", "Inactive"): return
    dispatched = dispatched_sales(db, tid, order_id)
    total = sum(l.qty for l in fulfillment.lines_for(db, order))
    qty = sum(dispatched.values())
    order.status = "Shipped" if qty >= total else "Partially shipped" if qty else "Awaiting stock"
    db.flush()


def dispatched_sales(db, tid, order_id):
    query = select(PurchaseOrderLine.sales_order_line_id, func.sum(BatchAllocation.qty)).join(
        BatchLine, BatchLine.po_line_id == PurchaseOrderLine.id).join(BatchLot, BatchLot.line_id == BatchLine.id).join(
        BatchAllocation, BatchAllocation.lot_id == BatchLot.id).join(Shipment, Shipment.id == BatchAllocation.shipment_id).where(
        BatchAllocation.tenant_id == uid(tid), Shipment.tenant_id == uid(tid), Shipment.sales_order_id == order_id,
        BatchAllocation.status == "Dispatched").group_by(PurchaseOrderLine.sales_order_line_id)
    totals = dict(db.execute(query).all())
    for line_id, qty in db.execute(select(InventoryAllocation.order_line_id, func.sum(InventoryAllocation.qty))
            .join(Shipment, Shipment.id == InventoryAllocation.shipment_id).where(InventoryAllocation.tenant_id == uid(tid),
                Shipment.sales_order_id == order_id, InventoryAllocation.status == "Dispatched").group_by(InventoryAllocation.order_line_id)):
        totals[line_id] = totals.get(line_id, 0) + qty
    from app.modules.sales.ordinary_shipping import dispatched
    order = db.get(SalesOrder, order_id)
    if order and order.tenant_id == uid(tid):
        for line_id, qty in dispatched(db, order).items():
            totals[line_id] = totals.get(line_id, 0) + qty
    return totals


def inventory_sources(db, tid, order_id):
    order = db.get(SalesOrder, order_id)
    if order is None or order.tenant_id != uid(tid): return []
    result = []
    for hold in fulfillment.holds_for(db, order):
        level = db.get(StockLevel, hold.stock_level_id)
        source = db.get(SalesOrderLine, hold.order_line_id)
        batch_qty = db.scalar(select(func.coalesce(func.sum(BatchLot.on_hand), 0)).join(BatchLine, BatchLine.id == BatchLot.line_id)
            .join(PurchaseOrderLine, PurchaseOrderLine.id == BatchLine.po_line_id).where(BatchLot.tenant_id == uid(tid),
                BatchLot.location_id == level.location_id, BatchLine.variant_id == level.variant_id,
                PurchaseOrderLine.sales_order_line_id == hold.order_line_id))
        allocated = db.scalar(select(func.coalesce(func.sum(InventoryAllocation.qty), 0)).where(InventoryAllocation.tenant_id == uid(tid),
            InventoryAllocation.order_line_id == hold.order_line_id, InventoryAllocation.stock_level_id == level.id,
            InventoryAllocation.status == "Reserved"))
        available = max(0, hold.qty-batch_qty-allocated)
        if available:
            location = db.get(Location, level.location_id)
            if location is None or location.is_deleted: continue
            result.append({"reservationId": hold.id, "lineId": source.id, "variantId": level.variant_id,
                "name": source.name, "color": source.color, "size": source.size,
                "location": location.name, "available": available})
    return result


def create_shipment(db, tid, actor, order_id, kind, command):
    obj, works, pos = parent(db, tid, order_id, kind, lock=True)
    prior, payload = begin(db, tid, command, "Allocate shipment", order_id)
    if prior is not None: return prior
    batches = rows(db, ProductionBatch, tid, ProductionBatch.order_id.in_([w.id for w in works]), ProductionBatch.status != "Cancelled")
    if not batches:
        raise HTTPException(409, "Plan production batches before using batch shipments")
    if command.inventoryAllocations and kind != "sales":
        raise HTTPException(400, "Combine existing inventory and production stock from the sales order workspace")
    lines = {l.id: l for l in rows(db, BatchLine, tid, BatchLine.batch_id.in_([b.id for b in batches]))}
    lots = {l.id: l for l in rows(db, BatchLot, tid, BatchLot.line_id.in_(lines), lock=True)}
    if len({a.lotId for a in command.allocations}) != len(command.allocations):
        raise HTTPException(400, "Select each source lot once")
    for a in command.allocations:
        lot = lots.get(a.lotId)
        if not lot or a.qty > lot.on_hand - lot.allocated:
            raise HTTPException(409, "Source batch quantity is unavailable or already allocated")
        location = db.get(Location, lot.location_id)
        if location is None or location.is_deleted:
            raise HTTPException(409, "Transfer the batch stock to an active warehouse before allocating it")
    sales_ids = {p.sales_order_id for p in pos if p.sales_order_id}
    if kind == "sales": sales_ids = {obj.id}
    if len(sales_ids) > 1: raise HTTPException(400, "Ship one customer order at a time")
    sales_id = next(iter(sales_ids), None)
    stock_sources = {s["reservationId"]: s for s in inventory_sources(db, tid, sales_id)} if sales_id else {}
    if len({a.reservationId for a in command.inventoryAllocations}) != len(command.inventoryAllocations):
        raise HTTPException(400, "Select each inventory reservation once")
    for a in command.inventoryAllocations:
        source = stock_sources.get(a.reservationId)
        if not source or a.qty > source["available"]:
            raise HTTPException(409, "Existing inventory is unavailable or already allocated")
    if sales_id:
        order = db.get(SalesOrder, sales_id)
        remaining = {l.id: l.qty - dispatched_sales(db, tid, sales_id).get(l.id, 0) for l in fulfillment.lines_for(db, order)}
        # Pending allocations from every linked production order also consume the sales demand.
        for a in rows(db, BatchAllocation, tid, BatchAllocation.status == "Reserved"):
            ship = db.get(Shipment, a.shipment_id)
            if ship.sales_order_id != sales_id: continue
            src = db.get(PurchaseOrderLine, db.get(BatchLine, db.get(BatchLot, a.lot_id).line_id).po_line_id)
            remaining[src.sales_order_line_id] = remaining.get(src.sales_order_line_id, 0) - a.qty
        for a in command.allocations:
            source = db.get(PurchaseOrderLine, lines[lots[a.lotId].line_id].po_line_id)
            remaining[source.sales_order_line_id] = remaining.get(source.sales_order_line_id, 0) - a.qty
        for a in rows(db, InventoryAllocation, tid, InventoryAllocation.status == "Reserved"):
            if db.get(Shipment, a.shipment_id).sales_order_id == sales_id:
                remaining[a.order_line_id] = remaining.get(a.order_line_id, 0) - a.qty
        for a in command.inventoryAllocations:
            source = stock_sources[a.reservationId]
            remaining[source["lineId"]] -= a.qty
        if any(q < 0 for q in remaining.values()): raise HTTPException(409, "Shipment exceeds remaining customer demand")
    if not command.carrier.strip() or not command.destination.strip():
        raise HTTPException(400, "Enter carrier and destination")
    ship = Shipment(tenant_id=uid(tid), sales_order_id=sales_id, batch_managed=True,
        order_ref=db.get(SalesOrder, sales_id).order_no if sales_id else obj.order_no if kind == "production" else obj.po_no,
        carrier=command.carrier.strip(), destination=command.destination.strip(), eta=command.eta, status="Pending")
    db.add(ship); db.flush(); ship.shipment_no = f"SHP-{9900 + ship.id}"
    grouped = {}
    for a in command.allocations:
        from app.models.catalog import Product, ProductVariant
        candidate = lines[lots[a.lotId].line_id]
        if db.get(Product, db.get(ProductVariant, candidate.variant_id).product_id).is_deleted:
            raise HTTPException(409, "A retired article cannot be allocated to a shipment.")
        lot = lots[a.lotId]; line = lines[lot.line_id]; src = db.get(PurchaseOrderLine, line.po_line_id)
        key = (line.variant_id, src.sales_order_line_id)
        if key not in grouped:
            shipment_line = ShipmentLine(tenant_id=uid(tid), shipment_id=ship.id, sku=src.sku,
                description=" / ".join(v for v in [src.name, src.color, src.size] if v), qty=0)
            db.add(shipment_line); db.flush(); grouped[key] = shipment_line
        shipment_line = grouped[key]; shipment_line.qty += a.qty; lot.allocated += a.qty
        db.add(BatchAllocation(tenant_id=uid(tid), shipment_id=ship.id, shipment_line_id=shipment_line.id, lot_id=lot.id, qty=a.qty))
    for a in command.inventoryAllocations:
        source = stock_sources[a.reservationId]
        hold = db.get(OrderStockReservation, a.reservationId)
        key = (source["variantId"], source["lineId"])
        if key not in grouped:
            src = db.get(SalesOrderLine, source["lineId"])
            shipment_line = ShipmentLine(tenant_id=uid(tid), shipment_id=ship.id, sku=src.sku,
                description=" / ".join(v for v in [src.name, src.color, src.size] if v), qty=0)
            db.add(shipment_line); db.flush(); grouped[key] = shipment_line
        shipment_line = grouped[key]; shipment_line.qty += a.qty
        db.add(InventoryAllocation(tenant_id=uid(tid), shipment_id=ship.id, shipment_line_id=shipment_line.id,
            order_line_id=hold.order_line_id, stock_level_id=hold.stock_level_id, qty=a.qty))
    return end(db, tid, actor, command, "Allocate shipment", payload, {"publicId": str(ship.public_id), "shipmentNo": ship.shipment_no})


def is_batch_shipment(db, shipment):
    return shipment.batch_managed


def shipment_status(db, tid, public_id, status, actor=None):
    ship = one(db, Shipment, tid, public_id)
    allocations = rows(db, BatchAllocation, tid, BatchAllocation.shipment_id == ship.id)
    stock_allocations = rows(db, InventoryAllocation, tid, InventoryAllocation.shipment_id == ship.id)
    if not allocations and not stock_allocations: raise HTTPException(409, "This shipment does not have allocations")
    if ship.sales_order_id:
        parent(db, tid, db.get(SalesOrder, ship.sales_order_id).public_id, "sales", lock=True)
    order_ids = sorted({db.get(ProductionBatch, db.get(BatchLine, db.get(BatchLot, a.lot_id).line_id).batch_id).order_id for a in allocations})
    for order_id in order_ids:
        parent(db, tid, db.get(ProductionOrder, order_id).public_id, lock=True)
    ship = rows(db, Shipment, tid, Shipment.id == ship.id, lock=True)[0]
    if ship.status == status: return ship
    if ship.status in ("Cancelled", "Returned"):
        raise HTTPException(409, "A cancelled or returned shipment cannot be reopened")
    if status == "Returned":
        if stock_allocations:
            raise HTTPException(409, "Reconcile older inventory origins through the existing sales returns workflow; this shipment cannot use a batch-only return.")
        if not all(a.status == "Dispatched" for a in allocations):
            raise HTTPException(409, "Only dispatched pieces can be recorded as returned")
        for a in allocations:
            lot = db.get(BatchLot, a.lot_id)
            line = db.get(BatchLine, lot.line_id)
            if min(line.approved, line.received) < a.qty:
                raise HTTPException(409, "Reconcile batch quantities before recording the return")
            # Physically returned pieces are quarantined, not immediately saleable stock.
            line.approved -= a.qty; line.received -= a.qty; line.held += a.qty
            from app.modules.inventory import ledger
            from types import SimpleNamespace
            levels = rows(db, StockLevel, tid, StockLevel.variant_id == line.variant_id, StockLevel.location_id == lot.location_id, lock=True)
            level = levels[0] if levels else SimpleNamespace(tenant_id=uid(tid), variant_id=line.variant_id, location_id=lot.location_id, on_hand=0)
            ledger.record(db, level=level, before=level.on_hand, kind="Return to QC", quantity=a.qty,
                request_id=uuid5(ship.public_id, f"return-{a.id}"), reference=ship.shipment_no,
                reason="Returned pieces held for QC; warehouse on-hand is unchanged", actor=actor,
                source_module="shipments", source_type="shipment", source_id=ship.public_id,
                batch_id=db.get(ProductionBatch, line.batch_id).public_id)
            a.status = "Returned"
            batch = db.get(ProductionBatch, line.batch_id)
            work = db.get(ProductionOrder, batch.order_id)
            po = db.get(PurchaseOrder, work.purchase_order_id)
            po.inventory_received = False; po.status = "Partially received"
            db.flush(); refresh(db, tid, work)
        ship.status = "Returned"; db.flush()
        if ship.sales_order_id: update_sales(db, tid, ship.sales_order_id)
        return ship
    if ship.status == "Delivered":
        raise HTTPException(409, "A delivered shipment can only progress through a recorded return")
    sequence = ["Pending", "In transit", "Customs", "Out for delivery", "Delivered"]
    if status != "Cancelled" and (status not in sequence or sequence.index(status) < sequence.index(ship.status)):
        raise HTTPException(409, "Shipment status cannot move backwards")
    if status == "Cancelled" and any(a.status == "Dispatched" for a in allocations + stock_allocations):
        raise HTTPException(409, "Dispatched stock requires a recorded return; it cannot be cancelled")
    if ship.status == "Pending" and status not in ("In transit", "Cancelled"):
        raise HTTPException(409, "Confirm dispatch before recording delivery progress")
    lots = {l.id: l for l in rows(db, BatchLot, tid, BatchLot.id.in_([a.lot_id for a in allocations]), lock=True)}
    for a in allocations:
        if a.status != "Reserved": continue
        lot = lots[a.lot_id]; lot.allocated -= a.qty
        if status == "Cancelled": a.status = "Cancelled"; continue
        line = db.get(BatchLine, lot.line_id)
        level = rows(db, StockLevel, tid, StockLevel.variant_id == line.variant_id, StockLevel.location_id == lot.location_id, lock=True)
        if not level or level[0].on_hand < a.qty or level[0].reserved < a.qty or lot.on_hand < a.qty:
            raise HTTPException(409, "Reconcile source stock before dispatch")
        source = db.get(PurchaseOrderLine, line.po_line_id)
        hold = rows(db, OrderStockReservation, tid, OrderStockReservation.order_line_id == source.sales_order_line_id,
            OrderStockReservation.stock_level_id == level[0].id) if source.sales_order_line_id else []
        if hold:
            if hold[0].qty < a.qty: raise HTTPException(409, "Reconcile order reservations before dispatch")
            if hold[0].qty == a.qty: db.delete(hold[0])
            else: hold[0].qty -= a.qty
        before = level[0].on_hand
        level[0].on_hand -= a.qty; level[0].reserved -= a.qty; lot.on_hand -= a.qty; a.status = "Dispatched"
        from app.modules.inventory import ledger
        ledger.record(db, level=level[0], before=before, kind="Dispatch",
            request_id=uuid5(ship.public_id, f"dispatch-batch-{a.id}"), reference=ship.shipment_no,
            reason="Batch stock dispatched", actor=actor, source_module="shipments", source_type="shipment",
            source_id=ship.public_id, batch_id=db.get(ProductionBatch, line.batch_id).public_id)
    for a in stock_allocations:
        if a.status != "Reserved": continue
        if status == "Cancelled": a.status = "Cancelled"; continue
        level = rows(db, StockLevel, tid, StockLevel.id == a.stock_level_id, lock=True)[0]
        hold = rows(db, OrderStockReservation, tid, OrderStockReservation.order_line_id == a.order_line_id,
            OrderStockReservation.stock_level_id == a.stock_level_id)
        if not hold or min(level.on_hand, level.reserved, hold[0].qty) < a.qty:
            raise HTTPException(409, "Reconcile existing inventory reservations before dispatch")
        before = level.on_hand
        level.on_hand -= a.qty; level.reserved -= a.qty
        from app.modules.inventory import ledger
        ledger.record(db, level=level, before=before, kind="Dispatch",
            request_id=uuid5(ship.public_id, f"dispatch-stock-{a.id}"), reference=ship.shipment_no,
            reason="Existing stock dispatched", actor=actor, source_module="shipments", source_type="shipment", source_id=ship.public_id)
        if hold[0].qty == a.qty: db.delete(hold[0])
        else: hold[0].qty -= a.qty
        a.status = "Dispatched"
    ship.status = status; db.flush()
    if ship.sales_order_id: update_sales(db, tid, ship.sales_order_id)
    return ship


def transfer_lot(db, tid, actor, batch_id, lot_id, command):
    batch, work, lines = locked_batch(db, tid, batch_id)
    prior, payload = begin(db, tid, command, "Transfer batch stock", lot_id)
    if prior is not None: return prior
    source = one(db, BatchLot, tid, lot_id) if isinstance(lot_id, str) else next(iter(rows(db, BatchLot, tid, BatchLot.id == lot_id, lock=True)), None)
    if source is None or source.line_id not in {l.id for l in lines}: raise HTTPException(404, "Batch lot not found")
    destination = rows(db, Location, tid, Location.id == command.locationId, Location.is_deleted.is_(False))
    if not destination or destination[0].id == source.location_id: raise HTTPException(400, "Select a different active location")
    if command.qty > source.on_hand - source.allocated: raise HTTPException(409, "Allocated shipment stock cannot be transferred")
    line = next(l for l in lines if l.id == source.line_id)
    levels = rows(db, StockLevel, tid, StockLevel.variant_id == line.variant_id,
        StockLevel.location_id.in_([source.location_id, command.locationId]), lock=True)
    level = next((s for s in levels if s.location_id == source.location_id), None)
    if level is None or min(level.on_hand, level.reserved) < command.qty: raise HTTPException(409, "Source inventory needs reconciliation")
    from app.modules.inventory.repository import add_stock
    dest = add_stock(db, tenant_id=uid(tid), variant_id=line.variant_id, location_id=command.locationId, qty=command.qty)
    before = level.on_hand
    level.on_hand -= command.qty; level.reserved -= command.qty; dest.reserved += command.qty
    from app.modules.inventory import ledger
    ledger.record(db, level=level, before=before, kind="Transfer out", request_id=command.requestId,
        reference=f"{batch.name} transfer", reason=f"Batch stock transfer to {destination[0].name}", actor=actor,
        source_module="inventory", source_type="batch_transfer", source_id=batch.public_id, batch_id=batch.public_id)
    ledger.record(db, level=dest, before=dest.on_hand-command.qty, kind="Transfer in", request_id=command.requestId,
        reference=f"{batch.name} transfer", reason="Batch stock received from source location", actor=actor,
        source_module="inventory", source_type="batch_transfer", source_id=batch.public_id, batch_id=batch.public_id)
    source.on_hand -= command.qty
    targets = rows(db, BatchLot, tid, BatchLot.line_id == line.id, BatchLot.location_id == command.locationId)
    if targets: targets[0].on_hand += command.qty
    else: db.add(BatchLot(tenant_id=uid(tid), line_id=line.id, location_id=command.locationId, on_hand=command.qty))
    src = db.get(PurchaseOrderLine, line.po_line_id)
    holds = rows(db, OrderStockReservation, tid, OrderStockReservation.order_line_id == src.sales_order_line_id,
        OrderStockReservation.stock_level_id == level.id) if src.sales_order_line_id else []
    if holds:
        h = holds[0]
        if h.qty < command.qty: raise HTTPException(409, "Source reservation needs reconciliation")
        targets = rows(db, OrderStockReservation, tid, OrderStockReservation.order_line_id == src.sales_order_line_id,
            OrderStockReservation.stock_level_id == dest.id)
        if targets: targets[0].qty += command.qty
        else: db.add(OrderStockReservation(tenant_id=uid(tid), order_id=h.order_id, order_line_id=h.order_line_id,
            stock_level_id=dest.id, qty=command.qty))
        if h.qty == command.qty: db.delete(h)
        else: h.qty -= command.qty
    return end(db, tid, actor, command, "Transfer batch stock", payload, {"transferred": command.qty}, batch.id)


def cancel_batch(db, tid, actor, batch_id, command):
    batch, work, lines = locked_batch(db, tid, batch_id)
    prior, payload = begin(db, tid, command, "Cancel batch", batch_id)
    if prior is not None: return prior
    if any(l.completed for l in lines):
        raise HTTPException(409, "Completed pieces require QC, rework or scrap records; the batch cannot be erased")
    batch.status = "Cancelled"
    db.flush(); refresh(db, tid, work)
    return end(db, tid, actor, command, "Cancel batch", payload, {"status": "Cancelled"}, batch.id)


def view(db, tid, order_id, kind="production"):
    from app.modules.batches.workflow import ready, tna_complete, states
    obj, works, pos = parent(db, tid, order_id, kind)
    sources = rows(db, PurchaseOrderLine, tid, PurchaseOrderLine.po_id.in_([p.id for p in pos]))
    source_map = {s.id: s for s in sources}
    batches = rows(db, ProductionBatch, tid, ProductionBatch.order_id.in_([w.id for w in works]))
    lines = rows(db, BatchLine, tid, BatchLine.batch_id.in_([b.id for b in batches]))
    lots = rows(db, BatchLot, tid, BatchLot.line_id.in_([l.id for l in lines]))
    allocations = rows(db, BatchAllocation, tid, BatchAllocation.lot_id.in_([l.id for l in lots]))
    inbound = rows(db, InboundAllocation, tid, InboundAllocation.line_id.in_([l.id for l in lines]))
    shipments = {s.id: s for s in rows(db, Shipment, tid, Shipment.id.in_([a.shipment_id for a in allocations]))}
    shipments.update({s.id: s for s in rows(db, Shipment, tid, Shipment.id.in_([a.shipment_id for a in inbound]))})
    inventory = inventory_sources(db, tid, obj.id) if kind == "sales" else []
    if kind == "sales":
        for s in rows(db, Shipment, tid, Shipment.sales_order_id == obj.id, Shipment.batch_managed.is_(True), Shipment.is_deleted.is_(False)):
            shipments[s.id] = s
    stock_allocations = rows(db, InventoryAllocation, tid, InventoryAllocation.shipment_id.in_(shipments))
    locations = rows(db, Location, tid, Location.is_deleted.is_(False))
    location_map = {l.id: l.name for l in locations}
    stages = rows(db, BatchStage, tid, BatchStage.batch_id.in_([b.id for b in batches]))
    events = rows(db, BatchEvent, tid, BatchEvent.batch_id.in_([b.id for b in batches]))
    shipment_events = rows(db, BatchEvent, tid, BatchEvent.kind.in_(["Allocate shipment", "Plan inbound shipment", "Shipment status"]))
    def totals(items):
        ids = {l.id for l in items}; lot_ids = {l.id for l in lots if l.line_id in ids}
        return {**{k: sum(getattr(l, k) for l in items) for k in ("planned", "completed", "approved", "held", "rejected", "received")},
            "awaitingQC": sum(l.completed-l.approved-l.held-l.rejected for l in items),
            "allocated": sum(a.qty for a in allocations if a.lot_id in lot_ids and a.status == "Reserved"),
            "dispatched": sum(a.qty for a in allocations if a.lot_id in lot_ids and a.status == "Dispatched"),
            "delivered": sum(a.qty for a in allocations if a.lot_id in lot_ids and a.status == "Dispatched" and shipments[a.shipment_id].status == "Delivered"),
            "available": sum(l.on_hand-l.allocated for l in lots if l.line_id in ids),
            "readyToShip": sum(ready(db, tid, line) for line in items),
            "inboundPending": sum(a.qty for a in inbound if a.line_id in ids and a.status == "Reserved"),
            "inboundTransit": sum(a.qty for a in inbound if a.line_id in ids and a.status == "In transit"),
            "inboundDelivered": sum(a.accepted for a in inbound if a.line_id in ids and a.status == "Delivered")}
    def line_view(line):
        from app.modules.batches.workflow import style_complete
        src = source_map[line.po_line_id]
        production_line = db.get(ProductionOrderLine, src.production_line_id) if src.production_line_id else None
        return {"id": line.id, "sourceId": src.id, "productionLineId": src.production_line_id,
            "productionLinePublicId": str(production_line.public_id) if production_line else None,
            "tnaComplete": style_complete(db, tid, line),
            "name": src.name, "color": src.color, "size": src.size,
            "variantId": line.variant_id, "salesOrderLineId": src.sales_order_line_id,
            **totals([line]), "lots": [{"id": l.id, "location": location_map.get(l.location_id, "Inactive location"),
                "onHand": l.on_hand, "allocated": l.allocated, "available": l.on_hand-l.allocated} for l in lots if l.line_id == line.id]}
    lot_map = {l.id: l for l in lots}; line_map = {l.id: l for l in lines}; batch_map = {b.id: b for b in batches}
    active_ids = {b.id for b in batches if b.status != "Cancelled"}
    summary = totals([l for l in lines if l.batch_id in active_ids])
    if kind == "sales":
        summary["available"] += sum(s["available"] for s in inventory)
        summary["allocated"] += sum(a.qty for a in stock_allocations if a.status == "Reserved")
        summary["dispatched"] += sum(a.qty for a in stock_allocations if a.status == "Dispatched")
        summary["delivered"] += sum(a.qty for a in stock_allocations if a.status == "Dispatched" and shipments[a.shipment_id].status == "Delivered")
    summary["ordered"] = sum(l.qty for l in fulfillment.lines_for(db, obj)) if kind == "sales" else sum(s.qty for s in sources)
    summary["remaining"] = max(0, summary["ordered"] - summary["dispatched"])
    return {"ref": obj.order_no if kind != "procurement" else obj.po_no, "summary": summary, "inventorySources": inventory,
        "sources": [{"id": s.id, "name": s.name, "color": s.color, "size": s.size, "qty": s.qty,
            "remainingToPlan": s.qty + sum(l.rejected-l.planned for l in lines if l.po_line_id == s.id and l.batch_id in active_ids)} for s in sources],
        "productionOrders": [{"publicId": str(w.public_id), "ref": w.order_no} for w in works],
        "locations": [{"id": l.id, "name": l.name} for l in locations],
        "batches": [{"publicId": str(b.public_id), "name": b.name, "factory": b.factory, "dueOn": b.due_on,
            "orderRef": next(w.order_no for w in works if w.id == b.order_id), "status": b.status,
            **states(db, tid, b, [l for l in lines if l.batch_id == b.id]),
            "poRef": next(p.po_no for p in pos if p.id == next(w.purchase_order_id for w in works if w.id == b.order_id)),
            "tnaComplete": tna_complete(db, tid, b.id),
            "tnaProgress": round(100 * sum(s.status in ("Completed", "Received") for s in stages if s.batch_id == b.id) / max(1, sum(s.batch_id == b.id for s in stages))),
            "summary": totals([l for l in lines if l.batch_id == b.id]),
            "lines": [line_view(l) for l in lines if l.batch_id == b.id],
            "stages": [{"id": s.id, "productionLineId": s.production_line_id,
                "name": s.name, "status": s.status, "worker": s.worker, "notes": s.notes,
                "startOn": s.start_on.isoformat() if s.start_on else None, "endOn": s.end_on.isoformat() if s.end_on else None,
                "durationDays": s.duration_days, "actualStartOn": s.actual_start_on.isoformat() if s.actual_start_on else None,
                "actualEndOn": s.actual_end_on.isoformat() if s.actual_end_on else None,
                "overdue": bool(s.end_on and s.end_on < date.today() and s.status not in ("Completed", "Received"))}
                for s in sorted(stages, key=lambda stage: stage.seq) if s.batch_id == b.id],
            "history": [{"kind": e.kind, "actor": e.actor, "at": e.created_at.isoformat(), "details": json.loads(e.payload),
                "inspectionId": json.loads(e.result).get("inspectionId")} for e in events if e.batch_id == b.id]} for b in batches],
        "shipments": [{"publicId": s.public_id.__str__(), "ref": s.shipment_no, "status": s.status, "direction": s.direction,
            "history": [{"kind": e.kind, "actor": e.actor, "at": e.created_at.isoformat(), "reason": json.loads(e.payload).get("reason")}
                for e in shipment_events if json.loads(e.result).get("publicId") == str(s.public_id) or json.loads(e.payload).get("scope") == str(s.public_id)],
            "carrier": s.carrier, "destination": s.destination, "qty": sum(a.qty for a in allocations+stock_allocations+inbound if a.shipment_id == s.id),
            "sources": [{"allocationId": a.id, "batch": batch_map[line_map[lot_map[a.lot_id].line_id].batch_id].name,
                "batchId": str(batch_map[line_map[lot_map[a.lot_id].line_id].batch_id].public_id),
                "item": source_map[line_map[lot_map[a.lot_id].line_id].po_line_id].name,
                "color": source_map[line_map[lot_map[a.lot_id].line_id].po_line_id].color,
                "size": source_map[line_map[lot_map[a.lot_id].line_id].po_line_id].size,
                "location": location_map.get(lot_map[a.lot_id].location_id), "qty": a.qty, "status": a.status}
                for a in allocations if a.shipment_id == s.id] + [{"allocationId": -a.id, "batch": "Previously received inventory",
                    "batchId": None, "item": db.get(SalesOrderLine, a.order_line_id).name,
                    "color": db.get(SalesOrderLine, a.order_line_id).color, "size": db.get(SalesOrderLine, a.order_line_id).size,
                    "location": location_map.get(db.get(StockLevel, a.stock_level_id).location_id), "qty": a.qty, "status": a.status}
                    for a in stock_allocations if a.shipment_id == s.id] + [{"allocationId": a.id,
                        "batch": batch_map[line_map[a.line_id].batch_id].name,
                        "batchId": str(batch_map[line_map[a.line_id].batch_id].public_id),
                        "item": source_map[line_map[a.line_id].po_line_id].name,
                        "color": source_map[line_map[a.line_id].po_line_id].color,
                        "size": source_map[line_map[a.line_id].po_line_id].size,
                        "location": location_map.get(a.location_id), "qty": a.qty, "accepted": a.accepted, "status": a.status}
                        for a in inbound if a.shipment_id == s.id]} for s in shipments.values()]}


def work_list(db, tid, kind):
    from app.modules.batches.workflow import ready, tna_complete, states
    batches = rows(db, ProductionBatch, tid, ProductionBatch.status != "Cancelled")
    result = []
    for b in batches:
        work = db.get(ProductionOrder, b.order_id)
        if work is None or work.tenant_id != uid(tid) or work.is_deleted: continue
        lines = rows(db, BatchLine, tid, BatchLine.batch_id == b.id)
        po = db.get(PurchaseOrder, work.purchase_order_id)
        if not po or po.is_deleted or po.status in ("Cancelled", "Rejected", "Inactive"): continue
        result.append({"publicId": str(b.public_id), "name": b.name, "orderId": str(work.public_id),
            **states(db, tid, b, lines),
            "orderRef": work.order_no, "status": b.status, "factory": b.factory, "dueOn": b.due_on,
            "poRef": po.po_no, "tnaComplete": tna_complete(db, tid, b.id),
            "readyToShip": sum(ready(db, tid, l) for l in lines), "received": sum(l.received for l in lines),
            "planned": sum(l.planned for l in lines), "completed": sum(l.completed for l in lines),
            "awaitingQC": sum(l.completed-l.approved-l.held-l.rejected for l in lines), "held": sum(l.held for l in lines)})
    return {"batches": result}


def receive_stock(db, tid, work, line, qty, location_id, actor=None, request_id=None):
    if qty > line.approved - line.received:
        raise HTTPException(409, "Receipt exceeds approved, unreceived pieces")
    location = rows(db, Location, tid, Location.id == location_id, Location.is_deleted.is_(False))
    if not location: raise HTTPException(400, "Select an active warehouse location")
    from app.modules.inventory.repository import add_stock
    level = add_stock(db, tenant_id=uid(tid), variant_id=line.variant_id, location_id=location[0].id, qty=qty)
    # All lot stock is earmarked for this production demand, including before shipment allocation.
    level.reserved += qty
    source = db.get(PurchaseOrderLine, line.po_line_id)
    po = db.get(PurchaseOrder, work.purchase_order_id)
    if po.sales_order_id and source.sales_order_line_id:
        hold = rows(db, OrderStockReservation, tid, OrderStockReservation.order_line_id == source.sales_order_line_id,
            OrderStockReservation.stock_level_id == level.id)
        if hold: hold[0].qty += qty
        else: db.add(OrderStockReservation(tenant_id=uid(tid), order_id=po.sales_order_id,
            order_line_id=source.sales_order_line_id, stock_level_id=level.id, qty=qty))
    lots = rows(db, BatchLot, tid, BatchLot.line_id == line.id, BatchLot.location_id == location[0].id)
    if lots: lots[0].on_hand += qty
    else: db.add(BatchLot(tenant_id=uid(tid), line_id=line.id, location_id=location[0].id, on_hand=qty))
    line.received += qty
    receipt = GoodsReceipt(tenant_id=uid(tid), po_ref=po.po_no, supplier_name=po.supplier_name,
        line_count=1, received_count=qty, status="Received", location=location[0].name)
    db.add(receipt); db.flush(); receipt.grn_no = f"GRN-{3000 + receipt.id}"
    from app.modules.inventory import ledger
    ledger.record(db, level=level, before=level.on_hand-qty, kind="Receipt",
        request_id=request_id, reference=receipt.grn_no, reason="Approved production batch physically received",
        actor=actor, source_module="procurement", source_type="receipt", source_id=receipt.public_id,
        batch_id=db.get(ProductionBatch, line.batch_id).public_id)
    db.add(GoodsReceiptLine(tenant_id=uid(tid), grn_id=receipt.id, name=source.name,
        sku=source.sku, ordered=source.qty, received=qty))
    all_sources = rows(db, PurchaseOrderLine, tid, PurchaseOrderLine.po_id == po.id)
    all_lines = rows(db, BatchLine, tid, BatchLine.po_line_id.in_([s.id for s in all_sources]))
    if sum(l.received for l in all_lines) >= sum(s.qty for s in all_sources):
        po.inventory_received = True
        po.status = "Received"
    else:
        po.status = "Partially received"
    db.flush()

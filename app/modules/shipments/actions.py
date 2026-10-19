"""Shared shipment preparation and the actions that move a shipment forward."""
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.audit import write_audit
from app.models.batches import BatchEvent, BatchLine, BatchLot, InboundAllocation, ProductionBatch
from app.models.inventory import OrderStockReservation
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.production import ProductionOrder
from app.models.sales import SalesOrder, SalesOrderLine
from app.models.shipments import Shipment, ShipmentLine
from app.modules.shipments.workspace import display


class SourceLine(BaseModel):
    key: str = Field(min_length=3, max_length=80)
    quantity: int = Field(ge=0)


class QtyLine(BaseModel):
    lineId: int = Field(gt=0)
    quantity: int = Field(ge=0)


class PrepareBody(BaseModel):
    requestId: UUID
    direction: str = Field(pattern="^(Incoming|Outgoing)$")
    origin: str = Field(default="", max_length=160)
    destination: str = Field(default="", max_length=160)
    carrier: str = Field(default="", max_length=120)
    dispatchOn: str = Field(default="", max_length=40)
    eta: str = Field(default="", max_length=40)
    tracking: str = Field(default="", max_length=120)
    reason: str = Field(min_length=3, max_length=400)
    locationId: int | None = None
    lines: list[SourceLine] = Field(min_length=1)


class ActionBody(BaseModel):
    requestId: UUID
    action: str = Field(pattern="^(prepare|pack|dispatch|tracking|cancel|deliver|resolve)$")
    reason: str = Field(min_length=3, max_length=400)
    origin: str = Field(default="", max_length=160)
    destination: str = Field(default="", max_length=160)
    carrier: str = Field(default="", max_length=120)
    dispatchOn: str = Field(default="", max_length=40)
    eta: str = Field(default="", max_length=40)
    tracking: str = Field(default="", max_length=120)
    trackingStatus: str = Field(default="", max_length=24)
    lines: list[QtyLine] = Field(default_factory=list)
    allocationId: int | None = None


def _remembered(session, tid, request_id, kinds):
    event = session.scalar(select(BatchEvent).where(BatchEvent.tenant_id == tid, BatchEvent.request_id == request_id))
    if event is None:
        return None
    if event.kind not in kinds:
        raise HTTPException(409, "This request ID was already used for a different operation")
    return json.loads(event.result) if event.result else None


def _ship(session, tid, public_id, lock=False):
    query = select(Shipment).where(Shipment.tenant_id == tid, Shipment.public_id == UUID(str(public_id)),
        Shipment.is_deleted.is_(False))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    ship = session.scalar(query)
    if ship is None:
        raise HTTPException(404, "Shipment not found")
    return ship


def _lines(session, tid, ship):
    return list(session.scalars(select(ShipmentLine).where(ShipmentLine.tenant_id == tid,
        ShipmentLine.shipment_id == ship.id).order_by(ShipmentLine.id)))


def _result(ship):
    return {"publicId": str(ship.public_id), "shipmentNo": ship.shipment_no, "status": display(ship)}


def _require_transport(payload):
    if not payload.origin.strip() or not payload.destination.strip() or not payload.carrier.strip():
        raise HTTPException(422, "Enter the origin, destination and carrier.")


def _stamp(ship, payload):
    _require_transport(payload)
    ship.origin = payload.origin.strip()
    ship.destination = payload.destination.strip()
    ship.carrier = payload.carrier.strip()
    ship.dispatch_on = payload.dispatchOn.strip() or None
    ship.eta = payload.eta.strip() or None
    ship.tracking_ref = payload.tracking.strip() or None


def _finish(session, tid, actor, payload, ship):
    from app.modules.inventory import workflows as flow
    session.flush()
    flow.event(session, tid, ship.public_id, "Prepare shipment", payload, actor or "user", snapshot=_result(ship))
    return _result(ship)


def _prepare_incoming_batch(session, tid, actor, payload, chosen):
    from app.modules.batches.dto import CreateInbound, InboundSource
    from app.modules.batches import workflow
    if not payload.locationId:
        raise HTTPException(422, "Select the destination warehouse.")
    allocations = [InboundSource(lineId=line_id, locationId=payload.locationId, qty=qty)
        for line_id, qty in chosen.items() if qty]
    if not allocations:
        raise HTTPException(422, "Enter a quantity to ship.")
    command = CreateInbound(requestId=payload.requestId, reason=payload.reason, carrier=payload.carrier.strip(),
        eta=payload.eta.strip(), allocations=allocations)
    result = workflow.plan_consolidated(session, tid, actor, command)
    ship = _ship(session, tid, result["publicId"])
    if ship.status == "Pending":
        ship.origin = payload.origin.strip()
        ship.destination = payload.destination.strip()
        ship.dispatch_on = payload.dispatchOn.strip() or None
        ship.eta = payload.eta.strip() or ship.eta
        ship.tracking_ref = payload.tracking.strip() or None
    session.flush()
    return _result(ship)


def _prepare_incoming_ordinary(session, tid, actor, payload, groups):
    from app.modules.quality.workflow import supplier_release
    from app.modules.shipments import supplier_inbound
    if len(groups) != 1:
        raise HTTPException(409, "Each supplier purchase order keeps its own shipment.")
    po_id, quantities = next(iter(groups.items()))
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.public_id == UUID(po_id),
        PurchaseOrder.tenant_id == tid, PurchaseOrder.is_deleted.is_(False)).with_for_update())
    if po is None:
        raise HTTPException(404, "Purchase order not found")
    if po.status not in ("Approved", "Partially received", "Received"):
        raise HTTPException(409, "The supplier purchase order must be approved before its goods can be shipped.")
    release = {line["poLineId"]: line["qty"] for line in supplier_release(session, po)["lines"]}
    if set(quantities) != set(release) or any(quantities[line_id] != release[line_id] for line_id in release):
        raise HTTPException(409, "This supplier shipment includes the full QC-released quantity.")
    ship = supplier_inbound.ensure(session, po)
    if ship is None:
        raise HTTPException(409, "Complete production TNA and final QC before preparing this shipment.")
    if ship.dispatched_at:
        raise HTTPException(409, "This supplier shipment has already been dispatched.")
    _stamp(ship, payload)
    session.flush()
    return _finish(session, tid, actor, payload, ship)


def _prepare_outgoing_ordinary(session, tid, actor, payload, groups):
    from app.modules.sales import fulfillment
    from app.modules.sales.ordinary_shipping import refresh
    if len(groups) != 1:
        raise HTTPException(409, "Prepare one customer order at a time.")
    order_id, quantities = next(iter(groups.items()))
    order = fulfillment.lock_order(session, tenant_id=tid, public_id=UUID(order_id))
    if order.allow_partial_shipments is False and any(qty <= 0 for qty in quantities.values()):
        raise HTTPException(409, "This order must ship complete. Include every remaining unit.")
    ship = refresh(session, order)
    if ship is None:
        raise HTTPException(409, "This order is not ready to ship. Reserved stock is still outstanding or already on a shipment.")
    if ship.packed_at or ship.inventory_dispatched:
        raise HTTPException(409, "Packing is already confirmed. Open the shipment to dispatch it.")
    summary = {row["lineId"]: row for row in fulfillment.stock_summary(session, order)}
    expected = {line_id for line_id, row in summary.items() if min(row["reserved"], row["outstanding"])}
    if any(line_id not in summary for line_id in quantities):
        raise HTTPException(409, "Reload the order. Its shippable lines have changed.")
    if not order.allow_partial_shipments and (set(quantities) != expected or any(
            quantities[line_id] != min(summary[line_id]["reserved"], summary[line_id]["outstanding"]) for line_id in expected)):
        raise HTTPException(409, "This order must ship complete. Include every remaining unit.")
    for line_id, qty in quantities.items():
        row = summary[line_id]
        if qty > min(row["reserved"], row["outstanding"]):
            raise HTTPException(409, "Quantity exceeds the reserved stock for this order.")
    if order.allow_partial_shipments:
        for old in _lines(session, tid, ship):
            session.delete(old)
        session.flush()
        source = {line.id: line for line in fulfillment.lines_for(session, order)}
        for line_id, qty in quantities.items():
            if not qty:
                continue
            line = source[line_id]
            session.add(ShipmentLine(tenant_id=tid, shipment_id=ship.id, order_line_id=line.id, sku=line.sku,
                description=" · ".join(v for v in [line.name, line.color, line.size] if v), qty=qty))
    _stamp(ship, payload)
    session.flush()
    return _finish(session, tid, actor, payload, ship)


def _prepare_outgoing_batch(session, tid, actor, payload, lots, reservations):
    from app.modules.batches.dto import AllocationIn, CreateShipment, InventoryIn
    from app.modules.batches import service as batches
    sales_ids = set()
    for lot_id in lots:
        lot = session.get(BatchLot, lot_id)
        line = session.get(BatchLine, lot.line_id) if lot else None
        source = session.get(PurchaseOrderLine, line.po_line_id) if line else None
        po = session.get(PurchaseOrder, source.po_id) if source else None
        if po is None or not po.sales_order_id:
            raise HTTPException(409, "Reserve this warehouse stock to a customer order before shipping it.")
        sales_ids.add(po.sales_order_id)
    for reservation_id in reservations:
        hold = session.get(OrderStockReservation, reservation_id)
        if hold is None or hold.tenant_id != tid:
            raise HTTPException(404, "Reserved stock was not found.")
        sales_ids.add(hold.order_id)
    if len(sales_ids) != 1:
        raise HTTPException(409, "Ship one customer delivery at a time.")
    order = session.get(SalesOrder, next(iter(sales_ids)))
    command = CreateShipment(requestId=payload.requestId, reason=payload.reason, carrier=payload.carrier.strip(),
        destination=payload.destination.strip(), eta=payload.eta.strip(),
        allocations=[AllocationIn(lotId=lot_id, qty=qty) for lot_id, qty in lots.items() if qty],
        inventoryAllocations=[InventoryIn(reservationId=reservation_id, qty=qty) for reservation_id, qty in reservations.items() if qty])
    result = batches.create_shipment(session, tid, actor, str(order.public_id), "sales", command)
    ship = _ship(session, tid, result["publicId"])
    if ship.status == "Pending":
        ship.origin = payload.origin.strip()
        ship.dispatch_on = payload.dispatchOn.strip() or None
        ship.tracking_ref = payload.tracking.strip() or None
        ship.eta = payload.eta.strip() or ship.eta
    session.flush()
    return _result(ship)


def prepare(session, tid, actor, payload: PrepareBody):
    remembered = _remembered(session, tid, payload.requestId, {"Plan inbound shipment", "Allocate shipment"})
    if remembered:
        return remembered
    from app.modules.inventory import workflows as flow
    existing = flow.replay(session, tid, payload.requestId, "Prepare shipment", payload)
    if existing:
        return json.loads(existing.payload).get("snapshot")
    _require_transport(payload)
    parsed = []
    for line in payload.lines:
        parts = line.key.split(":")
        if parts[0] not in ("bin", "oin", "oout", "bout", "sout") or len(parts) < 2:
            raise HTTPException(422, "Choose goods from the ready-to-ship list.")
        parsed.append((parts, line.quantity))
    kinds = {parts[0] for parts, _ in parsed}
    if kinds <= {"bout", "sout"}:
        kind = "batch-out"
    elif len(kinds) != 1:
        raise HTTPException(409, "Incoming supplier goods and outgoing customer goods stay on separate shipments.")
    else:
        kind = next(iter(kinds))
    direction = "Incoming" if kind in ("bin", "oin") else "Outgoing"
    if payload.direction != direction:
        raise HTTPException(409, "The selected goods do not match the shipment direction.")
    if kind == "bin":
        return _prepare_incoming_batch(session, tid, actor, payload, {int(parts[1]): qty for parts, qty in parsed})
    if kind == "oin":
        groups = {}
        for parts, qty in parsed:
            if len(parts) < 3:
                raise HTTPException(422, "Choose the articles on this purchase order.")
            groups.setdefault(parts[1], {})[int(parts[2])] = qty
        return _prepare_incoming_ordinary(session, tid, actor, payload, groups)
    if kind == "oout":
        groups = {}
        for parts, qty in parsed:
            if len(parts) < 3:
                raise HTTPException(422, "Choose the articles on this customer order.")
            groups.setdefault(parts[1], {})[int(parts[2])] = qty
        return _prepare_outgoing_ordinary(session, tid, actor, payload, groups)
    lots, reservations = {}, {}
    for parts, qty in parsed:
        if parts[0] == "bout":
            lots[int(parts[1])] = qty
        else:
            reservations[int(parts[1])] = qty
    return _prepare_outgoing_batch(session, tid, actor, payload, lots, reservations)


def _actor_name(actor):
    return actor or "user"


def state(session, tid, public_id):
    ship = _ship(session, tid, public_id)
    lines = _lines(session, tid, ship)
    incoming = ship.direction == "Inbound"
    dispatched = bool(ship.dispatched_at or ship.inventory_dispatched or ship.status in ("Dispatched", "In transit", "Customs", "Out for delivery", "Delivered"))
    actions = []
    def add(action, label, blocked, reason):
        actions.append({"id": action, "label": label, "blocked": blocked, "reason": reason})
    pending = ship.status in ("Pending", "Label created")
    add("prepare", "Save transport details", not pending or dispatched, None if pending and not dispatched else "Transport details stay as recorded after dispatch.")
    if not incoming:
        add("pack", "Confirm packing", bool(ship.packed_at) or dispatched or ship.status == "Cancelled",
            "Packing is already confirmed." if ship.packed_at else "Confirm packing before dispatch." if pending else "This shipment can no longer be packed.")
    add("dispatch", "Confirm dispatch", not pending or ship.status == "Cancelled" or (not incoming and not ship.packed_at),
        None if pending and (incoming or ship.packed_at) else "Confirm packing before dispatch." if not incoming and not ship.packed_at else "Dispatch is already recorded.")
    add("tracking", "Update tracking", not dispatched or ship.status in ("Delivered", "Cancelled", "Returned"),
        "Record dispatch before adding a tracking event.")
    if incoming:
        add("receive", "Open warehouse receiving", ship.status not in ("Dispatched", "In transit", "Customs", "Out for delivery"),
            "Dispatch the supplier shipment before receiving it." if not dispatched else None)
    else:
        add("deliver", "Confirm customer delivery", not dispatched or ship.status in ("Delivered", "Cancelled", "Returned") or ship.legacy_ambiguous,
            "This delivery was recorded before arrival quantities existed. Reconcile it before adding quantities." if ship.legacy_ambiguous else "Dispatch the shipment before recording delivery.")
    add("cancel", "Cancel preparation", not pending or dispatched, "Goods already dispatched need a return or reconciliation, not a cancellation.")
    open_gap = any((line.discrepancy_qty or 0) > 0 and line.discrepancy_status == "Open" for line in lines)
    inbound_gaps = session.scalars(select(InboundAllocation).where(InboundAllocation.tenant_id == tid,
        InboundAllocation.shipment_id == ship.id, InboundAllocation.discrepancy_status == "Open")).all()
    if open_gap or inbound_gaps:
        add("resolve", "Resolve discrepancy", False, "Record what happened to the missing units. This does not restock, scrap or refund them.")
    return {
        "id": str(ship.public_id), "ref": ship.shipment_no, "direction": "Incoming" if incoming else "Outgoing",
        "batchManaged": bool(ship.batch_managed),
        "status": ship.status, "label": display(ship), "legacy": bool(ship.legacy_ambiguous),
        "transport": {"origin": ship.origin or "", "destination": ship.destination or "", "carrier": ship.carrier or "",
            "dispatchOn": ship.dispatch_on or "", "eta": ship.eta or "", "tracking": ship.tracking_ref or ""},
        "receivingHref": "/inventory/receiving" if incoming else None,
        "lines": [{"lineId": line.id, "orderLineId": line.order_line_id, "name": line.description, "sku": line.sku, "qty": line.qty,
            "delivered": line.delivered_qty, "discrepancy": line.discrepancy_qty or 0,
            "discrepancyStatus": line.discrepancy_status} for line in lines],
        "inbound": [{"allocationId": row.id, "qty": row.qty, "accepted": row.accepted,
            "discrepancy": row.discrepancy_qty or 0, "discrepancyStatus": row.discrepancy_status} for row in inbound_gaps],
        "actions": actions,
    }


def _save_transport(ship, payload):
    if ship.dispatched_at or ship.inventory_dispatched:
        raise HTTPException(409, "Carrier, origin and destination cannot change after dispatch.")
    _stamp(ship, payload)


def _dispatch(session, tid, actor, ship, payload):
    from app.modules.batches.dto import ShipmentStatus
    from app.modules.batches import service as batches
    from app.modules.batches import workflow
    command = ShipmentStatus(requestId=payload.requestId, reason=payload.reason,
        status="In transit" if ship.batch_managed else "Dispatched")
    if ship.batch_managed and ship.direction == "Inbound":
        return workflow.inbound_status(session, tid, actor, ship, command)
    if ship.batch_managed:
        if ship.sales_order_id:
            batches.parent(session, tid, session.get(SalesOrder, ship.sales_order_id).public_id, "sales", lock=True)
        allocations = batches.rows(session, batches.BatchAllocation, tid, batches.BatchAllocation.shipment_id == ship.id)
        for allocation in allocations:
            lot = session.get(BatchLot, allocation.lot_id)
            line = session.get(BatchLine, lot.line_id)
            batch = session.get(ProductionBatch, line.batch_id)
            batches.locked_batch(session, tid, batch.public_id)
        prior, stored = batches.begin(session, tid, command, "Shipment status", ship.public_id)
        if prior is not None:
            return prior
        updated = batches.shipment_status(session, tid, ship.public_id, "In transit", actor=actor)
        return batches.end(session, tid, actor, command, "Shipment status", stored, {"publicId": str(updated.public_id), "status": updated.status})
    from app.modules.shipments.service import _transition
    updated = _transition(session, public_id=str(ship.public_id), status="Dispatched", actor=actor)
    return _result(updated)


def _deliver(session, tid, actor, ship, payload):
    if ship.direction == "Inbound":
        raise HTTPException(409, "Confirm physical arrival in Inventory → Warehouse receiving.")
    if ship.legacy_ambiguous:
        raise HTTPException(409, "This historical delivery has no arrival quantities. Reconcile it instead of guessing them.")
    if not (ship.dispatched_at or ship.inventory_dispatched or ship.status in ("Dispatched", "In transit", "Customs", "Out for delivery")):
        raise HTTPException(409, "Dispatch the shipment before recording customer delivery.")
    if ship.status in ("Delivered", "Cancelled", "Returned"):
        raise HTTPException(409, "This shipment is already closed.")
    lines = _lines(session, tid, ship)
    submitted = {line.lineId: line.quantity for line in payload.lines}
    if set(submitted) != {line.id for line in lines}:
        raise HTTPException(422, "Enter the delivered quantity for every shipment line.")
    for line in lines:
        arrived = submitted[line.id]
        if arrived > line.qty:
            raise HTTPException(409, "Delivered quantity cannot exceed the dispatched quantity.")
        line.delivered_qty = arrived
        line.discrepancy_qty = line.qty - arrived
        line.discrepancy_status = "Open" if line.discrepancy_qty else None
    ship.status = "Delivered"
    ship.delivered_at = datetime.now(timezone.utc)
    write_audit(session, tenant_id=tid, action="DELIVER", entity_type="shipment", entity_id=str(ship.public_id),
        detail=f"{ship.shipment_no}: customer delivery recorded; {payload.reason}; {_actor_name(actor)}")
    session.flush()
    return _result(ship)


def _resolve(session, tid, actor, ship, payload):
    when = datetime.now(timezone.utc)
    found = False
    if payload.allocationId:
        allocation = session.get(InboundAllocation, payload.allocationId)
        if allocation is None or allocation.shipment_id != ship.id or allocation.tenant_id != tid:
            raise HTTPException(404, "Discrepancy not found on this shipment.")
        if allocation.discrepancy_status != "Open":
            raise HTTPException(409, "This discrepancy is already resolved.")
        allocation.discrepancy_status = "Resolved"
        allocation.discrepancy_reason = payload.reason.strip()
        allocation.discrepancy_resolved_by = _actor_name(actor)
        allocation.discrepancy_resolved_at = when
        found = True
    for line in payload.lines:
        row = session.get(ShipmentLine, line.lineId)
        if row is None or row.shipment_id != ship.id or row.tenant_id != tid:
            raise HTTPException(404, "Discrepancy not found on this shipment.")
        if row.discrepancy_status != "Open":
            raise HTTPException(409, "This discrepancy is already resolved.")
        row.discrepancy_status = "Resolved"
        row.discrepancy_reason = payload.reason.strip()
        row.discrepancy_resolved_by = _actor_name(actor)
        row.discrepancy_resolved_at = when
        found = True
    if not found:
        raise HTTPException(422, "Choose the unresolved quantity to close.")
    write_audit(session, tenant_id=tid, action="RECONCILE", entity_type="shipment", entity_id=str(ship.public_id),
        detail=f"{ship.shipment_no}: discrepancy recorded without restock, scrap or refund; {payload.reason}; {_actor_name(actor)}")
    session.flush()
    return _result(ship)


def perform(session, tid, actor, public_id, payload: ActionBody):
    remembered = _remembered(session, tid, payload.requestId, {"Shipment status"})
    if remembered and payload.action in ("dispatch", "cancel"):
        return remembered
    from app.modules.inventory import workflows as flow
    existing = flow.replay(session, tid, payload.requestId, f"Shipment {payload.action}", payload)
    if existing:
        return json.loads(existing.payload).get("snapshot")
    preview = session.scalar(select(Shipment).where(Shipment.tenant_id == tid,
        Shipment.public_id == UUID(str(public_id)), Shipment.is_deleted.is_(False)))
    if preview and preview.sales_order_id:
        from app.modules.sales import fulfillment
        fulfillment.lock_order(session, tenant_id=tid, order_id=preview.sales_order_id)
    ship = _ship(session, tid, public_id, lock=True)
    if payload.action == "prepare":
        _save_transport(ship, payload)
        result = _result(ship)
    elif payload.action == "pack":
        if ship.direction == "Inbound":
            raise HTTPException(409, "Incoming supplier shipments are packed by the supplier. Confirm dispatch when they leave.")
        if ship.batch_managed:
            if ship.packed_at:
                raise HTTPException(409, "Packing is already confirmed.")
            if not _lines(session, tid, ship):
                raise HTTPException(409, "This shipment has no quantities to pack.")
            ship.packed_at = datetime.now(timezone.utc)
            ship.packed_by = _actor_name(actor)
            result = _result(ship)
        else:
            from app.modules.sales import fulfillment
            from app.modules.sales import ordinary_shipping
            from app.modules.shipments.packing import PackInput, PackLine
            if not ship.sales_order_id:
                raise HTTPException(409, "Link this shipment to its customer order before packing.")
            order = fulfillment.lock_order(session, tenant_id=tid, order_id=ship.sales_order_id)
            body = PackInput(requestId=payload.requestId, reason=payload.reason,
                lines=[PackLine(orderLineId=line.lineId, quantity=line.quantity) for line in payload.lines])
            ordinary_shipping.pack(session, order, ship, body, _actor_name(actor))
            result = _result(ship)
    elif payload.action == "dispatch":
        if ship.status == "Cancelled":
            raise HTTPException(409, "A cancelled shipment cannot be dispatched.")
        if payload.origin.strip():
            ship.origin = payload.origin.strip()
        if payload.destination.strip():
            ship.destination = payload.destination.strip()
        if payload.carrier.strip():
            ship.carrier = payload.carrier.strip()
        if payload.tracking.strip():
            ship.tracking_ref = payload.tracking.strip()
        if not (ship.origin or "").strip() or not (ship.destination or "").strip() or not (ship.carrier or "").strip():
            raise HTTPException(409, "Enter the origin, destination and carrier before dispatch.")
        result = _dispatch(session, tid, actor, ship, payload)
        if not isinstance(result, dict) or "shipmentNo" not in result:
            result = _result(_ship(session, tid, public_id))
    elif payload.action == "tracking":
        if ship.status not in ("Dispatched", "In transit", "Customs", "Out for delivery"):
            raise HTTPException(409, "Record dispatch before adding a tracking event.")
        status = payload.trackingStatus.strip()
        if status not in ("In transit", "Customs", "Out for delivery", ""):
            raise HTTPException(422, "Tracking events are In transit, Customs or Out for delivery.")
        if payload.tracking.strip():
            ship.tracking_ref = payload.tracking.strip()
        if payload.eta.strip():
            ship.eta = payload.eta.strip()
        if status and ship.batch_managed and ship.direction != "Inbound":
            from app.modules.batches import service as batches
            batches.shipment_status(session, tid, ship.public_id, status, actor=actor)
        elif status and ship.batch_managed:
            from app.modules.batches.dto import ShipmentStatus
            from app.modules.batches import workflow
            workflow.inbound_status(session, tid, actor, ship, ShipmentStatus(requestId=payload.requestId, reason=payload.reason, status=status))
        elif status:
            from app.modules.shipments.service import _transition
            _transition(session, public_id=str(ship.public_id), status=status, actor=actor)
        result = _result(_ship(session, tid, public_id))
    elif payload.action == "cancel":
        if ship.dispatched_at or ship.inventory_dispatched or ship.status not in ("Pending", "Label created"):
            raise HTTPException(409, "Goods already dispatched need a return or reconciliation, not a cancellation.")
        if ship.batch_managed and ship.direction == "Inbound":
            from app.modules.batches.dto import ShipmentStatus
            from app.modules.batches import workflow
            workflow.inbound_status(session, tid, actor, ship, ShipmentStatus(requestId=payload.requestId, reason=payload.reason, status="Cancelled"))
        elif ship.batch_managed:
            from app.modules.batches import service as batches
            batches.shipment_status(session, tid, ship.public_id, "Cancelled", actor=actor)
        elif ship.purchase_order_id:
            from app.modules.shipments.supplier_inbound import transition
            transition(session, ship, "Cancelled", actor)
        else:
            ship.status = "Cancelled"
            if ship.sales_order_id:
                from app.modules.sales import fulfillment
                order = fulfillment.lock_order(session, tenant_id=tid, order_id=ship.sales_order_id)
                fulfillment.refresh_order(session, order)
        result = _result(_ship(session, tid, public_id))
    elif payload.action == "deliver":
        result = _deliver(session, tid, actor, ship, payload)
    elif payload.action == "resolve":
        result = _resolve(session, tid, actor, ship, payload)
    else:
        raise HTTPException(422, "Unknown shipment action.")
    session.flush()
    if payload.action != "pack":
        flow.event(session, tid, ship.public_id, f"Shipment {payload.action}", payload, _actor_name(actor), snapshot=result)
    return result

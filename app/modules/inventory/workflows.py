"""Quantity-controlled RMAs and counted-stock review. Writes are atomic and replayable."""
import json
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.core.security import has_permission
from app.models.catalog import Product, ProductVariant
from app.models.inventory import Location, StockLevel, StockEntry
from app.models.inventory_operations import Stocktake, StocktakeLine, ReturnLine, WorkflowEvent
from app.models.sales import SalesReturn, SalesOrder, SalesOrderLine
from app.models.shipments import Shipment
from app.modules.inventory import repository as repo, ledger, replenishment as rules


def actor(principal):
    if not principal.email:
        raise HTTPException(409, "A named user is required for warehouse actions")
    return principal.email.lower()


def fingerprint(payload):
    return json.dumps(payload.model_dump(mode="json", exclude={"requestId"}), sort_keys=True)


def replay(session, tid, scope, kind, payload):
    event = session.scalar(select(WorkflowEvent).where(WorkflowEvent.tenant_id == tid,
        WorkflowEvent.request_id == payload.requestId))
    previous = json.loads(event.payload) if event else None
    if previous is not None:
        previous.pop("snapshot", None)
    if event and (event.kind != kind or json.dumps(previous, sort_keys=True) != fingerprint(payload) or (scope and event.scope_id != scope)):
        raise HTTPException(409, "Request ID has already been used for different data")
    return event


def event(session, tid, scope, kind, payload, who, snapshot=None):
    body = json.loads(fingerprint(payload))
    if snapshot is not None:
        body["snapshot"] = snapshot
    session.add(WorkflowEvent(tenant_id=tid, scope_id=scope, request_id=payload.requestId,
        kind=kind, payload=json.dumps(body, sort_keys=True), actor=who))
    session.flush()


def atomic(session, function, *args):
    try:
        with session.begin_nested():
            return function(session, *args)
    except IntegrityError as exc:
        raise HTTPException(409, "This operation changed concurrently. Reload before trying again.") from exc


def history(session, tid, pid):
    return [{"action": e.kind, "actor": e.actor, "at": e.created_at.isoformat(), "data": json.loads(e.payload)}
        for e in session.scalars(select(WorkflowEvent).where(WorkflowEvent.tenant_id == tid,
            WorkflowEvent.scope_id == pid).order_by(WorkflowEvent.id))]


def level(session, tid, vid, lid, lock=False):
    query = select(StockLevel).where(StockLevel.tenant_id == tid, StockLevel.variant_id == vid, StockLevel.location_id == lid)
    return session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def cache(session, tid, vid):
    session.flush()
    v = session.get(ProductVariant, vid)
    v.qty_on_hand = session.scalar(select(func.coalesce(func.sum(StockLevel.on_hand), 0)).where(
        StockLevel.tenant_id == tid, StockLevel.variant_id == vid)) or 0


def last_movement(session, tid, vid, lid):
    return session.scalar(select(func.coalesce(func.max(StockEntry.id), 0)).where(
        StockEntry.tenant_id == tid, StockEntry.variant_id == vid, StockEntry.location_id == lid,
        StockEntry.qty_delta != 0)) or 0


def material(line):
    if line.counted_qty is None:
        return False
    delta = abs(line.counted_qty - line.book_qty)
    return delta > 5 or delta > line.book_qty * .1


def count_snapshot(lines):
    return [{"label": l.label, "book": l.book_qty, "count": l.counted_qty, "recount": l.recount_qty} for l in lines]


def validate_counter(session, tid, counter, who):
    from app.models.core import User
    from app.core.roles import permissions_for
    if counter == who:
        return
    user = session.scalar(select(User).where(User.tenant_id == tid, func.lower(User.email) == counter, User.is_active.is_(True)))
    if not user or not has_permission(permissions_for(user.role, user.is_platform_admin), "inventory.write"):
        raise HTTPException(422, "Choose an active company counter with inventory write access")


def count_detail(session, tid, pid):
    s = rules.get(session, Stocktake, tid, pid)
    loc = session.get(Location, s.location_id)
    lines = session.scalars(select(StocktakeLine).where(StocktakeLine.tenant_id == tid,
        StocktakeLine.stocktake_id == s.id).order_by(StocktakeLine.id)).all()
    return {"id": str(s.public_id), "reference": s.reference, "reason": s.reason,
        "warehouse": loc.name, "status": s.status, "counter": s.counter, "creator": s.creator,
        "lines": [{"id": l.id, "label": l.label, "book": l.book_qty, "count": l.counted_qty,
            "recount": l.recount_qty, "material": material(l),
            "effective": l.recount_qty if l.recount_qty is not None else l.counted_qty} for l in lines],
        "events": history(session, tid, s.public_id)}


def counts(session, tid, limit=50, offset=0):
    items = [{"id": str(s.public_id), "reference": s.reference, "status": s.status,
        "warehouse": session.get(Location, s.location_id).name, "counter": s.counter}
        for s in session.scalars(select(Stocktake).where(Stocktake.tenant_id == tid).order_by(Stocktake.id.desc()).limit(limit).offset(offset))]
    return {"items": items, "total": session.scalar(select(func.count()).select_from(Stocktake).where(Stocktake.tenant_id == tid))}


def new_count(session, tid, payload, principal):
    who = actor(principal)
    # Location row serializes count creation including request replay.
    loc = rules.get(session, Location, tid, payload.locationId, True)
    old = replay(session, tid, None, "Count created", payload)
    if old:
        return count_detail(session, tid, old.scope_id)
    counter = payload.counter.strip().lower()
    validate_counter(session, tid, counter, who)
    query = select(ProductVariant).join(Product, Product.id == ProductVariant.product_id).where(
        ProductVariant.tenant_id == tid, Product.tenant_id == tid, Product.is_deleted.is_(False))
    if payload.variantIds:
        query = query.where(ProductVariant.public_id.in_(set(payload.variantIds)))
    else:
        query = query.where(ProductVariant.id.in_(select(StockLevel.variant_id).where(
            StockLevel.tenant_id == tid, StockLevel.location_id == loc.id)))
    variants = session.scalars(query.order_by(ProductVariant.id)).all()
    if not variants or (payload.variantIds and len(variants) != len(set(payload.variantIds))):
        raise HTTPException(422, "Choose valid variants for this count")
    if len(variants) > 500:
        raise HTTPException(422, "Split this warehouse count into sessions of at most 500 variants")
    s = Stocktake(tenant_id=tid, location_id=loc.id, reference=payload.reference.strip(),
        reason=payload.reason.strip(), counter=counter, creator=who, status="Counting")
    session.add(s)
    session.flush()
    for v in variants:
        row = level(session, tid, v.id, loc.id, True)
        session.add(StocktakeLine(tenant_id=tid, stocktake_id=s.id, variant_id=v.id,
            label=rules.label(session, v), book_qty=row.on_hand if row else 0,
            movement_id=last_movement(session, tid, v.id, loc.id)))
    session.flush()
    initial = session.scalars(select(StocktakeLine).where(StocktakeLine.tenant_id == tid, StocktakeLine.stocktake_id == s.id)).all()
    event(session, tid, s.public_id, "Count created", payload, who, count_snapshot(initial))
    return count_detail(session, tid, s.public_id)


def count_action(session, tid, pid, kind, payload, principal):
    who = actor(principal)
    from app.modules.sales import fulfillment
    waiting = fulfillment.lock_waiting_orders(session, tenant_id=tid) if kind == "post" else []
    s = rules.get(session, Stocktake, tid, pid, True)
    if replay(session, tid, pid, f"Count {kind}", payload):
        return count_detail(session, tid, pid)
    if s.status in ("Posted", "Cancelled"):
        raise HTTPException(409, "This stocktake is closed")
    lines = session.scalars(select(StocktakeLine).where(StocktakeLine.tenant_id == tid,
        StocktakeLine.stocktake_id == s.id).order_by(StocktakeLine.variant_id)).all()
    admin = has_permission(principal.permissions, "admin.write")
    previous_snapshot = count_snapshot(lines)
    if kind in ("save", "submit") and who != s.counter and not admin:
        raise HTTPException(403, "Only the assigned counter can submit counts")
    if kind == "save":
        if s.status not in ("Counting", "Recount"):
            raise HTTPException(409, "Count is already under review")
        indexed = {l.id: l for l in lines}
        if len({l.id for l in payload.lines}) != len(payload.lines) or not payload.lines:
            raise HTTPException(422, "Choose distinct count lines")
        for item in payload.lines:
            l = indexed.get(item.id)
            if l is None:
                raise HTTPException(422, "Count line not found")
            if s.status == "Recount":
                l.recount_qty = item.quantity
            else:
                l.counted_qty = item.quantity
    elif kind == "submit":
        if s.status not in ("Counting", "Recount") or any(
                (l.recount_qty if s.status == "Recount" else l.counted_qty) is None for l in lines):
            raise HTTPException(409, "Complete every line before submitting")
        s.status = "Submitted"
    elif kind == "recount":
        if s.status != "Submitted":
            raise HTTPException(409, "Submit the count before requesting a recount")
        if payload.counter:
            new_counter = payload.counter.strip().lower()
            validate_counter(session, tid, new_counter, who)
            s.counter = new_counter
        for l in lines:
            l.recount_qty = None
        s.status = "Recount"
    elif kind == "restart":
        for l in lines:
            current = level(session, tid, l.variant_id, s.location_id, True)
            l.book_qty = current.on_hand if current else 0
            l.movement_id = last_movement(session, tid, l.variant_id, s.location_id)
            l.counted_qty = l.recount_qty = None
        s.status = "Counting"
    elif kind == "cancel":
        s.status = "Cancelled"
    elif kind == "post":
        if s.status != "Submitted":
            raise HTTPException(409, "Submit the stocktake for approval first")
        if any(material(l) and l.recount_qty is None for l in lines):
            raise HTTPException(409, "Significant variances require a recount before approval (>5 units or >10%)")
        previous_counters = set(session.scalars(select(WorkflowEvent.actor).where(WorkflowEvent.tenant_id == tid,
            WorkflowEvent.scope_id == pid, WorkflowEvent.kind.in_(["Count save", "Count submit"]))))
        if any(material(l) for l in lines) and who in previous_counters | {s.counter, s.creator}:
            if not (admin and payload.override):
                raise HTTPException(403, "Significant variances need an independent reviewer. An administrator can explicitly override with a reason")
        from app.models.batches import BatchLine, BatchLot
        changes = []
        for l in lines:
            session.scalar(select(Product).join(ProductVariant, ProductVariant.product_id == Product.id).where(
                Product.tenant_id == tid, ProductVariant.tenant_id == tid, ProductVariant.id == l.variant_id).with_for_update(of=Product))
            current = level(session, tid, l.variant_id, s.location_id, True)
            before = current.on_hand if current else 0
            effective = l.recount_qty if l.recount_qty is not None else l.counted_qty
            if effective is None or before != l.book_qty or last_movement(session, tid, l.variant_id, s.location_id) != l.movement_id:
                raise HTTPException(409, f"Stock moved after the snapshot for {l.label}. Restart and count again")
            traced = session.scalar(select(func.coalesce(func.sum(BatchLot.on_hand), 0)).join(
                BatchLine, BatchLine.id == BatchLot.line_id).where(BatchLot.tenant_id == tid,
                BatchLine.tenant_id == tid, BatchLine.variant_id == l.variant_id, BatchLot.location_id == s.location_id)) or 0
            if effective < max(current.reserved if current else 0, traced):
                raise HTTPException(409, f"Count cannot remove reserved or batch-traced stock: {l.label}")
            changes.append((l, before, effective - before))
        for l, before, delta in changes:
            if delta:
                repo.adjust_stock(session, tenant_id=tid, variant_id=l.variant_id,
                    location_id=s.location_id, on_hand_delta=delta)
                current = level(session, tid, l.variant_id, s.location_id)
                ledger.record(session, level=current, before=before, kind="Adjustment",
                    request_id=payload.requestId, reference=s.reference, reason=payload.reason,
                    actor=who, source_module="inventory", source_type="stocktake", source_id=s.public_id)
                cache(session, tid, l.variant_id)
        s.status = "Posted"
        if any(delta > 0 for _, _, delta in changes):
            fulfillment.refresh_waiting_orders(session, waiting)
    else:
        raise HTTPException(404, "Unknown stocktake action")
    event(session, tid, pid, f"Count {kind}", payload, who,
        previous_snapshot if kind == "restart" else count_snapshot(lines))
    return count_detail(session, tid, pid)


def return_options(session, tid, order_id=None, search=""):
    query = select(SalesOrder).where(SalesOrder.tenant_id == tid,
        SalesOrder.is_deleted.is_(False), SalesOrder.id.in_(select(Shipment.sales_order_id).where(
            Shipment.tenant_id == tid, Shipment.is_deleted.is_(False), Shipment.batch_managed.is_(False),
            Shipment.direction == "Outbound", (Shipment.inventory_dispatched.is_(True) | (Shipment.status == "Delivered")))))
    if order_id:
        query = query.where(SalesOrder.id == order_id)
    elif search.strip():
        term = "%" + search.strip() + "%"
        query = query.where(SalesOrder.order_no.ilike(term) | SalesOrder.customer_name.ilike(term))
    orders = session.scalars(query.order_by(SalesOrder.id.desc()).limit(200)).all()
    result = []
    for order in orders:
        lines = []
        from app.modules.sales.fulfillment import normal_dispatched
        sent = normal_dispatched(session, order)
        for l in session.scalars(select(SalesOrderLine).where(SalesOrderLine.tenant_id == tid, SalesOrderLine.order_id == order.id)):
            authorized = session.scalar(select(func.coalesce(func.sum(ReturnLine.requested - ReturnLine.cancelled), 0)).where(
                ReturnLine.tenant_id == tid, ReturnLine.order_line_id == l.id)) or 0
            if sent.get(l.id, 0) > authorized:
                lines.append({"id": l.id, "label": f"{l.name} · {l.color or '—'} / {l.size or '—'}",
                    "remaining": sent[l.id] - authorized})
        if lines:
            result.append({"id": str(order.public_id), "label": f"{order.order_no} · {order.customer_name}",
                "currency": order.currency_code, "lines": lines})
    return {"orders": result, "locations": [{"id": str(l.public_id), "label": l.name} for l in session.scalars(
        select(Location).where(Location.tenant_id == tid, Location.is_deleted.is_(False)).order_by(Location.name))]}


def rma_state(lines):
    if not lines:
        return None
    if all(l.requested == l.cancelled for l in lines):
        return "Cancelled"
    if any(l.received - l.ready - l.repair - l.scrap - l.restocked > 0 for l in lines):
        return "Quarantine"
    if any(l.repair for l in lines):
        return "Repair"
    if any(l.ready for l in lines):
        return "Ready to restock"
    if any(l.received < l.requested - l.cancelled for l in lines):
        return "Partially received" if any(l.received for l in lines) else "Awaiting receipt"
    return "Closed"


def return_detail(session, tid, pid):
    r = rules.get(session, SalesReturn, tid, pid)
    lines = session.scalars(select(ReturnLine).where(ReturnLine.tenant_id == tid, ReturnLine.return_id == r.id).order_by(ReturnLine.id)).all()
    loc = session.get(Location, r.location_id) if r.location_id else None
    return {"id": str(r.public_id), "reference": r.rma_no, "order": r.order_ref, "customer": r.customer_name,
        "reason": r.reason, "refund": str(r.refund), "currency": r.currency_code,
        "status": rma_state(lines) or r.status, "warehouse": loc.name if loc else None,
        "locationId": str(loc.public_id) if loc else None, "legacy": not bool(lines),
        "lines": [{"id": l.id, "label": l.label, "requested": l.requested, "cancelled": l.cancelled,
            "received": l.received, "ready": l.ready, "repair": l.repair, "scrap": l.scrap, "restocked": l.restocked,
            "quarantine": l.received - l.ready - l.repair - l.scrap - l.restocked} for l in lines],
        "events": history(session, tid, r.public_id)}


def returns(session, tid, limit=50, offset=0):
    filters = (SalesReturn.tenant_id == tid, SalesReturn.is_deleted.is_(False))
    items = [return_detail(session, tid, pid) for pid in session.scalars(select(SalesReturn.public_id).where(
        *filters).order_by(SalesReturn.id.desc()).limit(limit).offset(offset))]
    return {"items": items, "total": session.scalar(select(func.count()).select_from(SalesReturn).where(*filters))}


def new_return(session, tid, payload, principal):
    who = actor(principal)
    order = rules.get(session, SalesOrder, tid, payload.orderId, True)
    old = replay(session, tid, None, "Return authorized", payload)
    if old:
        return return_detail(session, tid, old.scope_id)
    eligible = next((o for o in return_options(session, tid, order.id)["orders"] if o["id"] == str(order.public_id)), None)
    if eligible is None:
        raise HTTPException(409, "Choose a dispatched normal sales order with returnable quantities. Batch returns use shipment Returned / QC controls")
    loc = rules.get(session, Location, tid, payload.locationId)
    remaining = {l["id"]: l["remaining"] for l in eligible["lines"]}
    if len({l.orderLineId for l in payload.lines}) != len(payload.lines):
        raise HTTPException(422, "Choose distinct order lines")
    planned = []
    from app.modules.sales.fulfillment import variant_for
    for item in payload.lines:
        if item.quantity > remaining.get(item.orderLineId, 0):
            raise HTTPException(409, "Return exceeds shipped quantity remaining on this order line")
        l = session.get(SalesOrderLine, item.orderLineId)
        variant = session.get(ProductVariant, l.variant_id) if l.variant_id else variant_for(session, order, l)
        if variant is None or variant.tenant_id != tid:
            raise HTTPException(409, "Resolve the source variant before authorizing this return")
        planned.append((item, l, variant))
    r = SalesReturn(tenant_id=tid, sales_order_id=order.id, location_id=loc.id,
        order_ref=order.order_no, customer_name=order.customer_name, reason=payload.reason.strip(),
        refund=payload.refund, currency_code=order.currency_code, status="Awaiting receipt")
    session.add(r)
    session.flush()
    r.rma_no = f"RMA-{r.id}"
    for item, l, v in planned:
        session.add(ReturnLine(tenant_id=tid, return_id=r.id, order_line_id=l.id, variant_id=v.id,
            label=f"{l.name} · {l.color or '—'} / {l.size or '—'} · {l.sku or v.sku}", requested=item.quantity,
            cancelled=0, received=0, ready=0, repair=0, scrap=0, restocked=0))
    event(session, tid, r.public_id, "Return authorized", payload, who)
    return return_detail(session, tid, r.public_id)


def return_action(session, tid, pid, kind, payload, principal):
    who = actor(principal)
    from app.modules.sales import fulfillment
    waiting = fulfillment.lock_waiting_orders(session, tenant_id=tid) if kind == "restock" else []
    r = rules.get(session, SalesReturn, tid, pid, True)
    if replay(session, tid, pid, f"Return {kind}", payload):
        return return_detail(session, tid, pid)
    lines = session.scalars(select(ReturnLine).where(ReturnLine.tenant_id == tid, ReturnLine.return_id == r.id).order_by(ReturnLine.id)).all()
    if not lines or rma_state(lines) in ("Closed", "Cancelled"):
        raise HTTPException(409, "This return is closed or is a legacy record without quantity controls")
    if kind in ("cancel", "close"):
        if kind == "cancel" and any(l.received for l in lines):
            raise HTTPException(409, "Received stock cannot be cancelled")
        if any(l.received != l.scrap + l.restocked for l in lines):
            raise HTTPException(409, "Process all received stock before closing the return")
        for l in lines:
            l.cancelled = l.requested - l.received
    else:
        if not payload.lines or len({l.id for l in payload.lines}) != len(payload.lines):
            raise HTTPException(422, "Choose distinct return lines")
        if kind == "receive" and payload.locationId:
            loc = rules.get(session, Location, tid, payload.locationId)
            if any(l.received for l in lines) and loc.id != r.location_id:
                raise HTTPException(409, "This RMA already has physical stock in another warehouse")
            r.location_id = loc.id
        indexed = {l.id: l for l in lines}
        for item in payload.lines:
            l = indexed.get(item.id)
            if l is None:
                raise HTTPException(422, "Return line not found")
            quarantine = l.received - l.ready - l.repair - l.scrap - l.restocked
            movement = None
            quantity = item.quantity
            if kind == "receive":
                if quantity <= 0 or quantity > l.requested - l.cancelled - l.received:
                    raise HTTPException(409, "Receipt exceeds authorized outstanding quantity")
                l.received += quantity
                movement = "Customer return"
            elif kind in ("assess", "repair"):
                quantity = item.approved + item.repair + item.scrap
                if quantity <= 0 or quantity > (quarantine if kind == "assess" else l.repair) or (kind == "repair" and item.repair):
                    raise HTTPException(409, "Disposition exceeds stock in quarantine / repair")
                if kind == "repair":
                    l.repair -= quantity
                l.ready += item.approved
                l.repair += item.repair
                l.scrap += item.scrap
                if item.scrap:
                    quantity = item.scrap
                    movement = "Return scrap"
            elif kind == "restock":
                if quantity <= 0 or quantity > l.ready:
                    raise HTTPException(409, "Put-away exceeds QC-approved stock")
                v = session.get(ProductVariant, l.variant_id)
                session.scalar(select(Product).where(Product.tenant_id == tid, Product.id == v.product_id).with_for_update())
                row = repo.add_stock(session, tenant_id=tid, variant_id=l.variant_id, location_id=r.location_id, qty=quantity)
                l.ready -= quantity
                l.restocked += quantity
                ledger.record(session, level=row, before=row.on_hand-quantity, kind="Return restock",
                    reference=r.rma_no, reason=payload.reason, actor=who, source_module="inventory",
                    source_type="return", source_id=r.public_id)
                cache(session, tid, l.variant_id)
            else:
                raise HTTPException(404, "Unknown return action")
            if movement:
                # Quarantine and scrap are physical movements outside sellable
                # stock; record their quantity without changing the stock balance.
                row = level(session, tid, l.variant_id, r.location_id)
                if row is None:
                    from types import SimpleNamespace
                    row = SimpleNamespace(tenant_id=tid, variant_id=l.variant_id, location_id=r.location_id, on_hand=0)
                ledger.record(session, level=row, before=row.on_hand, kind=movement,
                    reference=r.rma_no, reason=payload.reason, actor=who, source_module="inventory",
                    source_type="return", source_id=r.public_id, quantity=quantity)
    r.status = rma_state(lines)
    event(session, tid, pid, f"Return {kind}", payload, who)
    if kind == "restock":
        fulfillment.refresh_waiting_orders(session, waiting)
    return return_detail(session, tid, pid)

"""Live warehouse replenishment, serialized against incoming purchase orders."""
import math
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import func, select
from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import Location, StockLevel
from app.models.inventory_operations import ReplenishmentRule
from app.models.procurement import Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceiptLine
from app.models.batches import BatchLine


def get(session, model, tid, pid, lock=False):
    query = select(model).where(model.tenant_id == tid, model.public_id == pid)
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None or getattr(row, "is_deleted", False):
        raise HTTPException(404, "Record not found")
    return row


def label(session, variant):
    product = session.get(Product, variant.product_id)
    color = session.get(AttributeValue, variant.color_id) if variant.color_id else None
    size = session.get(AttributeValue, variant.size_id) if variant.size_id else None
    return f"{product.title} · {color.value if color else '—'} / {size.value if size else '—'} · {variant.sku}"


def options(session, tid):
    from app.models.core import User
    from app.core.roles import permissions_for, has_permission
    return {
        "variants": [{"id": str(v.public_id), "label": label(session, v)} for v in session.scalars(
            select(ProductVariant).join(Product, Product.id == ProductVariant.product_id).where(
                ProductVariant.tenant_id == tid, Product.tenant_id == tid,
                Product.is_deleted.is_(False), ProductVariant.status != "Archived").order_by(ProductVariant.sku))],
        "locations": [{"id": str(l.public_id), "label": l.name} for l in session.scalars(select(Location).where(
            Location.tenant_id == tid, Location.is_deleted.is_(False)).order_by(Location.name))],
        "suppliers": [{"id": str(s.public_id), "label": s.name} for s in session.scalars(select(Supplier).where(
            Supplier.tenant_id == tid, Supplier.is_deleted.is_(False)).order_by(Supplier.name))],
        "counters": [{"id": u.email, "label": u.display_name or u.email} for u in session.scalars(select(User).where(
            User.tenant_id == tid, User.is_active.is_(True)).order_by(User.email))
            if has_permission(permissions_for(u.role, u.is_platform_admin), "inventory.write")],
    }


def save_rule(session, tid, payload):
    v = get(session, ProductVariant, tid, payload.variantId)
    product = session.scalar(select(Product).where(Product.id == v.product_id, Product.tenant_id == tid).with_for_update())
    if product.is_deleted or v.status == "Archived":
        raise HTTPException(409, "Choose an active catalog variant")
    loc = get(session, Location, tid, payload.locationId)
    supplier = get(session, Supplier, tid, payload.supplierId)
    r = session.scalar(select(ReplenishmentRule).where(ReplenishmentRule.tenant_id == tid,
        ReplenishmentRule.variant_id == v.id, ReplenishmentRule.location_id == loc.id).with_for_update())
    if r is None:
        r = ReplenishmentRule(tenant_id=tid, variant_id=v.id, location_id=loc.id)
        session.add(r)
    r.supplier_id, r.minimum, r.target = supplier.id, payload.minimum, payload.target
    r.lead_days, r.minimum_order, r.pack_multiple, r.active = payload.leadDays, payload.minimumOrder, payload.packMultiple, payload.active
    session.flush()
    return {"id": str(r.public_id)}


def incoming(session, tid):
    from app.modules.sales.fulfillment import variant_for
    result = []
    for po, line in session.execute(select(PurchaseOrder, PurchaseOrderLine).join(PurchaseOrderLine,
            PurchaseOrderLine.po_id == PurchaseOrder.id).where(PurchaseOrder.tenant_id == tid,
            PurchaseOrderLine.tenant_id == tid, PurchaseOrder.is_deleted.is_(False),
            PurchaseOrder.sales_order_id.is_(None), PurchaseOrder.status.notin_(["Cancelled", "Rejected", "Inactive"]))):
        variant = session.get(ProductVariant, line.variant_id) if line.variant_id else None
        if variant is None:
            try:
                variant = variant_for(session, SimpleNamespace(tenant_id=tid), line)
            except HTTPException:
                continue
        if not variant or variant.tenant_id != tid:
            continue
        # Batch receipts are recorded on their traced line. Ordinary receipts
        # have a stable PO-line FK; never count the same receipt twice.
        batch_received = session.scalar(select(func.coalesce(func.sum(BatchLine.received), 0)).where(
            BatchLine.tenant_id == tid, BatchLine.po_line_id == line.id)) or 0
        normal_received = session.scalar(select(func.coalesce(func.sum(GoodsReceiptLine.received), 0)).where(
            GoodsReceiptLine.tenant_id == tid, GoodsReceiptLine.po_line_id == line.id)) or 0
        remaining = max(0, line.qty - batch_received - normal_received)
        # Historical complete receipts without line links remain complete.
        if po.inventory_received:
            remaining = 0
        if remaining:
            loc = session.get(Location, po.incoming_location_id) if po.incoming_location_id else None
            result.append({"poId": str(po.public_id), "po": po.po_no, "variant": variant.id,
                "location": po.incoming_location_id, "warehouse": loc.name if loc else None,
                "quantity": remaining, "expected": po.expected})
    return result


def plan(session, tid):
    arrivals = incoming(session, tid)
    rows = []
    for rule in session.scalars(select(ReplenishmentRule).where(ReplenishmentRule.tenant_id == tid).order_by(ReplenishmentRule.id)):
        v = session.get(ProductVariant, rule.variant_id)
        product = session.get(Product, v.product_id)
        loc = session.get(Location, rule.location_id)
        supplier = session.get(Supplier, rule.supplier_id)
        level = session.scalar(select(StockLevel).where(StockLevel.tenant_id == tid,
            StockLevel.variant_id == v.id, StockLevel.location_id == loc.id))
        available = level.on_hand - level.reserved if level else 0
        pending = [p for p in arrivals if p["variant"] == v.id]
        assigned = sum(p["quantity"] for p in pending if p["location"] == loc.id)
        unassigned = sum(p["quantity"] for p in pending if p["location"] is None)
        enabled = rule.active and not product.is_deleted and product.status == "Active" and not loc.is_deleted and not supplier.is_deleted and v.status != "Archived"
        shortage = max(0, rule.target - available - assigned) if enabled and available + assigned <= rule.minimum else 0
        suggested = math.ceil(max(shortage, rule.minimum_order) / rule.pack_multiple) * rule.pack_multiple if shortage else 0
        rows.append({"id": str(rule.public_id), "variantId": str(v.public_id), "label": label(session, v),
            "locationId": str(loc.public_id), "warehouse": loc.name, "supplierId": str(supplier.public_id), "supplier": supplier.name,
            "minimum": rule.minimum, "target": rule.target, "leadDays": rule.lead_days,
            "minimumOrder": rule.minimum_order, "packMultiple": rule.pack_multiple, "active": rule.active,
            "available": available, "incoming": assigned, "unassigned": unassigned, "suggested": suggested,
            "enabled": enabled, "expected": (date.today() + timedelta(days=rule.lead_days)).isoformat()})
    return {"rules": rows, "unassigned": [{k: value for k, value in p.items() if k != "variant"} for p in arrivals if p["location"] is None]}


def visible_alerts(session):
    """RLS selects the caller's company for legacy screen and dashboard readers."""
    result = []
    for tid in session.scalars(select(ReplenishmentRule.tenant_id).distinct()):
        result.extend(r for r in plan(session, tid)["rules"] if r["enabled"] and r["suggested"] > 0)
    return sorted(result, key=lambda r: (r["available"], r["label"]))


def create_po(session, tid, pid, payload):
    rule = get(session, ReplenishmentRule, tid, pid)
    v = session.get(ProductVariant, rule.variant_id)
    # The variant's product serializes proposals across different warehouses.
    session.scalar(select(Product).where(Product.tenant_id == tid, Product.id == v.product_id)
        .with_for_update().execution_options(populate_existing=True))
    session.refresh(v)
    rule = get(session, ReplenishmentRule, tid, pid, True)
    session.scalars(select(StockLevel).where(StockLevel.tenant_id == tid, StockLevel.variant_id == v.id)
        .order_by(StockLevel.id).with_for_update().execution_options(populate_existing=True)).all()
    previous = session.scalar(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tid,
        PurchaseOrder.replenishment_request_id == payload.requestId))
    if previous:
        if previous.replenishment_rule_id != rule.id:
            raise HTTPException(409, "Request ID belongs to another proposal")
        return {"id": str(previous.public_id), "po": previous.po_no}
    row = next(r for r in plan(session, tid)["rules"] if r["id"] == str(pid))
    if row["unassigned"]:
        raise HTTPException(409, "Assign outstanding incoming POs to a warehouse before creating more stock")
    if not row["suggested"] or not row["enabled"]:
        raise HTTPException(409, "This warehouse no longer needs replenishment")
    if v.supplier_price is None or v.supplier_price <= 0:
        raise HTTPException(409, "Set the variant's supplier price in Catalog first")
    if v.currency_code != "EUR":
        raise HTTPException(409, "Replenishment POs currently use EUR; set a supplier price in EUR before creating this proposal")
    from app.modules.procurement.dto import PurchaseOrderCreate, POLineIn
    from app.modules.procurement.service import create_po as purchase
    product = session.get(Product, v.product_id)
    color = session.get(AttributeValue, v.color_id) if v.color_id else None
    size = session.get(AttributeValue, v.size_id) if v.size_id else None
    supplier = session.get(Supplier, rule.supplier_id)
    po = purchase(session, tenant_id=tid, resolved_supplier=supplier, payload=PurchaseOrderCreate(
        supplier=supplier.name, expected=row["expected"], lines=[POLineIn(name=product.title,
            color=color.value if color else None, size=size.value if size else None,
            sku=v.sku, qty=row["suggested"], price=v.supplier_price)]))
    po.incoming_location_id, po.replenishment_rule_id, po.replenishment_request_id = rule.location_id, rule.id, payload.requestId
    for line in session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.tenant_id == tid, PurchaseOrderLine.po_id == po.id)):
        line.variant_id = v.id
    from app.core.audit import write_audit
    write_audit(session, tenant_id=tid, action="CREATE", entity_type="replenishment", entity_id=str(rule.public_id),
        detail=f"{po.po_no}: {row['suggested']} units for {row['warehouse']}; {payload.reason}")
    session.flush()
    return {"id": str(po.public_id), "po": po.po_no}


def assign(session, tid, pid, payload):
    po = get(session, PurchaseOrder, tid, pid, True)
    if po.sales_order_id or po.inventory_received or po.status in ("Cancelled", "Rejected"):
        raise HTTPException(409, "Only open stock purchase orders can be assigned")
    if payload.locationId is None:
        raise HTTPException(422, "Choose the incoming warehouse")
    from app.modules.sales.fulfillment import variant_for
    products = set()
    for line in session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.tenant_id == tid, PurchaseOrderLine.po_id == po.id)):
        variant = variant_for(session, SimpleNamespace(tenant_id=tid), line)
        if variant:
            products.add(variant.product_id)
    session.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_(products))
        .order_by(Product.id).with_for_update()).all()
    po.incoming_location_id = get(session, Location, tid, payload.locationId).id
    session.flush()
    return {"id": str(po.public_id)}

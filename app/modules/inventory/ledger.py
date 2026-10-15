"""Append stock movement snapshots in the same transaction as their source."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import Location, StockEntry

KINDS = ["Opening balance", "Receipt", "Adjustment", "Transfer out", "Transfer in",
         "Dispatch", "Return to QC", "Write off", "Return to supplier", "Customer return", "Return scrap", "Return restock"]


def record(session, *, level, before, kind, request_id=None, reference, reason, actor=None,
           source_module=None, source_type=None, source_id=None, source_ref=None, batch_id=None, quantity=None):
    if kind not in KINDS:
        raise ValueError("Unknown inventory movement type")
    delta = level.on_hand - before
    if delta == 0 and kind not in ("Opening balance", "Return to QC", "Customer return", "Return scrap"):
        return None
    variant = session.get(ProductVariant, level.variant_id)
    product = session.get(Product, variant.product_id) if variant else None
    location = session.get(Location, level.location_id)
    if not variant or not product or not location or any(
            row.tenant_id != level.tenant_id for row in (variant, product, location)):
        raise HTTPException(409, "Stock movement has an invalid variant or location.")
    if actor:
        from app.models.core import User
        try:
            user = session.scalar(select(User).where(User.tenant_id == level.tenant_id,
                User.public_id == UUID(str(actor))))
            if user:
                actor = f"{user.display_name or user.email} ({user.email})"
        except (ValueError, TypeError):
            pass
    color = session.get(AttributeValue, variant.color_id) if variant.color_id else None
    size = session.get(AttributeValue, variant.size_id) if variant.size_id else None
    entry = StockEntry(tenant_id=level.tenant_id, request_id=request_id or uuid4(), kind=kind,
        reference=(reference or source_ref or kind)[:80], reason=(reason or kind)[:400],
        actor=str(actor)[:256] if actor else None, variant_id=variant.id, location_id=location.id,
        qty_delta=delta, before_qty=before, after_qty=level.on_hand,
        quantity=abs(delta) if quantity is None else quantity,
        source_module=source_module, source_type=source_type,
        source_id=UUID(str(source_id)) if source_id else None, source_ref=(source_ref or reference)[:80],
        batch_id=UUID(str(batch_id)) if batch_id else None,
        article_name=product.title, variant_sku=variant.sku,
        color_name=color.value if color else None, size_name=size.value if size else None,
        location_name=location.name)
    session.add(entry)
    session.flush()
    return entry


def serialize(entry, variant):
    href = None
    if entry.source_id:
        prefix = {("procurement", "receipt"): "/procurement/receipts/",
                  ("shipments", "shipment"): "/shipments/shipments/",
                  ("sales", "order"): "/sales/orders/",
                  ("inventory", "return"): "/inventory/returns/",
                  ("inventory", "stocktake"): "/inventory/stocktakes/"}.get((entry.source_module, entry.source_type))
        if prefix:
            href = prefix + str(entry.source_id)
    return {"id": str(entry.public_id), "receiptId": str(entry.request_id),
        "kind": entry.kind, "reference": entry.reference, "reason": entry.reason, "actor": entry.actor,
        "article": entry.article_name, "sku": entry.variant_sku, "variantId": str(variant.public_id),
        "color": entry.color_name, "size": entry.size_name, "location": entry.location_name,
        "delta": entry.qty_delta, "quantity": entry.quantity,
        "before": entry.before_qty, "after": entry.after_qty, "createdAt": entry.created_at.isoformat(),
        "source": {"module": entry.source_module, "type": entry.source_type,
            "reference": entry.source_ref or entry.reference, "href": href},
        "batchId": str(entry.batch_id) if entry.batch_id else None,
        "movementHref": f"/inventory/movements/{entry.request_id}"}


def history(session, *, tenant_id, product_id=None, request_id=None, variant_id=None, location_id=None,
            kind=None, search="", from_at: datetime | None = None, to_at: datetime | None = None,
            limit=25, offset=0):
    tid = UUID(str(tenant_id))
    conditions = [StockEntry.tenant_id == tid, ProductVariant.tenant_id == tid]
    if product_id:
        product = session.scalar(select(Product).where(Product.tenant_id == tid, Product.public_id == product_id))
        if product is None:
            raise HTTPException(404, "Article not found")
        conditions.append(ProductVariant.product_id == product.id)
    if request_id:
        conditions.append(StockEntry.request_id == request_id)
    base = list(conditions)
    if variant_id:
        conditions.append(ProductVariant.public_id == variant_id)
    if location_id:
        conditions.append(StockEntry.location_id.in_(select(Location.id).where(
            Location.tenant_id == tid, Location.public_id == location_id)))
    if kind:
        if kind not in KINDS:
            raise HTTPException(422, "Choose a valid movement type.")
        conditions.append(StockEntry.kind == kind)
    from_at = from_at.replace(tzinfo=timezone.utc) if from_at and from_at.tzinfo is None else from_at
    to_at = to_at.replace(tzinfo=timezone.utc) if to_at and to_at.tzinfo is None else to_at
    if from_at and to_at and from_at >= to_at:
        raise HTTPException(422, "The end date must follow the start date.")
    if from_at:
        conditions.append(StockEntry.created_at >= from_at)
    if to_at:
        conditions.append(StockEntry.created_at < to_at)
    if search.strip():
        value = "%" + search.strip() + "%"
        conditions.append(or_(*(column.ilike(value) for column in (
            StockEntry.article_name, StockEntry.variant_sku, StockEntry.reference, StockEntry.source_ref,
            StockEntry.reason, StockEntry.actor, StockEntry.color_name, StockEntry.size_name))))
    joined = select(StockEntry, ProductVariant).join(ProductVariant, ProductVariant.id == StockEntry.variant_id)
    total = session.scalar(select(func.count()).select_from(StockEntry).join(ProductVariant,
        ProductVariant.id == StockEntry.variant_id).where(*conditions))
    rows = session.execute(joined.where(*conditions).order_by(StockEntry.created_at.desc(), StockEntry.id.desc())
        .limit(limit).offset(offset)).all()
    variants = session.scalars(select(ProductVariant).where(ProductVariant.tenant_id == tid,
        ProductVariant.id.in_(select(StockEntry.variant_id).join(ProductVariant,
            ProductVariant.id == StockEntry.variant_id).where(*base).correlate(None))).order_by(ProductVariant.sku)).all()
    locations = session.scalars(select(Location).where(Location.tenant_id == tid,
        Location.id.in_(select(StockEntry.location_id).join(ProductVariant,
            ProductVariant.id == StockEntry.variant_id).where(*base).correlate(None))).order_by(Location.name)).all()
    return {"entries": [serialize(entry, variant) for entry, variant in rows], "total": total,
        "kinds": KINDS,
        "variants": [{"id": str(v.public_id), "sku": v.sku} for v in variants],
        "locations": [{"id": str(loc.public_id), "name": loc.name} for loc in locations]}

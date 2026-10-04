"""Shared catalogue/inventory matrix writes; stock levels are authoritative."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import Location, StockLevel
from app.modules.inventory import repository as repo


def save_matrix(session, *, tenant_id, public_id, payload):
    tid = UUID(str(tenant_id))
    from app.modules.sales import fulfillment
    waiting_orders = fulfillment.lock_waiting_orders(session, tenant_id=tid)
    product = session.execute(select(Product).where(
        Product.public_id == public_id, Product.tenant_id == tid,
        Product.is_deleted == False).with_for_update()).scalar_one_or_none()
    if product is None:
        return None
    variants = session.scalars(select(ProductVariant).where(
        ProductVariant.product_id == product.id, ProductVariant.tenant_id == tid)
        .order_by(ProductVariant.id)).all()
    attributes = {a.id: a for a in session.scalars(select(AttributeValue).where(
        AttributeValue.tenant_id == tid)).all()}
    def key(variant):
        color = attributes.get(variant.color_id)
        size = attributes.get(variant.size_id)
        return (color.value.casefold(), size.value.casefold()) if color and size else None
    active = {key(v): v for v in variants if key(v) and v.status != "Archived"}
    archived = {key(v): v for v in variants if key(v) and v.status == "Archived"}

    # Validate the entire replacement before changing any data.
    planned, targets, used_ids, color_names = [], set(), set(), set()
    for row in payload.colors:
        color = row.color.strip()
        if not color or color.casefold() in color_names:
            raise HTTPException(400, "Colour names must be non-empty and unique.")
        if not row.cells:
            raise HTTPException(400, "Each colour must have at least one size.")
        color_names.add(color.casefold())
        for cell in row.cells:
            size = cell.size.strip()
            target = (color.casefold(), size.casefold())
            if not size or target in targets:
                raise HTTPException(400, "Size names must be non-empty and unique in each colour.")
            targets.add(target)
            source = ((row.originalColor or color).strip().casefold(),
                      (cell.originalSize or size).strip().casefold())
            variant = active.get(source)
            if variant is None:
                variant = active.get(target) or archived.get(target)
            if variant is not None:
                if variant.id in used_ids:
                    raise HTTPException(409, "Two cells refer to the same variant. Reload the article.")
                used_ids.add(variant.id)
            planned.append((row, cell, color, size, variant))

    # Keep historical variant/SKU references; remove only empty variants from
    # both current matrices. Stock and reservations must never disappear.
    removed = [v for v in variants if key(v) and v.status != "Archived" and v.id not in used_ids]
    for variant in removed:
        levels = session.scalars(select(StockLevel).where(
            StockLevel.variant_id == variant.id, StockLevel.tenant_id == tid).with_for_update()).all()
        if any(level.on_hand or level.reserved for level in levels):
            raise HTTPException(409, f"Cannot remove {variant.sku}: clear its stock and reservations first.")

    if payload.location:
        location = session.scalar(select(Location.id).where(
            Location.tenant_id == tid, Location.is_deleted == False,
            Location.name == payload.location.strip()))
        if location is None:
            raise HTTPException(400, "Select an existing inventory location.")
    else:
        # Use the article's assigned location, not an arbitrary tenant warehouse.
        location = session.scalar(select(StockLevel.location_id)
            .join(ProductVariant, ProductVariant.id == StockLevel.variant_id)
            .join(Location, Location.id == StockLevel.location_id)
            .where(ProductVariant.product_id == product.id, ProductVariant.tenant_id == tid,
                   ProductVariant.status != "Archived", Location.tenant_id == tid,
                   Location.is_deleted == False)
            .order_by(StockLevel.on_hand.desc(), StockLevel.location_id).limit(1))
    if location is None and any(cell.qty > 0 for _, cell, _, _, _ in planned):
        raise HTTPException(400, "Create an inventory location before adding stock.")
    price, currency = repo.variant_defaults(session, product_id=product.id)
    template = next((v for v in variants if v.status != "Archived"), None)
    stock_increased = False
    for row, cell, color_name, size_name, variant in planned:
        color = next((a for a in attributes.values()
                      if a.attr_type == "Color" and a.value.casefold() == color_name.casefold()), None)
        if color is None:
            color = repo.create_color(session, tenant_id=tid, value=color_name, hex=row.hex)
            attributes[color.id] = color
        elif row.hex:
            color.hex = row.hex
        size = next((a for a in attributes.values()
                     if a.attr_type == "Size" and a.value.casefold() == size_name.casefold()), None)
        if size is None:
            size = repo.create_size(session, tenant_id=tid, value=size_name)
            attributes[size.id] = size
        if variant is None:
            variant = repo.find_or_create_variant(session, tenant_id=tid, product_id=product.id,
                color_id=color.id, size_id=size.id, price=price, currency=currency)
            if template:
                variant.retail_price = template.retail_price
                variant.wholesale_price = template.wholesale_price
                variant.online_price = template.online_price
                variant.supplier_price = template.supplier_price
        variant.color_id, variant.size_id, variant.status = color.id, size.id, "Active"
        levels = session.scalars(select(StockLevel).where(
            StockLevel.variant_id == variant.id, StockLevel.tenant_id == tid)
            .order_by(StockLevel.location_id).with_for_update()).all()
        reserved = sum(level.reserved or 0 for level in levels)
        if cell.qty < reserved:
            raise HTTPException(409, f"{variant.sku} has {reserved} reserved units; on-hand cannot be lower.")
        current = sum(level.on_hand or 0 for level in levels)
        delta = cell.qty - current
        if delta > 0:
            stock_increased = True
            target = levels[0] if levels else None
            if target is None:
                if location is None:
                    raise HTTPException(400, "Create an inventory location before adding stock.")
                target = StockLevel(tenant_id=tid, variant_id=variant.id, location_id=location,
                                    on_hand=0, reserved=0)
                session.add(target)
            target.on_hand += delta
        elif delta < 0:
            remaining = -delta
            for level in levels:
                take = min(remaining, max(0, (level.on_hand or 0) - (level.reserved or 0)))
                level.on_hand -= take
                remaining -= take
            if remaining:
                raise HTTPException(409, "Cannot reduce stock below reservations at its locations.")
        elif not levels and location is not None:
            repo.ensure_stock_row(session, tenant_id=tid, variant_id=variant.id, location_id=location)
        variant.qty_on_hand = cell.qty  # legacy field kept aligned for older consumers
    for variant in removed:
        variant.status = "Archived"
        variant.qty_on_hand = 0
    session.flush()
    if stock_increased:
        fulfillment.refresh_waiting_orders(session, waiting_orders)
    return product

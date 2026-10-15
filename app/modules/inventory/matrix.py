"""Shared catalogue/inventory matrix writes; stock levels are authoritative."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import StockLevel
from app.modules.inventory import repository as repo


def save_matrix(session, *, tenant_id, public_id, payload):
    tid = UUID(str(tenant_id))
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

    # A matrix describes which variants exist. Physical quantities are posted
    # only through an Inventory receipt or count correction.
    for _, cell, _, _, variant in planned:
        current = 0 if variant is None else sum(level.on_hand for level in session.scalars(
            select(StockLevel).where(StockLevel.tenant_id == tid,
                StockLevel.variant_id == variant.id).with_for_update()).all())
        if cell.qty is not None and cell.qty != current:
            raise HTTPException(409, "Stock quantities require an Inventory receipt or count correction")

    # Keep historical variant/SKU references; remove only empty variants from
    # both current matrices. Stock and reservations must never disappear.
    removed = [v for v in variants if key(v) and v.status != "Archived" and v.id not in used_ids]
    for variant in removed:
        levels = session.scalars(select(StockLevel).where(
            StockLevel.variant_id == variant.id, StockLevel.tenant_id == tid).with_for_update()).all()
        if any(level.on_hand or level.reserved for level in levels):
            raise HTTPException(409, f"Cannot remove {variant.sku}: clear its stock and reservations first.")

    price, currency = repo.variant_defaults(session, product_id=product.id)
    template = next((v for v in variants if v.status != "Archived"), None)
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
        variant.qty_on_hand = sum(session.scalars(select(StockLevel.on_hand).where(
            StockLevel.tenant_id == tid, StockLevel.variant_id == variant.id)).all())
    for variant in removed:
        variant.status = "Archived"
        variant.qty_on_hand = 0
    session.flush()
    return product

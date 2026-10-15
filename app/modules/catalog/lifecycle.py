"""Catalog release rules for new commercial commitments."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.catalog import AttributeValue, Category, Product, ProductVariant


def validate_activation(session: Session, *, tenant_id: UUID, product_id: int,
                        title: str, category_id: int | None, season: str | None,
                        prices: dict[str, object]) -> None:
    """Validate the release state before an article becomes orderable."""
    missing = []
    if not title.strip():
        missing.append("title")
    category = session.scalar(select(Category).where(Category.id == category_id,
        Category.tenant_id == tenant_id)) if category_id else None
    if category is None or not category.is_active:
        missing.append("an active category")
    if not (season or "").strip():
        missing.append("season")
    variants = session.scalars(select(ProductVariant).where(
        ProductVariant.tenant_id == tenant_id,
        ProductVariant.product_id == product_id,
        ProductVariant.status != "Archived")).all()
    attrs = {a.id: a.attr_type for a in session.scalars(select(AttributeValue).where(
        AttributeValue.tenant_id == tenant_id)).all()}
    saleable = [v for v in variants if v.color_id and v.size_id
                and attrs.get(v.color_id) == "Color" and attrs.get(v.size_id) == "Size"]
    if not saleable:
        missing.append("at least one saved colour and size variant")
    for variant in saleable:
        if not (variant.sku or "").strip():
            missing.append("a SKU for every variant")
        for field, label in (("retail", "retail"), ("wholesale", "wholesale"),
                             ("online", "online"), ("supplier", "supplier")):
            value = prices.get(field)
            if value is None:
                value = getattr(variant, f"{field}_price")
            if value is None or value <= 0:
                missing.append(f"a positive {label} price for every variant")
    if missing:
        raise HTTPException(409, "Cannot activate article: complete " + ", ".join(dict.fromkeys(missing)) + ".")


def ensure_new_lines_orderable(session: Session, *, tenant_id: UUID, lines) -> None:
    """Block new lines that identify a Draft or Discontinued catalog article.

    Unlisted custom lines remain supported by the existing order workflow.
    Existing order fulfillment deliberately does not call this function.
    """
    for line in lines:
        sku = (getattr(line, "sku", None) or "").strip()
        name = (getattr(line, "name", None) or "").strip()
        products = session.scalars(select(Product).join(ProductVariant,
            ProductVariant.product_id == Product.id).where(
            Product.tenant_id == tenant_id, ProductVariant.tenant_id == tenant_id,
            ProductVariant.sku == sku)
            .with_for_update(of=Product)).all() if sku else []
        if not products and name:
            products = session.scalars(select(Product).where(
                Product.tenant_id == tenant_id,
                func.lower(Product.title) == name.lower()).limit(2)
                .with_for_update()).all()
        if len(products) > 1:
            raise HTTPException(409, f"Multiple catalog articles match {name or sku}; select a unique SKU.")
        if products and products[0].is_deleted:
            raise HTTPException(409, f"{products[0].title} is retired and cannot be added to a new order.")
        if products and products[0].status != "Active":
            raise HTTPException(409, f"{products[0].title} is {products[0].status} and cannot be added to a new order.")

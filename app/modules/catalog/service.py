"""Catalog business logic: maps entities to the frontend ScreenConfig."""
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.modules.catalog import repository as repo
from app.modules.inventory import repository as inv_repo
from app.modules.catalog.dto import (
    AttributeCreate, AttributeUpdate, CategoryCreate, CategoryUpdate,
    ProductCreate, ProductUpdate, ProductEditorSave, VariantPricesUpdate, VariantGtinsUpdate,
)
from app.presenters.screen import list_config, text_cell

_STATUS_TONE = {"Active": "green", "Draft": "gray", "Discontinued": "red"}
_ATTR_TONE = {"Color": "navy", "Size": "neutral"}


def _tid(tenant_id: str | UUID) -> UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def list_seasons(session: Session, *, tenant_id: str | UUID) -> list[str]:
    return repo.list_seasons(session, tenant_id=_tid(tenant_id))


def search_products(session: Session, *, q: str, limit: int = 10) -> list[dict]:
    """Type-ahead suggestions for order/line item entry. An empty query lists
    the full catalogue (so clicking the field shows everything); typing filters."""
    rows = repo.search_products(session, q=(q or "").strip(), limit=limit)
    fallback_price, fallback_ccy = None, None
    if any(r["price"] is None for r in rows):
        fallback_price, fallback_ccy = repo.default_price(session)
    out = []
    for r in rows:
        if r["price"] is not None:
            price, ccy = float(r["price"]), r["currency_code"]
        else:  # product has no priced variant yet → tenant default
            price, ccy = (fallback_price or 0.0), fallback_ccy
        # Per-channel prices for order-line pricing; fall back to the base price.
        def _chan(key: str) -> float:
            v = r.get(key)
            return float(v) if v is not None else price
        out.append({
            "name": r["title"], "price": price, "currency": ccy or "EUR",
            "colors": r.get("colors", []),
            "prices": {
                "retail": _chan("retail"),
                "online": _chan("online"),
                "wholesale": _chan("wholesale"),
                "supplier": _chan("supplier"),
            },
        })
    return out


def list_colors(session: Session) -> list[dict]:
    """Color options for order/PO line pickers."""
    return [{"name": r["value"], "hex": r["hex"] or "#CBD1DC"}
            for r in repo.list_colors(session)]


def list_sizes(session: Session) -> list[str]:
    """Ordered size scale for PO size breakdowns."""
    return repo.list_sizes(session)


def list_category_names(session: Session) -> list[str]:
    """All category names, for the product form's Category picker."""
    return repo.list_category_names(session)


def products_screen(session: Session, *, limit: int = 25, offset: int = 0) -> dict:
    rows, total = repo.list_products(session, limit=limit, offset=offset)
    def _money(v):
        return f"€{v:,.2f}" if v is not None else "—"

    grid = []
    for r in rows:
        grid.append([
            text_cell(r["title"], avatar=True, sub=r["category"] or "—"),
            text_cell(r["sku"] or "—", mono=True),
            text_cell(_money(r["retail"]), align="right", mono=True, strong=True),
            text_cell(_money(r["online"]), align="right", mono=True),
            text_cell(_money(r["wholesale"]), align="right", mono=True),
            text_cell(_money(r["supplier"]), align="right", mono=True),
            text_cell(str(r["variant_count"]), align="center", mono=True),
            text_cell(r["status"], badge=_STATUS_TONE.get(r["status"], "gray")),
        ])
    return list_config(
        columns=[
            {"label": "Product"}, {"label": "SKU ID"},
            {"label": "Retail Price", "align": "right"},
            {"label": "Online Price", "align": "right"},
            {"label": "Wholesale Price", "align": "right"},
            {"label": "Supplier Price", "align": "right"},
            {"label": "Variants", "align": "center"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"title": r["title"], "sku": r["sku"] or "",
                  "category": r["category"],
                  "season": r["season"], "status": r["status"],
                  "retailPrice": float(r["retail"]) if r["retail"] is not None else None,
                  "onlinePrice": float(r["online"]) if r["online"] is not None else None,
                  "wholesalePrice": float(r["wholesale"]) if r["wholesale"] is not None else None,
                  "supplierPrice": float(r["supplier"]) if r["supplier"] is not None else None}
                 for r in rows],
        search="Search products, SKU…", action="New product",
        filters=["Category", "Season", "Status"],
    )


_PRODUCT_STATUS_TONE = {"Active": "green", "Draft": "neutral", "Discontinued": "red"}


def save_product_matrix(session: Session, *, tenant_id, public_id: str, payload):
    from app.modules.inventory.matrix import save_matrix
    return save_matrix(session, tenant_id=tenant_id, public_id=public_id, payload=payload)


def product_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_product_detail(session, public_id=public_id)
    if data is None:
        return None
    prod, category, variants = data["product"], data["category"], data["variants"]
    priced_variants = [v for v in variants if v["color"] and v["size"]]

    def _minp(key: str) -> str:
        vals = [v[key] for v in priced_variants if v.get(key) is not None]
        return f"€{min(vals):,.2f}" if vals else "—"

    def _minraw(key: str) -> float | None:
        vals = [v[key] for v in priced_variants if v.get(key) is not None]
        return float(min(vals)) if vals else None

    price_label = _minp("retail_price")
    prices = {"retail": _minp("retail_price"), "wholesale": _minp("wholesale_price"),
              "online": _minp("online_price"), "supplier": _minp("supplier_price")}

    # Both screens derive their axes and quantities from the same active variants
    # and stock levels, including variants with no inventory location yet.
    from app.modules.inventory.service import stock_article_detail
    stock = stock_article_detail(session, public_id=public_id)["stock"]
    sizes = stock["sizes"]
    matrix = [{"name": c["name"], "hex": c["hex"],
               "cells": [{"q": q, "tone": "red" if q == 0 else "amber" if q < 40 else "neutral"}
                         for q in c["cells"]]} for c in stock["colors"]]

    specs = [
        {"k": "Composition", "v": prod.composition or "—"},
        {"k": "Fabric", "v": prod.fabric or "—"},
        {"k": "Gauge", "v": prod.gauge or "—"},
        {"k": "Care", "v": prod.care or "—"},
        {"k": "Origin", "v": prod.origin or "—"},
        {"k": "HS code", "v": prod.hs_code or "—"},
        {"k": "Weight", "v": prod.weight or "—"},
    ]

    return {
        "variant": "product",
        "ref": (category or "Product").upper(),
        "title": prod.title,
        "statusLabel": prod.status,
        "statusTone": _PRODUCT_STATUS_TONE.get(prod.status, "neutral"),
        "meta": [
            {"k": "Category", "v": category or "—"},
            {"k": "Season", "v": prod.season or "—"},
            {"k": "Retail price", "v": price_label},
            {"k": "Variants", "v": str(len(variants))},
        ],
        "tabs": ["Overview", "Variant matrix", "Inventory", "Pricing", "Activity"],
        "product": {
            "sizes": sizes,
            "matrix": matrix,
            "specs": specs,
            "prices": prices,
            "variants": [{"id": str(v["public_id"]), "sku": v["sku"],
                          "color": v["color"], "size": v["size"],
                          "gtin": v["barcode"],
                          "currency": v["currency_code"],
                          "retailPrice": float(v["retail_price"]) if v["retail_price"] is not None else None,
                          "wholesalePrice": float(v["wholesale_price"]) if v["wholesale_price"] is not None else None,
                          "onlinePrice": float(v["online_price"]) if v["online_price"] is not None else None,
                          "supplierPrice": float(v["supplier_price"]) if v["supplier_price"] is not None else None}
                         for v in variants if v["color"] and v["size"]],
            "image": prod.image_url or None,
            # Raw values to prefill the in-place edit form.
            "form": {
                "title": prod.title,
                "category": category or "",
                "season": prod.season or "",
                "status": prod.status,
                "retailPrice": _minraw("retail_price"),
                "wholesalePrice": _minraw("wholesale_price"),
                "onlinePrice": _minraw("online_price"),
                "supplierPrice": _minraw("supplier_price"),
                "imageUrl": prod.image_url or "",
                "composition": prod.composition or "",
                "fabric": prod.fabric or "",
                "gauge": prod.gauge or "",
                "care": prod.care or "",
                "origin": prod.origin or "",
                "hsCode": prod.hs_code or "",
                "weight": prod.weight or "",
            },
        },
    }


def create_product(session: Session, *, tenant_id: str | UUID, payload: ProductCreate):
    tid = _tid(tenant_id)
    if payload.status != "Draft":
        raise HTTPException(409, "Create the article as Draft, save its colour and size variants, then activate it.")
    sku = (payload.sku or "").strip()
    if not sku:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SKU ID is required")
    if repo.sku_taken(session, sku=sku):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"SKU ID “{sku}” is already in use — choose a different one",
        )
    category_id = repo.get_or_create_category(session, tenant_id=tid, name=payload.category)
    product = repo.create_product(
        session, tenant_id=tid, title=payload.title,
        category_id=category_id, season=payload.season, status=payload.status,
        image_url=payload.imageUrl,
    )
    # Always seed the first variant with the user-supplied SKU (prices optional).
    try:
        repo.create_variant(
            session, tenant_id=tid, product_id=product.id, sku=sku,
            retail=payload.retailPrice, wholesale=payload.wholesalePrice,
            online=payload.onlinePrice, supplier=payload.supplierPrice,
            currency_code=payload.currency_code,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"SKU ID “{sku}” is already in use — choose a different one",
        ) from exc
    write_audit(
        session, tenant_id=tid, action="CREATE", entity_type="product",
        entity_id=str(product.public_id), detail=f"Created product “{product.title}”",
    )
    return product


def update_product(session: Session, *, tenant_id: str | UUID, public_id: str, payload: ProductUpdate):
    tid = _tid(tenant_id)
    if any(value is not None for value in (payload.retailPrice, payload.wholesalePrice,
                                           payload.onlinePrice, payload.supplierPrice)):
        raise HTTPException(409, "Edit prices per colour and size in the article Pricing tab.")
    from sqlalchemy import select
    from app.models.catalog import Category, Product
    from app.modules.catalog.lifecycle import validate_activation

    existing = session.scalar(select(Product).where(Product.public_id == public_id,
        Product.tenant_id == tid, Product.is_deleted.is_(False)).with_for_update())
    if existing is None:
        return None
    if payload.status == "Active" and existing.status != "Active":
        category = session.scalar(select(Category).where(Category.tenant_id == tid,
            Category.name == (payload.category or "")))
        category_id = category.id if category else None
        validate_activation(session, tenant_id=tid, product_id=existing.id,
            title=payload.title, category_id=category_id, season=payload.season,
            prices={})
    else:
        category_id = repo.get_or_create_category(session, tenant_id=tid, name=payload.category)
    product = repo.update_product(
        session, public_id=public_id, title=payload.title, category_id=category_id,
        season=payload.season, status=payload.status,
        # Pass the raw value ("" clears the image; None means "no change").
        image_url=payload.imageUrl,
        specs={"composition": payload.composition, "fabric": payload.fabric,
               "gauge": payload.gauge,
               "care": payload.care, "origin": payload.origin,
               "hsCode": payload.hsCode, "weight": payload.weight},
    )
    if product is None:
        return None
    sku = (payload.sku or "").strip()
    if sku:
        if repo.sku_taken(session, sku=sku, exclude_product_id=product.id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"SKU ID “{sku}” is already in use — choose a different one",
            )
        repo.set_product_sku(session, product=product, sku=sku,
                             currency_code=payload.currency_code)
    write_audit(session, tenant_id=tid, action="UPDATE", entity_type="product",
                entity_id=str(product.public_id), detail=f"Updated product “{product.title}”")
    return product


def save_product_editor(session: Session, *, tenant_id: str | UUID, public_id: str,
                        payload: ProductEditorSave):
    """Save detail changes and the matrix inside the request's DB transaction."""
    from sqlalchemy import select
    from app.models.catalog import Category, Product

    tid = _tid(tenant_id)
    product = session.scalar(select(Product).where(Product.public_id == public_id,
        Product.tenant_id == tid, Product.is_deleted.is_(False)).with_for_update())
    if product is None:
        return None
    category = session.scalar(select(Category.name).where(Category.id == product.category_id)) if product.category_id else None
    fields = {"title": product.title, "category": category, "season": product.season,
              "status": product.status, "imageUrl": product.image_url or "",
              "composition": product.composition, "fabric": product.fabric,
              "gauge": product.gauge, "care": product.care, "origin": product.origin,
              "hsCode": product.hs_code, "weight": product.weight}
    fields.update(payload.details.model_dump(exclude_unset=True))
    from app.modules.inventory.matrix import save_matrix
    save_matrix(session, tenant_id=tid, public_id=public_id, payload=payload.matrix)
    return update_product(session, tenant_id=tid, public_id=public_id,
        payload=ProductUpdate(**fields))


def update_variant_prices(session: Session, *, tenant_id: str | UUID, public_id: str,
                          payload: VariantPricesUpdate):
    from sqlalchemy import select
    from app.models.catalog import Product, ProductVariant

    tid = _tid(tenant_id)
    product = session.scalar(select(Product).where(Product.public_id == public_id,
        Product.tenant_id == tid, Product.is_deleted.is_(False)).with_for_update())
    if product is None:
        return None
    ids = [entry.variantId for entry in payload.variants]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Each variant can appear only once in a price save.")
    variants = session.scalars(select(ProductVariant).where(
        ProductVariant.product_id == product.id, ProductVariant.tenant_id == tid,
        ProductVariant.public_id.in_(ids), ProductVariant.status != "Archived",
        ProductVariant.color_id.is_not(None), ProductVariant.size_id.is_not(None))
        .order_by(ProductVariant.id).with_for_update()).all()
    if len(variants) != len(ids):
        raise HTTPException(409, "A variant changed or was removed. Reload the article before saving prices.")
    by_id = {v.public_id: v for v in variants}
    for entry in payload.variants:
        values = (entry.retailPrice, entry.wholesalePrice,
                  entry.onlinePrice, entry.supplierPrice)
        if product.status == "Active" and any(value is None or value <= 0 for value in values):
            raise HTTPException(409, "Active articles need positive retail, wholesale, online and supplier prices for every variant.")
        variant = by_id[entry.variantId]
        variant.retail_price, variant.wholesale_price = values[:2]
        variant.online_price, variant.supplier_price = values[2:]
        variant.price = entry.retailPrice or 0
    session.flush()
    write_audit(session, tenant_id=tid, action="UPDATE", entity_type="product",
                entity_id=str(product.public_id), detail=f"Updated variant prices for “{product.title}”")
    return product


def _gtin(value: str | None) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    if not candidate.isascii() or not candidate.isdigit() or len(candidate) not in (8, 12, 13, 14):
        raise HTTPException(422, "GTIN must have 8, 12, 13 or 14 digits.")
    total = sum(int(digit) * (3 if offset % 2 == 0 else 1)
                for offset, digit in enumerate(reversed(candidate[:-1])))
    if (10 - total % 10) % 10 != int(candidate[-1]):
        raise HTTPException(422, "GTIN check digit is invalid.")
    return candidate


def update_variant_gtins(session: Session, *, tenant_id: str | UUID, public_id: str,
                         payload: VariantGtinsUpdate):
    from sqlalchemy import select
    from app.models.catalog import Product, ProductVariant

    tid = _tid(tenant_id)
    product = session.scalar(select(Product).where(Product.public_id == public_id,
        Product.tenant_id == tid, Product.is_deleted.is_(False)).with_for_update())
    if product is None:
        return None
    ids = [entry.variantId for entry in payload.variants]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Each variant can appear only once in a GTIN save.")
    variants = session.scalars(select(ProductVariant).where(
        ProductVariant.product_id == product.id, ProductVariant.tenant_id == tid,
        ProductVariant.public_id.in_(ids), ProductVariant.status != "Archived",
        ProductVariant.color_id.is_not(None), ProductVariant.size_id.is_not(None))
        .order_by(ProductVariant.id).with_for_update()).all()
    if len(variants) != len(ids):
        raise HTTPException(409, "A variant changed or was removed. Reload the article before saving GTINs.")
    codes = [_gtin(entry.gtin) for entry in payload.variants]
    assigned = [code for code in codes if code]
    if len(assigned) != len(set(assigned)):
        raise HTTPException(409, "A GTIN cannot be assigned to more than one variant.")
    conflicts = session.scalar(select(ProductVariant.id).where(
        ProductVariant.tenant_id == tid, ProductVariant.barcode.in_(assigned),
        ProductVariant.public_id.not_in(ids)).limit(1)) if assigned else None
    if conflicts is not None:
        raise HTTPException(409, "This GTIN is already assigned to another variant.")
    by_id = {v.public_id: v for v in variants}
    try:
        with session.begin_nested():
            # Clear the edited rows first so two variants can exchange GTINs
            # without a transient violation of the unique barcode index.
            for variant in variants:
                variant.barcode = None
            session.flush()
            for entry, code in zip(payload.variants, codes):
                by_id[entry.variantId].barcode = code
            session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "This GTIN is already assigned to another variant.") from exc
    write_audit(session, tenant_id=tid, action="UPDATE", entity_type="product",
                entity_id=str(product.public_id), detail=f"Updated variant GTINs for “{product.title}”")
    return product


def set_product_image(session: Session, *, public_id: str, image_url: str | None):
    return repo.set_product_image(session, public_id=public_id, image_url=image_url)


# ---------- Categories ----------

def categories_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_categories(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], strong=True),
            text_cell(str(r["products"] or 0), align="center", mono=True),
            text_cell(str(int(r["active"] or 0)), align="center", mono=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Category"},
            {"label": "Products", "align": "center"},
            {"label": "Active", "align": "center"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "active": bool(r["is_active"])}
                 for r in rows],
        search="Search categories…", action="New category", filters=[],
    )


def create_category(session: Session, *, tenant_id: str | UUID, payload: CategoryCreate):
    tid = _tid(tenant_id)
    parent_id = repo.find_category_id(session, name=payload.parent) if payload.parent else None
    return repo.create_category(
        session, tenant_id=tid, name=payload.name,
        parent_id=parent_id, is_active=payload.active,
    )


def update_category(session: Session, *, public_id: str, payload: CategoryUpdate):
    parent_id = repo.find_category_id(session, name=payload.parent) if payload.parent else None
    return repo.update_category(session, public_id=public_id, name=payload.name,
                                parent_id=parent_id, is_active=payload.active)


def delete_category(session: Session, *, tenant_id: str | UUID, public_id: UUID):
    tid = _tid(tenant_id)
    category = repo.category_for_delete(session, tenant_id=tid, public_id=public_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    products, children = repo.category_dependencies(session, category_id=category.id)
    if products or children:
        raise HTTPException(status.HTTP_409_CONFLICT,
            f"This category has {products} linked article(s) and {children} child category(s). "
            "Reassign them before deleting it. Archived articles also retain their category.")
    write_audit(session, tenant_id=tid, action="DELETE", entity_type="category",
                entity_id=str(category.public_id), detail=f"Deleted category “{category.name}”")
    session.delete(category)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "This category is still in use. Reassign linked articles and child categories before deleting it.") from exc


# ---------- Attributes (colors & sizes) ----------

def attributes_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_attributes(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["value"], strong=True),
            text_cell(r["attr_type"], badge=_ATTR_TONE.get(r["attr_type"], "neutral")),
            text_cell(r["code"], mono=True),
            text_cell(str(r["used"] or 0), align="center", mono=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Value"}, {"label": "Type"}, {"label": "Code"},
            {"label": "Used in", "align": "center"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"value": r["value"], "type": r["attr_type"], "code": r["code"]} for r in rows],
        search="Search attributes…", action="New attribute", filters=["Type"],
    )


def create_attribute(session: Session, *, tenant_id: str | UUID, payload: AttributeCreate):
    tid = _tid(tenant_id)
    return repo.create_attribute(
        session, tenant_id=tid, attr_type=payload.type,
        value=payload.value, code=payload.code,
    )


def update_attribute(session: Session, *, tenant_id: str | UUID, public_id: str,
                     payload: AttributeUpdate):
    from sqlalchemy import func, or_, select
    from app.models.catalog import AttributeValue, ProductVariant

    tid = _tid(tenant_id)
    attr = session.scalar(select(AttributeValue).where(AttributeValue.public_id == public_id,
        AttributeValue.tenant_id == tid).with_for_update())
    if attr is None:
        return None
    value, code = payload.value.strip(), payload.code.strip()
    if not value or not code:
        raise HTTPException(422, "Attribute value and code are required.")
    linked = session.scalar(select(ProductVariant.id).where(ProductVariant.tenant_id == tid,
        or_(ProductVariant.color_id == attr.id, ProductVariant.size_id == attr.id)).limit(1))
    if linked is not None and payload.type != attr.attr_type:
        raise HTTPException(409, "A colour or size used by variants cannot change type. Rename it instead.")
    duplicate = session.scalar(select(AttributeValue.id).where(AttributeValue.tenant_id == tid,
        AttributeValue.attr_type == payload.type, AttributeValue.id != attr.id,
        or_(func.lower(AttributeValue.value) == value.lower(),
            func.lower(AttributeValue.code) == code.lower())).limit(1))
    if duplicate is not None:
        raise HTTPException(409, "An attribute of this type already uses that name or code.")
    attr.attr_type, attr.value, attr.code = payload.type, value, code
    session.flush()
    write_audit(session, tenant_id=tid, action="UPDATE", entity_type="attribute",
                entity_id=str(attr.public_id), detail=f"Updated {attr.attr_type} “{attr.value}”")
    return attr

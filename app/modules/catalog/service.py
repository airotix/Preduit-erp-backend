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
    ProductCreate, ProductUpdate,
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

    def _minp(key: str) -> str:
        vals = [v[key] for v in variants if v.get(key) is not None]
        return f"€{min(vals):,.2f}" if vals else "—"

    def _minraw(key: str) -> float | None:
        vals = [v[key] for v in variants if v.get(key) is not None]
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
    category_id = repo.get_or_create_category(session, tenant_id=tid, name=payload.category)
    product = repo.update_product(
        session, public_id=public_id, title=payload.title, category_id=category_id,
        season=payload.season, status=payload.status, retail=payload.retailPrice,
        wholesale=payload.wholesalePrice, online=payload.onlinePrice,
        supplier=payload.supplierPrice,
        currency_code=payload.currency_code,
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


def update_attribute(session: Session, *, public_id: str, payload: AttributeUpdate):
    return repo.update_attribute(session, public_id=public_id, attr_type=payload.type,
                                 value=payload.value, code=payload.code)

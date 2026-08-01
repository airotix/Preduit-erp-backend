"""Catalog business logic: maps entities to the frontend ScreenConfig."""
import uuid
from uuid import UUID

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


def search_products(session: Session, *, q: str, limit: int = 10) -> list[dict]:
    """Type-ahead suggestions for order/line item entry."""
    if not q or not q.strip():
        return []
    rows = repo.search_products(session, q=q.strip(), limit=limit)
    fallback_price, fallback_ccy = None, None
    if any(r["price"] is None for r in rows):
        fallback_price, fallback_ccy = repo.default_price(session)
    out = []
    for r in rows:
        if r["price"] is not None:
            price, ccy = float(r["price"]), r["currency_code"]
        else:  # product has no priced variant yet → tenant default
            price, ccy = (fallback_price or 0.0), fallback_ccy
        out.append({
            "name": r["title"], "price": price, "currency": ccy or "EUR",
            "colors": r.get("colors", []),
        })
    return out


def list_colors(session: Session) -> list[dict]:
    """Color options for order/PO line pickers."""
    return [{"name": r["value"], "hex": r["hex"] or "#CBD1DC"}
            for r in repo.list_colors(session)]


def list_sizes(session: Session) -> list[str]:
    """Ordered size scale for PO size breakdowns."""
    return repo.list_sizes(session)


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
            text_cell(str(r["variant_count"]), align="center", mono=True),
            text_cell(r["status"], badge=_STATUS_TONE.get(r["status"], "gray")),
        ])
    return list_config(
        columns=[
            {"label": "Product"}, {"label": "SKU ID"},
            {"label": "Retail Price", "align": "right"},
            {"label": "Online Price", "align": "right"},
            {"label": "Wholesale Price", "align": "right"},
            {"label": "Variants", "align": "center"}, {"label": "Status"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"title": r["title"], "category": r["category"],
                  "season": r["season"], "status": r["status"]} for r in rows],
        search="Search products, SKU…", action="New product",
        filters=["Category", "Season", "Status"],
    )


_PRODUCT_STATUS_TONE = {"Active": "green", "Draft": "neutral", "Discontinued": "red"}


def save_product_matrix(session: Session, *, tenant_id, public_id: str, payload):
    """Edit a product's variant matrix in place: upsert colors, variants and
    per-(color,size) on-hand quantities. Mirrors the inventory stock editor."""
    tid = _tid(tenant_id)
    product = inv_repo.get_product_by_public_id(session, public_id=public_id)
    if product is None:
        return None
    price, currency = inv_repo.variant_defaults(session, product_id=product.id)
    for col in payload.colors:
        name = (col.color or "").strip()
        if not name:
            continue
        color = (inv_repo.find_attr_value(session, attr_type="Color", value=name)
                 or inv_repo.create_color(session, tenant_id=tid, value=name, hex=col.hex))
        for cell in col.cells:
            size = inv_repo.find_attr_value(session, attr_type="Size", value=cell.size)
            if size is None:
                continue
            variant = inv_repo.find_or_create_variant(
                session, tenant_id=tid, product_id=product.id, color_id=color.id,
                size_id=size.id, price=price, currency=currency)
            variant.qty_on_hand = int(cell.qty or 0)
    session.flush()
    return product


def product_detail(session: Session, *, public_id: str) -> dict | None:
    data = repo.get_product_detail(session, public_id=public_id)
    if data is None:
        return None
    prod, category, variants = data["product"], data["category"], data["variants"]

    def _minp(key: str) -> str:
        vals = [v[key] for v in variants if v.get(key) is not None]
        return f"€{min(vals):,.2f}" if vals else "—"

    price_label = _minp("retail_price")
    prices = {"retail": _minp("retail_price"), "wholesale": _minp("wholesale_price"),
              "online": _minp("online_price")}

    # Full catalog size scale (XS…7XL) so the matrix shows every size column,
    # with 0 where this product has no variant — matching the stock editor.
    # Any size a variant uses that isn't in the catalog is appended.
    sizes = repo.list_sizes(session)
    for v in variants:
        if v["size"] and v["size"] not in sizes:
            sizes.append(v["size"])

    # Colors in first-seen order, with swatch hex.
    colors: dict[str, str] = {}
    for v in variants:
        if v["color"] and v["color"] not in colors:
            colors[v["color"]] = v["color_hex"] or "#C9C2B3"

    # On-hand units per (color, size) → matrix cells.
    qty: dict[tuple[str, str], int] = {}
    for v in variants:
        if v["color"] and v["size"]:
            qty[(v["color"], v["size"])] = qty.get((v["color"], v["size"]), 0) + (v["qty_on_hand"] or 0)

    def _tone(q: int) -> str:
        return "red" if q == 0 else "amber" if q < 40 else "neutral"

    matrix = [
        {
            "name": color,
            "hex": hex_,
            "cells": [
                {"q": qty.get((color, s), 0), "tone": _tone(qty.get((color, s), 0))}
                for s in sizes
            ],
        }
        for color, hex_ in colors.items()
    ]

    specs = [
        {"k": "Composition", "v": prod.composition or "—"},
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
        },
    }


def create_product(session: Session, *, tenant_id: str | UUID, payload: ProductCreate):
    tid = _tid(tenant_id)
    category_id = repo.get_or_create_category(session, tenant_id=tid, name=payload.category)
    product = repo.create_product(
        session, tenant_id=tid, title=payload.title,
        category_id=category_id, season=payload.season, status=payload.status,
        image_url=payload.imageUrl,
    )
    # Seed the first variant with an auto-generated SKU when any price is given.
    if any(p is not None for p in (payload.retailPrice, payload.wholesalePrice, payload.onlinePrice)):
        repo.create_variant(
            session, tenant_id=tid, product_id=product.id,
            retail=payload.retailPrice, wholesale=payload.wholesalePrice,
            online=payload.onlinePrice, currency_code=payload.currency_code,
        )
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
        currency_code=payload.currency_code, image_url=payload.imageUrl,
    )
    if product is not None:
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
            r["parent"] or "—",
            text_cell(str(r["products"] or 0), align="center", mono=True),
            text_cell(str(int(r["active"] or 0)), align="center", mono=True),
        ]
        for r in rows
    ]
    return list_config(
        columns=[
            {"label": "Category"}, {"label": "Parent"},
            {"label": "Products", "align": "center"},
            {"label": "Active", "align": "center"},
        ],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "parent": r["parent"] or "", "active": bool(r["is_active"])}
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

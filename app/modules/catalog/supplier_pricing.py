from uuid import UUID
from decimal import Decimal
from types import SimpleNamespace
from pydantic import BaseModel, Field
from fastapi import HTTPException
from sqlalchemy import select
from app.models.supplier_pricing import SupplierPrice
from app.models.catalog import Product, ProductVariant, AttributeValue
from app.models.procurement import Supplier


class PriceIn(BaseModel):
    variantId: UUID
    price: Decimal | None = Field(default=None, gt=0, max_digits=19, decimal_places=4)


class SupplierPricesIn(BaseModel):
    supplierId: UUID
    active: bool = True
    prices: list[PriceIn] = Field(min_length=1, max_length=1000)


def article(session, tid, pid, lock=False):
    q = select(Product).where(Product.tenant_id == tid, Product.public_id == pid, Product.is_deleted.is_(False))
    p = session.scalar(q.with_for_update().execution_options(populate_existing=True) if lock else q)
    if p is None:
        raise HTTPException(404, "Article not found")
    return p


def supplier_options(session, tid):
    return [{"id": str(s.public_id), "name": s.name} for s in session.scalars(select(Supplier).where(
        Supplier.tenant_id == tid, Supplier.is_deleted.is_(False)).order_by(Supplier.name))]


def current_variants(session, tid, product_id):
    """Use only real, current colour/size cells belonging to this article."""
    attrs = {a.id: a for a in session.scalars(select(AttributeValue).where(AttributeValue.tenant_id == tid))}
    variants = session.scalars(select(ProductVariant).where(ProductVariant.tenant_id == tid,
        ProductVariant.product_id == product_id, ProductVariant.status != "Archived")
        .order_by(ProductVariant.id)).all()
    return attrs, [v for v in variants if v.color_id in attrs and v.size_id in attrs
        and attrs[v.color_id].attr_type == "Color" and attrs[v.size_id].attr_type == "Size"]


def detail(session, tid, pid):
    p = article(session, tid, pid)
    attrs, variants = current_variants(session, tid, p.id)
    prices = session.execute(select(SupplierPrice, Supplier).join(Supplier, Supplier.id == SupplierPrice.supplier_id).where(
        SupplierPrice.tenant_id == tid, Supplier.tenant_id == tid, Supplier.is_deleted.is_(False),
        SupplierPrice.variant_id.in_([v.id for v in variants]))).all()
    grouped = {}
    ids = {v.id: str(v.public_id) for v in variants}
    for price, supplier in prices:
        group = grouped.setdefault(supplier.id, {"id": str(supplier.public_id), "name": supplier.name,
            "active": False, "prices": {}})
        group["active"] = group["active"] or price.active
        group["prices"][ids[price.variant_id]] = {"price": str(price.price), "active": price.active, "currency": price.currency}
    return {"variants": [{"id": str(v.public_id), "sku": v.sku, "color": attrs[v.color_id].value, "size": attrs[v.size_id].value}
        for v in variants], "suppliers": list(grouped.values()), "options": supplier_options(session, tid)}


def save(session, tid, pid, payload, actor):
    p = article(session, tid, pid, True)
    supplier = session.scalar(select(Supplier).where(Supplier.tenant_id == tid,
        Supplier.public_id == payload.supplierId, Supplier.is_deleted.is_(False)))
    if supplier is None:
        raise HTTPException(404, "Choose an existing supplier from Procurement")
    _, variants = current_variants(session, tid, p.id)
    by_public = {v.public_id: v for v in variants}
    if len({v.variantId for v in payload.prices}) != len(payload.prices) or {v.variantId for v in payload.prices} != set(by_public):
        raise HTTPException(409, "Variants changed. Reload and save the complete supplier price grid")
    if payload.active and not any(v.price is not None for v in payload.prices):
        raise HTTPException(422, "Enter at least one agreed supplier price")
    old = {r.variant_id: r for r in session.scalars(select(SupplierPrice).where(
        SupplierPrice.tenant_id == tid, SupplierPrice.supplier_id == supplier.id,
        SupplierPrice.variant_id.in_([v.id for v in variants])).with_for_update())}
    changes = []
    for item in payload.prices:
        v = by_public[item.variantId]
        row = old.get(v.id)
        if item.price is None:
            if row:
                changes.append(f"{v.sku}: disabled (was {row.price})")
                session.delete(row)
            continue
        changes.append(f"{v.sku}: {row.price if row else 'unset'} -> {item.price}; active={payload.active}")
        if row is None:
            row = SupplierPrice(tenant_id=tid, variant_id=v.id, supplier_id=supplier.id)
            session.add(row)
        row.price, row.currency, row.active = item.price, "EUR", payload.active
    from app.core.audit import write_audit
    write_audit(session, tenant_id=tid, action="UPDATE", entity_type="catalog_supplier_prices", entity_id=str(p.public_id),
        detail=f"{actor}: supplier {supplier.name}; " + "; ".join(changes))
    session.flush()
    return detail(session, tid, pid)


def agreed_price(session, tid, variant, supplier_id):
    row = session.scalar(select(SupplierPrice).where(SupplierPrice.tenant_id == tid,
        SupplierPrice.variant_id == variant.id, SupplierPrice.supplier_id == supplier_id, SupplierPrice.active.is_(True)))
    if row is None or row.price <= 0 or row.currency != "EUR":
        raise HTTPException(409, f"Save an active EUR price for SKU {variant.sku} and this supplier in Catalog → article → Supplier pricing first")
    return row.price


def sourcing(session, tid, names):
    """Configured suppliers and variant prices, shared by manual and shortage forms."""
    q = select(Product).where(Product.tenant_id == tid, Product.is_deleted.is_(False))
    if names:
        q = q.where(Product.title.in_(names))
    else:
        q = q.where(Product.id.in_(select(ProductVariant.product_id).join(SupplierPrice,
            SupplierPrice.variant_id == ProductVariant.id).where(ProductVariant.tenant_id == tid,
                SupplierPrice.tenant_id == tid, SupplierPrice.active.is_(True))))
    products = session.scalars(q.order_by(Product.id)).all()
    result = []
    for p in products:
        if names and sum(x.title == p.title for x in products) > 1:
            raise HTTPException(409, "Article names are ambiguous; resolve duplicate Catalog names first")
        d = detail(session, tid, p.public_id)
        variants = {v["id"]: v for v in d["variants"]}
        result.append({"productId": p.id, "id": str(p.public_id), "name": p.title,
            "suppliers": [{"id": s["id"], "name": s["name"], "prices": [
                {**variants[vid], "price": float(price["price"]), "currency": price["currency"]}
                for vid, price in s["prices"].items() if vid in variants and price["active"] and price["currency"] == "EUR"]}
                for s in d["suppliers"] if s["active"]]})
    return {"articles": result}


def po_lines(session, tid, supplier, inputs):
    from app.modules.sales.fulfillment import variant_for
    variants = [variant_for(session, SimpleNamespace(tenant_id=tid), line) for line in inputs]
    if any(v is None for v in variants):
        raise HTTPException(409, "Choose exact saved Catalog colour and size variants before creating a PO")
    session.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_({v.product_id for v in variants}))
        .order_by(Product.id).with_for_update().execution_options(populate_existing=True)).all()
    attrs = {a.id: a.value for a in session.scalars(select(AttributeValue).where(AttributeValue.tenant_id == tid))}
    result = []
    for line, variant in zip(inputs, variants):
        session.refresh(variant)
        product = session.get(Product, variant.product_id)
        if product.is_deleted or product.status != "Active" or variant.status == "Archived":
            raise HTTPException(409, "Only active Catalog variants can be purchased")
        result.append({"name": product.title, "color": attrs.get(variant.color_id), "size": attrs.get(variant.size_id),
            "sku": variant.sku, "variant_id": variant.id, "qty": line.qty,
            "price": agreed_price(session, tid, variant, supplier.id)})
    return result

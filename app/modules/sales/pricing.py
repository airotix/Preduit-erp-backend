"""Capture catalog prices once; invoice documents retain the agreed order values."""
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select
from app.models.catalog import Product, ProductVariant, AttributeValue
from app.modules.sales.fulfillment import variant_for


def catalog_lines(session, tid, payload):
    field = {"Wholesale": "wholesale_price", "Retail": "retail_price",
        "Online": "online_price", "Marketplace": "online_price"}[payload.channel]
    planned = []
    # Lock products in a stable order before reading their current prices.
    variants = [variant_for(session, SimpleNamespace(tenant_id=tid), line) for line in payload.lines]
    if any(v is None for v in variants):
        raise HTTPException(409, "Choose saved Catalog colour and size variants for every order line")
    products = {v.product_id for v in variants}
    session.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_(products))
        .order_by(Product.id).with_for_update().execution_options(populate_existing=True)).all()
    for line, v in zip(payload.lines, variants):
        session.refresh(v)
        p = session.get(Product, v.product_id)
        if p.is_deleted or p.status != "Active" or v.status == "Archived":
            raise HTTPException(409, f"{p.title} is not available for new orders")
        price = getattr(v, field)
        if price is None or price <= 0:
            raise HTTPException(409, f"Set a positive {payload.channel} Catalog price for {v.sku}")
        if v.currency_code != "EUR":
            raise HTTPException(409, "Sales currently uses EUR; choose Catalog prices in EUR")
        color = session.get(AttributeValue, v.color_id) if v.color_id else None
        size = session.get(AttributeValue, v.size_id) if v.size_id else None
        if not color or not size:
            raise HTTPException(409, "Save the variant's colour and size in Catalog first")
        planned.append({"name": p.title, "color": color.value, "size": size.value, "sku": v.sku,
            "variant_id": v.id, "qty": line.qty, "price": price})
    return planned


def lock_document_values(session, order, data):
    """Keep editable invoice presentation fields; protect its commercial snapshot."""
    from app.modules.sales import service
    snapshot = service._build_sales_invoice_draft(session, order_no=order.order_no,
        invoice_type=order.channel if order.channel in ("Wholesale", "Retail", "Online") else "Retail")
    if snapshot is None:
        raise HTTPException(409, "The invoice's source order is unavailable")
    result = dict(data)
    for key in ("orderNo", "orderRef", "poNo", "invoiceType", "layout", "currency", "articles", "items", "lines", "totals"):
        result.pop(key, None)
        if key in snapshot:
            result[key] = snapshot[key]
    # Preserve the source customer identity while allowing contact/address formatting.
    result["buyer"] = {**(result.get("buyer") or {}), "name": order.customer_name}
    result["_orderLocked"] = True
    return result

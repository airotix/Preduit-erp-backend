"""Catalog data access. All queries are automatically tenant-filtered by RLS —
no explicit `WHERE tenant_id` needed (plan §2)."""
import re
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.catalog import AttributeValue, Category, Product, ProductVariant

_SKU_RE = re.compile(r"^SKU-(\d{1,6})$")


def next_sku(session: Session) -> str:
    """Next sequential SKU across the tenant: SKU-000001 … SKU-999999."""
    mx = 0
    for s in session.execute(
        select(ProductVariant.sku).where(ProductVariant.sku.like("SKU-%"))
    ).scalars():
        m = _SKU_RE.match(s or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return f"SKU-{mx + 1:06d}"


def list_products(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    """Return (rows, total). Each row aggregates its variants."""
    v_count = func.count(ProductVariant.id)
    v_min = func.min(ProductVariant.price)
    v_ccy = func.min(ProductVariant.currency_code)

    stmt = (
        select(
            Product.public_id, Product.title,
            Category.name.label("category"),
            Product.season, Product.status,
            v_count.label("variant_count"),
            v_min.label("min_price"),
            v_ccy.label("currency_code"),
            func.min(ProductVariant.sku).label("sku"),
            func.min(ProductVariant.retail_price).label("retail"),
            func.min(ProductVariant.online_price).label("online"),
            func.min(ProductVariant.wholesale_price).label("wholesale"),
        )
        .outerjoin(ProductVariant, ProductVariant.product_id == Product.id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(Product.is_deleted == False)  # noqa: E712
        .group_by(
            Product.public_id, Product.title, Category.name,
            Product.season, Product.status,
        )
        .order_by(Product.title)
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Product).where(Product.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def search_products(session: Session, *, q: str, limit: int = 10) -> list[dict]:
    """Type-ahead over product titles → suggested name, price, and own colors."""
    prods = [dict(r._mapping) for r in session.execute(
        select(
            Product.id,
            Product.title,
            func.min(ProductVariant.price).label("price"),
            func.min(ProductVariant.currency_code).label("currency_code"),
        )
        .outerjoin(ProductVariant, ProductVariant.product_id == Product.id)
        .where(Product.is_deleted == False, Product.title.ilike(f"%{q}%"))  # noqa: E712
        .group_by(Product.id, Product.title)
        .order_by(Product.title)
        .limit(limit)
    )]

    ids = [p["id"] for p in prods]
    colors_by_product: dict[int, list[dict]] = {}
    if ids:
        rows = session.execute(
            select(ProductVariant.product_id, AttributeValue.value, AttributeValue.hex)
            .join(AttributeValue, AttributeValue.id == ProductVariant.color_id)
            .where(ProductVariant.product_id.in_(ids), AttributeValue.attr_type == "Color")
            .distinct()
            .order_by(AttributeValue.value)
        )
        for pid, value, hexv in rows:
            colors_by_product.setdefault(pid, []).append(
                {"name": value, "hex": hexv or "#CBD1DC"}
            )
    for p in prods:
        p["colors"] = colors_by_product.get(p["id"], [])
    return prods


def list_colors(session: Session) -> list[dict]:
    """All Color attribute values for the tenant (for order/PO color pickers)."""
    return [dict(r._mapping) for r in session.execute(
        select(AttributeValue.value, AttributeValue.hex)
        .where(AttributeValue.attr_type == "Color")
        .order_by(AttributeValue.value)
    )]


def list_sizes(session: Session) -> list[str]:
    """All Size attribute values, in scale order (for PO size breakdowns)."""
    return [r[0] for r in session.execute(
        select(AttributeValue.value)
        .where(AttributeValue.attr_type == "Size")
        .order_by(AttributeValue.sort_order, AttributeValue.value)
    )]


def default_price(session: Session) -> tuple[float | None, str | None]:
    """Tenant-wide fallback list price/currency for products that have no variant yet."""
    row = session.execute(
        select(func.avg(ProductVariant.price), func.min(ProductVariant.currency_code))
    ).first()
    if row and row[0] is not None:
        return float(row[0]), row[1]
    return None, None


def get_or_create_category(session: Session, *, tenant_id: UUID, name: str | None) -> int | None:
    """Resolve a category by name for this tenant, creating it if missing."""
    if not name:
        return None
    existing = session.execute(
        select(Category.id).where(Category.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    category = Category(tenant_id=tenant_id, name=name)
    session.add(category)
    session.flush()
    return category.id


def create_product(session: Session, *, tenant_id: UUID, title: str,
                   category_id: int | None, season: str | None, status: str,
                   image_url: str | None = None) -> Product:
    product = Product(
        tenant_id=tenant_id, title=title,
        category_id=category_id, season=season, status=status,
        image_url=image_url or None,
    )
    session.add(product)
    session.flush()  # populate id / public_id
    session.refresh(product)
    return product


def update_product(session: Session, *, public_id: str, title: str, category_id: int | None,
                   season: str | None, status: str, retail=None, wholesale=None,
                   online=None, currency_code: str = "EUR", image_url=None) -> Product | None:
    product = session.execute(
        select(Product).where(Product.public_id == public_id,
                              Product.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if product is None:
        return None
    product.title = title
    product.category_id = category_id
    product.season = season
    product.status = status
    if image_url is not None:
        product.image_url = image_url or None
    session.flush()
    # If any price was supplied, apply it to the first variant (create one with an
    # auto SKU if none exists). Only the prices provided are changed.
    if any(p is not None for p in (retail, wholesale, online)):
        variant = session.execute(
            select(ProductVariant).where(ProductVariant.product_id == product.id)
            .order_by(ProductVariant.id)
        ).scalars().first()
        if variant is None:
            create_variant(session, tenant_id=product.tenant_id, product_id=product.id,
                           retail=retail, wholesale=wholesale, online=online,
                           currency_code=currency_code)
        else:
            if retail is not None:
                variant.retail_price = retail
                variant.price = retail
            if wholesale is not None:
                variant.wholesale_price = wholesale
            if online is not None:
                variant.online_price = online
        session.flush()
    session.refresh(product)
    return product


def set_product_image(session: Session, *, public_id: str, image_url: str | None) -> Product | None:
    product = session.execute(
        select(Product).where(Product.public_id == public_id, Product.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if product is None:
        return None
    product.image_url = image_url or None
    session.flush()
    return product


def get_product_detail(session: Session, *, public_id: str) -> dict | None:
    product = session.execute(
        select(Product).where(Product.public_id == public_id, Product.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if product is None:
        return None
    category = (
        session.execute(select(Category.name).where(Category.id == product.category_id)).scalar_one_or_none()
        if product.category_id else None
    )
    Color = aliased(AttributeValue)
    Size = aliased(AttributeValue)
    vstmt = (
        select(
            ProductVariant.sku, ProductVariant.price, ProductVariant.currency_code,
            ProductVariant.retail_price, ProductVariant.wholesale_price, ProductVariant.online_price,
            ProductVariant.status, ProductVariant.qty_on_hand,
            Color.value.label("color"), Color.hex.label("color_hex"),
            Size.value.label("size"), Size.sort_order.label("size_sort"),
        )
        .outerjoin(Color, Color.id == ProductVariant.color_id)
        .outerjoin(Size, Size.id == ProductVariant.size_id)
        .where(ProductVariant.product_id == product.id)
        .order_by(ProductVariant.sku)
    )
    variants = [dict(r._mapping) for r in session.execute(vstmt)]
    return {"product": product, "category": category, "variants": variants}


def create_variant(session: Session, *, tenant_id: UUID, product_id: int, sku: str | None = None,
                    retail=None, wholesale=None, online=None, currency_code: str,
                    status: str = "Active") -> ProductVariant:
    r = retail if retail is not None else 0
    w = wholesale if wholesale is not None else r
    o = online if online is not None else r
    variant = ProductVariant(
        tenant_id=tenant_id, product_id=product_id, sku=sku or next_sku(session),
        price=r, retail_price=r, wholesale_price=w, online_price=o,
        currency_code=currency_code, status=status,
    )
    session.add(variant)
    session.flush()
    return variant


# ---------- Categories ----------

def list_categories(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    Parent = aliased(Category)
    active_sum = func.sum(case((Product.status == "Active", 1), else_=0))
    stmt = (
        select(
            Category.public_id, Category.name, Category.is_active,
            Parent.name.label("parent"),
            func.count(Product.id).label("products"),
            active_sum.label("active"),
        )
        .outerjoin(Product, Product.category_id == Category.id)
        .outerjoin(Parent, Parent.id == Category.parent_id)
        .group_by(Category.public_id, Category.name, Category.is_active, Parent.name)
        .order_by(Category.name)
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(select(func.count()).select_from(Category)).scalar_one()
    return rows, total


def update_category(session: Session, *, public_id: str, name: str,
                    parent_id: int | None, is_active: bool) -> Category | None:
    c = session.execute(
        select(Category).where(Category.public_id == public_id)
    ).scalar_one_or_none()
    if c is None:
        return None
    c.name = name
    c.parent_id = parent_id
    c.is_active = is_active
    session.flush()
    session.refresh(c)
    return c


def update_attribute(session: Session, *, public_id: str, attr_type: str,
                     value: str, code: str) -> AttributeValue | None:
    a = session.execute(
        select(AttributeValue).where(AttributeValue.public_id == public_id)
    ).scalar_one_or_none()
    if a is None:
        return None
    a.attr_type = attr_type
    a.value = value
    a.code = code
    session.flush()
    session.refresh(a)
    return a


def find_category_id(session: Session, *, name: str) -> int | None:
    return session.execute(
        select(Category.id).where(Category.name == name)
    ).scalar_one_or_none()


def create_category(session: Session, *, tenant_id: UUID, name: str,
                    parent_id: int | None, is_active: bool) -> Category:
    category = Category(tenant_id=tenant_id, name=name, parent_id=parent_id, is_active=is_active)
    session.add(category)
    session.flush()
    session.refresh(category)
    return category


# ---------- Attributes (colors & sizes) ----------

def list_attributes(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    used = (
        select(func.count(ProductVariant.id))
        .where(or_(ProductVariant.color_id == AttributeValue.id,
                   ProductVariant.size_id == AttributeValue.id))
        .correlate(AttributeValue)
        .scalar_subquery()
    )
    stmt = (
        select(
            AttributeValue.public_id, AttributeValue.attr_type,
            AttributeValue.value, AttributeValue.code,
            used.label("used"),
        )
        .order_by(AttributeValue.attr_type, AttributeValue.value)
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(select(func.count()).select_from(AttributeValue)).scalar_one()
    return rows, total


def create_attribute(session: Session, *, tenant_id: UUID, attr_type: str,
                     value: str, code: str) -> AttributeValue:
    attr = AttributeValue(tenant_id=tenant_id, attr_type=attr_type, value=value, code=code)
    session.add(attr)
    session.flush()
    session.refresh(attr)
    return attr

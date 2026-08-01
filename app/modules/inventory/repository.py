"""Inventory data access. Tenant-filtered automatically by RLS."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.catalog import AttributeValue, Category, Product, ProductVariant
from app.models.inventory import Location, ReorderAlert, StockLevel, StockTransfer


# ---------- lookups ----------

def find_variant_id_by_sku(session: Session, *, sku: str) -> int | None:
    return session.execute(
        select(ProductVariant.id).where(ProductVariant.sku == sku)
    ).scalar_one_or_none()


def find_location_id_by_name(session: Session, *, name: str) -> int | None:
    return session.execute(
        select(Location.id).where(Location.name == name)
    ).scalar_one_or_none()


# ---------- Stock levels (aggregated per article/product) ----------

def list_stock(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    """One row per article (product), summed across every variant and location."""
    stmt = (
        select(
            Product.public_id, Product.title,
            Category.name.label("category"),
            func.count(func.distinct(ProductVariant.color_id)).label("colors"),
            func.count(func.distinct(StockLevel.location_id)).label("locations"),
            func.coalesce(func.sum(StockLevel.on_hand), 0).label("on_hand"),
            func.coalesce(func.sum(StockLevel.reserved), 0).label("reserved"),
            func.min(ProductVariant.sku).label("sku"),
            func.min(ProductVariant.retail_price).label("retail"),
            func.min(ProductVariant.online_price).label("online"),
            func.min(ProductVariant.wholesale_price).label("wholesale"),
        )
        .select_from(StockLevel)
        .join(ProductVariant, ProductVariant.id == StockLevel.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .group_by(Product.public_id, Product.title, Category.name)
        .order_by(Product.title)
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count(func.distinct(Product.id)))
        .select_from(StockLevel)
        .join(ProductVariant, ProductVariant.id == StockLevel.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
    ).scalar_one()
    return rows, total


def get_article_stock(session: Session, *, public_id: str) -> dict | None:
    """Full color × size × location breakdown for a single article."""
    product = session.execute(
        select(Product).where(Product.public_id == public_id,
                              Product.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if product is None:
        return None

    Color = aliased(AttributeValue)
    Size = aliased(AttributeValue)

    # Color × size grid, summed across locations.
    grid = session.execute(
        select(
            Color.value.label("color"), Color.hex.label("hex"),
            Color.sort_order.label("color_order"),
            Size.value.label("size"), Size.sort_order.label("size_order"),
            func.coalesce(func.sum(StockLevel.on_hand), 0).label("on_hand"),
            func.coalesce(func.sum(StockLevel.reserved), 0).label("reserved"),
        )
        .select_from(StockLevel)
        .join(ProductVariant, ProductVariant.id == StockLevel.variant_id)
        .outerjoin(Color, Color.id == ProductVariant.color_id)
        .outerjoin(Size, Size.id == ProductVariant.size_id)
        .where(ProductVariant.product_id == product.id)
        .group_by(Color.value, Color.hex, Color.sort_order, Size.value, Size.sort_order)
    ).mappings().all()

    # Full size scale from the Size attribute catalog (ordered), so every
    # size shows as a column even when this article carries no stock in it.
    all_sizes = session.execute(
        select(AttributeValue.value, AttributeValue.sort_order)
        .where(AttributeValue.attr_type == "Size")
        .order_by(AttributeValue.sort_order, AttributeValue.value)
    ).mappings().all()

    # Per-location totals.
    locations = session.execute(
        select(
            Location.name.label("location"),
            func.coalesce(func.sum(StockLevel.on_hand), 0).label("on_hand"),
            func.coalesce(func.sum(StockLevel.reserved), 0).label("reserved"),
        )
        .select_from(StockLevel)
        .join(ProductVariant, ProductVariant.id == StockLevel.variant_id)
        .join(Location, Location.id == StockLevel.location_id)
        .where(ProductVariant.product_id == product.id)
        .group_by(Location.name)
        .order_by(Location.name)
    ).mappings().all()

    return {"product": product, "grid": [dict(r) for r in grid],
            "all_sizes": [dict(r) for r in all_sizes],
            "locations": [dict(r) for r in locations]}


def get_product_by_public_id(session: Session, *, public_id: str) -> Product | None:
    return session.execute(
        select(Product).where(Product.public_id == public_id,
                              Product.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def primary_location_id(session: Session, *, product_id: int) -> int | None:
    """Location holding the most units for this product; else the first location."""
    top = session.execute(
        select(StockLevel.location_id)
        .join(ProductVariant, ProductVariant.id == StockLevel.variant_id)
        .where(ProductVariant.product_id == product_id)
        .group_by(StockLevel.location_id)
        .order_by(func.coalesce(func.sum(StockLevel.on_hand), 0).desc())
        .limit(1)
    ).scalar_one_or_none()
    if top is not None:
        return top
    return session.execute(
        select(Location.id).where(Location.is_deleted == False)  # noqa: E712
        .order_by(Location.id).limit(1)
    ).scalar_one_or_none()


def variant_defaults(session: Session, *, product_id: int) -> tuple[Decimal, str]:
    """Price/currency to copy onto brand-new variants of this product."""
    row = session.execute(
        select(ProductVariant.price, ProductVariant.currency_code)
        .where(ProductVariant.product_id == product_id).limit(1)
    ).first()
    if row:
        return row[0], row[1]
    return Decimal("0"), "USD"


def find_attr_value(session: Session, *, attr_type: str, value: str) -> AttributeValue | None:
    return session.execute(
        select(AttributeValue).where(AttributeValue.attr_type == attr_type,
                                     AttributeValue.value == value)
    ).scalars().first()


def create_color(session: Session, *, tenant_id: UUID, value: str, hex: str | None) -> AttributeValue:
    base = "".join(ch for ch in value.upper() if ch.isalnum())[:3] or "CLR"
    code, n = base, 1
    while session.execute(
        select(AttributeValue.id).where(AttributeValue.code == code)
    ).scalar_one_or_none() is not None:
        n += 1
        code = f"{base}{n}"
    av = AttributeValue(tenant_id=tenant_id, attr_type="Color", value=value,
                        code=code, hex=hex or "#CBD1DC", sort_order=0)
    session.add(av)
    session.flush()
    session.refresh(av)
    return av


def find_or_create_variant(session: Session, *, tenant_id: UUID, product_id: int,
                           color_id: int, size_id: int, price: Decimal,
                           currency: str) -> ProductVariant:
    existing = session.execute(
        select(ProductVariant).where(ProductVariant.product_id == product_id,
                                     ProductVariant.color_id == color_id,
                                     ProductVariant.size_id == size_id)
    ).scalars().first()
    if existing:
        return existing
    from app.modules.catalog.repository import next_sku  # local import avoids cycle
    sku = next_sku(session)
    v = ProductVariant(tenant_id=tenant_id, product_id=product_id, sku=sku,
                       color_id=color_id, size_id=size_id, price=price,
                       retail_price=price, wholesale_price=price, online_price=price,
                       currency_code=currency, status="Active")
    session.add(v)
    session.flush()
    session.refresh(v)
    return v


def set_variant_on_hand(session: Session, *, tenant_id: UUID, variant_id: int,
                        location_id: int, on_hand: int) -> None:
    """Upsert a stock level's on-hand, preserving any existing reservation."""
    existing = session.execute(
        select(StockLevel).where(StockLevel.variant_id == variant_id,
                                 StockLevel.location_id == location_id)
    ).scalar_one_or_none()
    if existing:
        existing.on_hand = on_hand
    else:
        session.add(StockLevel(tenant_id=tenant_id, variant_id=variant_id,
                               location_id=location_id, on_hand=on_hand, reserved=0))
    session.flush()


def upsert_stock(session: Session, *, tenant_id: UUID, variant_id: int, location_id: int,
                 on_hand: int, reserved: int) -> StockLevel:
    existing = session.execute(
        select(StockLevel).where(
            StockLevel.variant_id == variant_id, StockLevel.location_id == location_id
        )
    ).scalar_one_or_none()
    if existing:
        existing.on_hand = on_hand
        existing.reserved = reserved
        session.flush()
        return existing
    level = StockLevel(
        tenant_id=tenant_id, variant_id=variant_id, location_id=location_id,
        on_hand=on_hand, reserved=reserved,
    )
    session.add(level)
    session.flush()
    return level


# ---------- Locations ----------

def list_locations(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    skus = (
        select(func.count(func.distinct(StockLevel.variant_id)))
        .where(StockLevel.location_id == Location.id).correlate(Location).scalar_subquery()
    )
    onhand = (
        select(func.coalesce(func.sum(StockLevel.on_hand), 0))
        .where(StockLevel.location_id == Location.id).correlate(Location).scalar_subquery()
    )
    stmt = (
        select(
            Location.public_id, Location.name, Location.code, Location.kind,
            Location.region, Location.capacity, skus.label("skus"), onhand.label("onhand"),
        )
        .where(Location.is_deleted == False)  # noqa: E712
        .order_by(Location.name)
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Location).where(Location.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def _apply_location_fields(loc: Location, *, name, code, kind, region, capacity) -> None:
    loc.name = name
    loc.code = code
    loc.kind = kind
    loc.region = region
    loc.capacity = capacity


def create_location(session: Session, *, tenant_id: UUID, **fields) -> Location:
    loc = Location(tenant_id=tenant_id)
    _apply_location_fields(loc, **fields)
    session.add(loc)
    session.flush()
    session.refresh(loc)
    return loc


def update_location(session: Session, *, public_id: str, **fields) -> Location | None:
    loc = session.execute(
        select(Location).where(Location.public_id == public_id,
                               Location.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if loc is None:
        return None
    _apply_location_fields(loc, **fields)
    session.flush()
    session.refresh(loc)
    return loc


# ---------- Transfers ----------

def list_transfers(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    FromL = aliased(Location)
    ToL = aliased(Location)
    stmt = (
        select(
            StockTransfer.public_id, StockTransfer.transfer_no, FromL.name.label("from_name"),
            ToL.name.label("to_name"), StockTransfer.units,
            StockTransfer.status, StockTransfer.eta,
        )
        .select_from(StockTransfer)
        .outerjoin(FromL, FromL.id == StockTransfer.from_location_id)
        .outerjoin(ToL, ToL.id == StockTransfer.to_location_id)
        .where(StockTransfer.is_deleted == False)  # noqa: E712
        .order_by(StockTransfer.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(StockTransfer).where(StockTransfer.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def set_transfer_status(session: Session, *, public_id: str, status: str) -> StockTransfer | None:
    t = session.execute(
        select(StockTransfer).where(StockTransfer.public_id == public_id,
                                    StockTransfer.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if t is None:
        return None
    t.status = status
    session.flush()
    return t


def create_transfer(session: Session, *, tenant_id: UUID, from_id: int | None,
                    to_id: int | None, units: int) -> StockTransfer:
    trf = StockTransfer(
        tenant_id=tenant_id, from_location_id=from_id, to_location_id=to_id,
        units=units, status="Draft",
    )
    session.add(trf)
    session.flush()
    trf.transfer_no = f"TRF-{2000 + trf.id}"
    session.flush()
    session.refresh(trf)
    return trf


# ---------- Reorder alerts ----------

def list_alerts(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    Color = aliased(AttributeValue)
    Size = aliased(AttributeValue)
    stmt = (
        select(
            ReorderAlert.sku, ReorderAlert.available, ReorderAlert.reorder_point,
            ReorderAlert.suggested, ReorderAlert.severity,
            Product.title, Color.value.label("color"), Size.value.label("size"),
            ProductVariant.retail_price.label("retail"),
            ProductVariant.online_price.label("online"),
            ProductVariant.wholesale_price.label("wholesale"),
        )
        .select_from(ReorderAlert)
        .outerjoin(ProductVariant, ProductVariant.sku == ReorderAlert.sku)
        .outerjoin(Product, Product.id == ProductVariant.product_id)
        .outerjoin(Color, Color.id == ProductVariant.color_id)
        .outerjoin(Size, Size.id == ProductVariant.size_id)
        .where(ReorderAlert.is_deleted == False)  # noqa: E712
        .order_by(ReorderAlert.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(ReorderAlert).where(ReorderAlert.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_alert(session: Session, *, tenant_id: UUID, variant_id: int | None, sku: str,
                 suggested: int, supplier: str) -> ReorderAlert:
    alert = ReorderAlert(
        tenant_id=tenant_id, variant_id=variant_id, sku=sku,
        suggested=suggested, supplier=supplier, severity="Low",
    )
    session.add(alert)
    session.flush()
    session.refresh(alert)
    return alert

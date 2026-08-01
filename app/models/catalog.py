"""Catalog ORM models (exemplar vertical slice)."""
import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AttributeValue(Base):
    __tablename__ = "attribute_values"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    attr_type: Mapped[str] = mapped_column(String(20))  # 'Color' | 'Size'
    value: Mapped[str] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(20))
    hex: Mapped[str | None] = mapped_column(String(9), nullable=True)   # color swatch
    sort_order: Mapped[int] = mapped_column(Integer, default=0)         # size ordering


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("categories.id"), nullable=True)
    season: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    # Spec fields shown on the product detail page.
    composition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gauge: Mapped[str | None] = mapped_column(String(40), nullable=True)
    care: Mapped[str | None] = mapped_column(String(120), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hs_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    weight: Mapped[str | None] = mapped_column(String(20), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ProductVariant(Base):
    __tablename__ = "product_variants"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64))
    color_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    qty_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(19, 4))  # base = retail (back-compat)
    retail_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    wholesale_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    online_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="Active")

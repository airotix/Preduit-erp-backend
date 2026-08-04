"""Catalog API contracts (Pydantic v2), decoupled from ORM models."""
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    """Matches the frontend 'New product' form. category/season are names;
    price seeds the first variant (its SKU is auto-generated: SKU-000001…)."""
    title: str = Field(min_length=1, max_length=200)
    category: str | None = None
    season: str | None = None
    status: str = "Draft"
    retailPrice: Decimal | None = None
    wholesalePrice: Decimal | None = None
    onlinePrice: Decimal | None = None
    currency_code: str = "EUR"
    imageUrl: str | None = None
    # Specifications
    composition: str | None = Field(default=None, max_length=120)
    gauge: str | None = Field(default=None, max_length=40)
    care: str | None = Field(default=None, max_length=120)
    origin: str | None = Field(default=None, max_length=80)
    hsCode: str | None = Field(default=None, max_length=20)
    weight: str | None = Field(default=None, max_length=20)


class ProductUpdate(ProductCreate):
    """Same field set; price optional (updates the first variant)."""


class ProductOut(BaseModel):
    public_id: UUID
    title: str
    season: str | None
    status: str
    variant_count: int
    min_price: Decimal | None
    currency_code: str | None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent: str | None = None
    active: bool = False


class CategoryUpdate(CategoryCreate):
    """Same editable field set as create."""


class AttributeCreate(BaseModel):
    value: str = Field(min_length=1, max_length=60)
    type: str = Field(pattern="^(Color|Size)$")
    code: str = Field(min_length=1, max_length=20)


class AttributeUpdate(AttributeCreate):
    """Same editable field set as create."""

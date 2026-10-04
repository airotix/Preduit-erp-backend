"""Catalog API contracts (Pydantic v2), decoupled from ORM models."""
from decimal import Decimal
from uuid import UUID
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


class ProductCreate(BaseModel):
    """Matches the frontend 'New product' form. category/season are names;
    sku is required and must be unique per tenant (stored on the first variant)."""
    title: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=64)
    category: str | None = None
    season: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    status: str = "Draft"
    retailPrice: Decimal | None = None
    wholesalePrice: Decimal | None = None
    onlinePrice: Decimal | None = None
    supplierPrice: Decimal | None = None
    currency_code: str = "EUR"
    imageUrl: str | None = None
    # Specifications
    composition: str | None = Field(default=None, max_length=120)
    fabric: str | None = Field(default=None, max_length=120)
    gauge: str | None = Field(default=None, max_length=40)
    care: str | None = Field(default=None, max_length=120)
    origin: str | None = Field(default=None, max_length=80)
    hsCode: str | None = Field(default=None, max_length=20)
    weight: str | None = Field(default=None, max_length=20)


class ProductUpdate(ProductCreate):
    """Same field set; SKU optional so the detail editor can omit it."""
    sku: str | None = Field(default=None, max_length=64)


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

"""Catalog API contracts (Pydantic v2), decoupled from ORM models."""
from decimal import Decimal
from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints
from app.modules.inventory.dto import MatrixUpdate


class ProductCreate(BaseModel):
    """Matches the frontend 'New product' form. category/season are names;
    sku is required and must be unique per tenant (stored on the first variant)."""
    title: str = Field(min_length=1, max_length=200)
    sku: str = Field(pattern=r"^SKU-[0-9]{6}$", min_length=10, max_length=10)
    category: str | None = None
    season: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    status: Literal["Draft", "Active", "Discontinued"] = "Draft"
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
    sku: str | None = Field(default=None, pattern=r"^SKU-[0-9]{6}$", min_length=10, max_length=10)


class ProductDetailChanges(BaseModel):
    """Only fields explicitly sent by the detail editor are changed."""
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    season: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    status: Literal["Draft", "Active", "Discontinued"] | None = None
    imageUrl: str | None = None
    composition: str | None = Field(default=None, max_length=120)
    fabric: str | None = Field(default=None, max_length=120)
    gauge: str | None = Field(default=None, max_length=40)
    care: str | None = Field(default=None, max_length=120)
    origin: str | None = Field(default=None, max_length=80)
    hsCode: str | None = Field(default=None, max_length=20)
    weight: str | None = Field(default=None, max_length=20)


class ProductEditorSave(BaseModel):
    details: ProductDetailChanges
    matrix: MatrixUpdate


class VariantPriceChange(BaseModel):
    variantId: UUID
    retailPrice: Decimal | None = Field(ge=0, max_digits=19, decimal_places=4)
    wholesalePrice: Decimal | None = Field(ge=0, max_digits=19, decimal_places=4)
    onlinePrice: Decimal | None = Field(ge=0, max_digits=19, decimal_places=4)
    supplierPrice: Decimal | None = Field(ge=0, max_digits=19, decimal_places=4)


class VariantPricesUpdate(BaseModel):
    variants: list[VariantPriceChange] = Field(min_length=1)


class VariantGtinChange(BaseModel):
    variantId: UUID
    gtin: str | None = Field(default=None, max_length=64)


class VariantGtinsUpdate(BaseModel):
    variants: list[VariantGtinChange] = Field(min_length=1)


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


class StockDisposition(BaseModel):
    stockLevelId: UUID
    quantity: int = Field(gt=0)
    kind: str = Field(pattern="^(Write off|Return to supplier)$")
    reason: str = Field(min_length=3, max_length=400)
    reference: str | None = Field(default=None, max_length=80)


class StockReconciliation(BaseModel):
    reason: str = Field(min_length=3, max_length=400)

"""Procurement API contracts — match the frontend forms."""
from decimal import Decimal

from pydantic import BaseModel, Field


class POLineIn(BaseModel):
    """A single line on the New PO form (item + color + size + qty + price)."""
    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(default=None, max_length=60)
    size: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=64)
    qty: int = Field(gt=0)
    price: Decimal = Field(ge=0)


class PurchaseOrderCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    expected: str = Field(min_length=1, max_length=40)
    lines: list[POLineIn] = Field(min_length=1)


class GoodsReceiptCreate(BaseModel):
    po: str = Field(min_length=1, max_length=32)
    supplier: str = Field(min_length=1, max_length=200)
    lines: int = Field(gt=0)


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=80)
    leadTime: str = Field(min_length=1, max_length=40)
    category: str = Field(min_length=1, max_length=120)


class SupplierUpdate(SupplierCreate):
    """Same editable field set as create."""


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)

"""Procurement API contracts — match the frontend forms."""
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.validation import clean_email, clean_contact
from app.modules.inventory.dto import WarehouseReceiptCreate


class POLineIn(BaseModel):
    """A single line on the New PO form (item + color + size + qty + price)."""
    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(default=None, max_length=60)
    size: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=64)
    qty: int = Field(gt=0)
    price: Decimal | None = Field(default=None, ge=0)  # canonical price is read from Catalog


class PurchaseOrderCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    supplierId: UUID | None = None
    expected: str = Field(min_length=1, max_length=40)
    lines: list[POLineIn] = Field(min_length=1)

    @field_validator("expected")
    @classmethod
    def expected_date(cls, value):
        from app.modules.procurement.performance import iso_date
        if iso_date(value) is None:
            raise ValueError("Expected delivery must be a complete date in YYYY-MM-DD format")
        return value


class SupplierPOLineIn(POLineIn):
    supplierId: UUID


class GroupedPurchaseOrderCreate(BaseModel):
    requestId: UUID
    expected: str = Field(min_length=1, max_length=40)
    lines: list[SupplierPOLineIn] = Field(min_length=1, max_length=1000)

    @field_validator("expected")
    @classmethod
    def expected_date(cls, value):
        from app.modules.procurement.performance import iso_date
        if iso_date(value) is None:
            raise ValueError("Expected delivery must be a complete date in YYYY-MM-DD format")
        return value


class GoodsReceiptCreate(WarehouseReceiptCreate):
    po: str = Field(min_length=1, max_length=32)
    supplier: str | None = Field(default=None, max_length=200)


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Optional so auto-registered suppliers (created from a PO/receipt with no
    # profile yet) can be edited without re-entering these.
    region: str | None = Field(default=None, max_length=80)
    leadTime: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=256)
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    vatNumber: str | None = Field(default=None, max_length=40)
    contactPerson: str | None = Field(default=None, max_length=120)
    bankDetails: str | None = Field(default=None, max_length=400)
    # Structured banking details (auto-fill the invoice bank block).
    bankName: str | None = Field(default=None, max_length=120)
    bankAccountTitle: str | None = Field(default=None, max_length=120)
    bankAccountNumber: str | None = Field(default=None, max_length=60)
    bankSwift: str | None = Field(default=None, max_length=20)
    bankIban: str | None = Field(default=None, max_length=60)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        return clean_email(v)

    @model_validator(mode="after")
    def _v_contact(self):
        return clean_contact(self)


class SupplierUpdate(SupplierCreate):
    """Same editable field set as create."""


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)
    reason: str | None = Field(default=None, max_length=400)
    reviewToken: str | None = Field(default=None, max_length=64)
    acknowledged: bool = False


class ResolveImpact(BaseModel):
    key: str = Field(min_length=3, max_length=80)
    reason: str = Field(min_length=3, max_length=400)


class POExpectedUpdate(BaseModel):
    expected: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=3, max_length=400)
    reviewToken: str = Field(min_length=64, max_length=64)
    acknowledged: bool = False

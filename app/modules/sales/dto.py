"""Sales API contracts (Pydantic v2)."""
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    """Matches the frontend 'New customer' form + the customer detail card editor.
    email/type are lenient so a customer without an email can still be edited."""
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=256)
    type: str | None = Field(default="Retail", max_length=20)
    region: str | None = None
    phone: str | None = None
    address: str | None = None
    code: str | None = Field(default=None, max_length=20)
    terms: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, max_length=3)
    taxId: str | None = Field(default=None, max_length=40)
    bankName: str | None = Field(default=None, max_length=120)
    bankAccount: str | None = Field(default=None, max_length=60)
    contactTitle: str | None = Field(default=None, max_length=120)


class CustomerUpdate(CustomerCreate):
    """Same editable field set as create."""


class StatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=24)


class CustomerOut(BaseModel):
    public_id: UUID
    name: str
    email: str
    type: str
    region: str | None


class OrderLineIn(BaseModel):
    """A single line on the New order form (item + colour + size + qty + price)."""
    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(default=None, max_length=60)
    size: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=64)
    qty: int = Field(gt=0)
    price: Decimal = Field(ge=0)


class OrderCreate(BaseModel):
    """Matches the frontend 'New order' form (customer + channel + line items)."""
    customer: str = Field(min_length=1, max_length=200)
    channel: str = Field(pattern="^(Wholesale|Online|Marketplace|Retail)$")
    lines: list[OrderLineIn] = Field(min_length=1)


class InvoiceCreate(BaseModel):
    """Matches the frontend 'New invoice' form."""
    customer: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(ge=0)
    dueDate: str = Field(min_length=1, max_length=40)


class InvoiceSettleIn(BaseModel):
    """Payment confirmation for a receivable (New Order → payment modal)."""
    amountPaid: Decimal = Field(default=0, ge=0)
    paid: bool = False


class ReturnCreate(BaseModel):
    """Matches the frontend 'New return' form."""
    order: str = Field(min_length=1, max_length=40)
    customer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=80)
    refund: Decimal = Field(ge=0)

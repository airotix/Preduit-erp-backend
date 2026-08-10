"""Finance API contracts — match the frontend forms."""
from decimal import Decimal

from pydantic import BaseModel, Field


class LedgerEntryIn(BaseModel):
    """Manual customer-ledger entry from the 'New entry' button."""
    description: str = Field(min_length=1, max_length=400)
    debit: Decimal = Field(default=0, ge=0)
    credit: Decimal = Field(default=0, ge=0)


class LedgerDescriptionIn(BaseModel):
    """Inline edit of a ledger row's description."""
    type: str = Field(pattern="^(invoice|manual|payment|cn)$")
    publicId: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=400)


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern="^(Asset|Liability|Equity|Income|Expense)$")
    subtype: str | None = None
    currency: str = "EUR"
    openingBalance: Decimal | None = None
    taxRate: Decimal | None = None
    parent: str | None = None
    description: str | None = None
    active: bool = True


class AccountUpdate(AccountCreate):
    """Same editable field set as create."""


class JournalEntryCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=32)
    memo: str = Field(min_length=1, max_length=300)
    debit: Decimal = Field(gt=0)
    credit: Decimal = Field(gt=0)
    status: str = Field(default="Draft", pattern="^(Draft|Posted|Void)$")
    date: str | None = None


class JournalEntryUpdate(JournalEntryCreate):
    """Same editable field set as create."""


class JournalLineIn(BaseModel):
    account: str = Field(min_length=1, max_length=160)  # "code · name" or bare code
    description: str | None = None
    debit: Decimal = Field(default=0, ge=0)
    credit: Decimal = Field(default=0, ge=0)


class JournalFull(BaseModel):
    """A balanced, multi-line journal entry (real double-entry posting)."""
    reference: str | None = None
    date: str | None = None
    memo: str = Field(min_length=1, max_length=300)
    lines: list[JournalLineIn] = Field(min_length=2)


class PaymentCreate(BaseModel):
    party: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0)
    type: str = Field(pattern="^(Receipt|Disbursement)$")
    method: str | None = None
    reference: str | None = None
    allocatedTo: str | None = None
    date: str | None = None
    status: str = Field(default="Pending", pattern="^(Pending|Cleared|Failed)$")
    notes: str | None = None


class PaymentUpdate(PaymentCreate):
    """Same editable field set as create."""


class BillCreate(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    poRef: str | None = None
    amount: Decimal = Field(gt=0)
    dueDate: str | None = None
    status: str = Field(default="Open", pattern="^(Open|Scheduled|Paid)$")


class BillUpdate(BillCreate):
    """Same editable field set as create."""


class PeriodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    start_date: str
    end_date: str


class BudgetLineCreate(BaseModel):
    fiscal_year: int = Field(ge=2000, le=2100)
    account_code: str = Field(min_length=1, max_length=20)
    account_name: str | None = None
    amount: Decimal = Field(ge=0)


class FixedAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = None
    cost: Decimal = Field(gt=0)
    salvage: Decimal = Field(default=0, ge=0)
    life_months: int = Field(gt=0, le=1200)
    in_service_date: str | None = None


class BankAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_no: str | None = None
    gl_code: str | None = None
    currency: str = "EUR"


class BankTxnCreate(BaseModel):
    txn_date: str | None = None
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal  # + deposit, - withdrawal

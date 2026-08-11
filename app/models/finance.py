"""Finance ORM models."""
import datetime
import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SupplierBill(Base):
    """Payables — the source for live AP aging."""
    __tablename__ = "supplier_bills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    bill_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier_name: Mapped[str] = mapped_column(String(200))
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    po_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memo: Mapped[str | None] = mapped_column(String(400), nullable=True)  # editable ledger description
    issued_date: Mapped[datetime.date | None] = mapped_column(
        Date, default=datetime.date.today, nullable=True)  # ledger date = when added
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    due_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Open")
    gl_journal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Account(Base):
    __tablename__ = "chart_of_accounts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    acct_type: Mapped[str] = mapped_column(String(20))
    subtype: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR")
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    parent_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    normal_side: Mapped[str | None] = mapped_column(String(1), nullable=True)  # 'D' | 'C'
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    entry_no: Mapped[str] = mapped_column(String(32))
    entry_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entry_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    posted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    period_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reversed_of_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    total_debit: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    source_note: Mapped[str | None] = mapped_column(String, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    entry_id: Mapped[int] = mapped_column(BigInteger)
    account: Mapped[str] = mapped_column(String(160))
    account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payment_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pay_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    party: Mapped[str] = mapped_column(String(200))
    party_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # customer|supplier
    party_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    allocated_to: Mapped[str | None] = mapped_column(String(60), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    pay_type: Mapped[str] = mapped_column(String(20), default="Receipt")
    status: Mapped[str] = mapped_column(String(20), default="Pending")
    method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gl_journal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class CreditNote(Base):
    """Customer credit notes — credit entries on the customer ledger."""
    __tablename__ = "credit_notes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    cn_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    cn_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gl_journal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class LedgerEntry(Base):
    """Manual customer-ledger entry (a debit and/or credit with a description),
    created from the customer ledger 'New entry' button. Ledger-only — affects
    the customer's running balance but is not posted to the GL."""
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    entry_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class FiscalPeriod(Base):
    __tablename__ = "fiscal_periods"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[datetime.date] = mapped_column(Date)
    end_date: Mapped[datetime.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="Open")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class BudgetLine(Base):
    __tablename__ = "budget_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    account_code: Mapped[str] = mapped_column(String(20))
    account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class FixedAsset(Base):
    __tablename__ = "fixed_assets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    asset_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    salvage: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    life_months: Mapped[int] = mapped_column(Integer, default=36)
    in_service_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    accumulated: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    status: Mapped[str] = mapped_column(String(12), default="Active")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(120))
    account_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gl_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    bank_account_id: Mapped[int] = mapped_column(BigInteger)
    txn_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    matched_payment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="Unmatched")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ArAging(Base):
    __tablename__ = "ar_aging"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    customer_name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    current_amt: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b1_30: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b31_60: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b61_90: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b90_plus: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)


class ApAging(Base):
    __tablename__ = "ap_aging"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    supplier_name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    current_amt: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b1_30: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b31_60: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b61_90: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    b90_plus: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)

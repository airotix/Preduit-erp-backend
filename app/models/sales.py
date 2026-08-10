"""Sales ORM models."""
import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, Uuid, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(256))
    # 'type' is mapped to a differently-named attribute to avoid shadowing.
    kind: Mapped[str] = mapped_column("type", String(20), default="Retail")
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    terms: Mapped[str | None] = mapped_column(String(20), nullable=True)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(20), default="Online")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(20), default="New")
    order_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_id: Mapped[int] = mapped_column(BigInteger)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    invoice_id: Mapped[int] = mapped_column(BigInteger)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    invoice_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_no: Mapped[str | None] = mapped_column(String(32), nullable=True)  # source sales order
    memo: Mapped[str | None] = mapped_column(String(400), nullable=True)  # editable ledger description
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    issued_date: Mapped[datetime.date] = mapped_column(Date, default=datetime.date.today)
    due_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    due_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)  # real date for AR aging
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(20), default="Open")
    gl_journal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class SalesInvoice(Base):
    """Commercial/retail invoice generated against a sales order — full editable
    doc in `data`. Retail/Online use the flat receipt layout, Wholesale the
    colour×size matrix. Mirrors procurement's PoInvoice."""
    __tablename__ = "sales_invoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    invoice_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_type: Mapped[str] = mapped_column(String(20), default="Retail")
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class SalesReturn(Base):
    __tablename__ = "sales_returns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("NEWSEQUENTIALID()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    rma_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    refund: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(String(20), default="Inspecting")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

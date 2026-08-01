"""Procurement ORM models."""
import datetime
import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    lead_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    on_time_pct: Mapped[int] = mapped_column(Integer, default=0)
    defect_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    price_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="New")
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    terms: Mapped[str | None] = mapped_column(String(20), nullable=True)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    po_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    supplier_name: Mapped[str] = mapped_column(String(200))
    supplier_country: Mapped[str | None] = mapped_column(String(4), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR")
    expected: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="Pending approval")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    grn_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    po_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier_name: Mapped[str] = mapped_column(String(200))
    supplier_country: Mapped[str | None] = mapped_column(String(4), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="Expected")
    received_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    grn_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ordered: Mapped[int] = mapped_column(Integer, default=0)
    received: Mapped[int] = mapped_column(Integer, default=0)


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    po_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)


class PoInvoice(Base):
    """Commercial invoice generated against a PO — full editable doc in `data`."""
    __tablename__ = "po_invoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    invoice_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    po_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

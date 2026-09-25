"""Inventory ORM models."""
import uuid

from sqlalchemy import BigInteger, Boolean, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column("type", String(20), default="Warehouse")
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class StockLevel(Base):
    __tablename__ = "stock_levels"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    variant_id: Mapped[int] = mapped_column(BigInteger)
    location_id: Mapped[int] = mapped_column(BigInteger)
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class StockTransfer(Base):
    __tablename__ = "stock_transfers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    transfer_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    eta: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class StockTransferLine(Base):
    """One article (colour × size) moving on a stock transfer."""
    __tablename__ = "stock_transfer_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Client-side default so multi-line inserts don't rely on the server
    # gen_random_uuid() default (which fails under SQLAlchemy's batch INSERT).
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    transfer_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ReorderAlert(Base):
    __tablename__ = "reorder_alerts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    variant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sku: Mapped[str] = mapped_column(String(64))
    available: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, default=0)
    suggested: Mapped[int] = mapped_column(Integer, default=0)
    supplier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="Low")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

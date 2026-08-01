"""Shipments ORM models."""
import uuid

from sqlalchemy import BigInteger, Boolean, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    shipment_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="Label created")
    eta: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Carrier(Base):
    __tablename__ = "carriers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(120))
    service: Mapped[str | None] = mapped_column(String(80), nullable=True)
    avg_transit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    on_time_pct: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ShipmentLine(Base):
    __tablename__ = "shipment_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    shipment_id: Mapped[int] = mapped_column(BigInteger)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(200))
    qty: Mapped[int] = mapped_column(Integer, default=0)

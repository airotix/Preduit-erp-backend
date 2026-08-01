"""Production ORM models."""
import datetime
import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductionOrder(Base):
    __tablename__ = "production_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    style: Mapped[str] = mapped_column(String(200))
    factory: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(20), default="Cutting")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ProductionStage(Base):
    __tablename__ = "production_stages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_id: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(40))
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="Pending")
    start_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    end_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    worker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(400), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class BomLine(Base):
    __tablename__ = "bill_of_materials"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    component: Mapped[str] = mapped_column(String(200))
    style: Mapped[str | None] = mapped_column(String(200), nullable=True)
    material: Mapped[str | None] = mapped_column(String(80), nullable=True)
    qty_per_unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

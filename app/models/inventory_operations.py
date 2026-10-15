"""Warehouse planning and controlled physical-count workflows."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class ReplenishmentRule(Base):
    __tablename__ = "inventory_replenishment_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "variant_id", "location_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_variants.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    minimum: Mapped[int] = mapped_column(Integer)
    target: Mapped[int] = mapped_column(Integer)
    lead_days: Mapped[int] = mapped_column(Integer)
    minimum_order: Mapped[int] = mapped_column(Integer, default=1)
    pack_multiple: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Stocktake(Base):
    __tablename__ = "inventory_stocktakes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    reference: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(400))
    counter: Mapped[str] = mapped_column(String(256))
    creator: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(20), default="Counting")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StocktakeLine(Base):
    __tablename__ = "inventory_stocktake_lines"
    __table_args__ = (UniqueConstraint("stocktake_id", "variant_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    stocktake_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("inventory_stocktakes.id"))
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_variants.id"))
    label: Mapped[str] = mapped_column(String(400))
    book_qty: Mapped[int] = mapped_column(Integer)
    movement_id: Mapped[int] = mapped_column(BigInteger, default=0)
    counted_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recount_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReturnLine(Base):
    __tablename__ = "inventory_return_lines"
    __table_args__ = (UniqueConstraint("return_id", "order_line_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    return_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_returns.id"))
    order_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order_lines.id"))
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_variants.id"))
    label: Mapped[str] = mapped_column(String(400))
    requested: Mapped[int] = mapped_column(Integer)
    cancelled: Mapped[int] = mapped_column(Integer, default=0)
    received: Mapped[int] = mapped_column(Integer, default=0)
    ready: Mapped[int] = mapped_column(Integer, default=0)
    repair: Mapped[int] = mapped_column(Integer, default=0)
    scrap: Mapped[int] = mapped_column(Integer, default=0)
    restocked: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowEvent(Base):
    __tablename__ = "inventory_workflow_events"
    __table_args__ = (UniqueConstraint("tenant_id", "request_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

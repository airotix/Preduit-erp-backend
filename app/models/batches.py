"""Quantity ledgers for production, QC, warehouse lots and dispatch allocations."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Integer, String, Text, DateTime, Uuid, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Identity:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)


class ProductionBatch(Identity, Base):
    __tablename__ = "production_batches"
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_orders.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    factory: Mapped[str | None] = mapped_column(String(120))
    due_on: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(24), default="Planned")


class BatchLine(Identity, Base):
    __tablename__ = "production_batch_lines"
    __table_args__ = (UniqueConstraint("batch_id", "po_line_id"),
        CheckConstraint("planned > 0 AND completed >= 0 AND completed <= planned"),
        CheckConstraint("approved >= 0 AND held >= 0 AND rejected >= 0 AND approved + held + rejected <= completed"),
        CheckConstraint("received >= 0 AND received <= approved"))
    batch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_batches.id"), index=True)
    po_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_order_lines.id"))
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_variants.id"))
    planned: Mapped[int] = mapped_column(Integer)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    held: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    received: Mapped[int] = mapped_column(Integer, default=0)


class BatchStage(Identity, Base):
    __tablename__ = "production_batch_stages"
    __table_args__ = (UniqueConstraint("batch_id", "seq"),)
    batch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_batches.id"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="Pending")
    worker: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(400))


class BatchLot(Identity, Base):
    __tablename__ = "production_batch_lots"
    __table_args__ = (UniqueConstraint("line_id", "location_id"),
        CheckConstraint("on_hand >= 0 AND allocated >= 0 AND allocated <= on_hand"))
    line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_batch_lines.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    allocated: Mapped[int] = mapped_column(Integer, default=0)


class BatchAllocation(Identity, Base):
    __tablename__ = "shipment_batch_allocations"
    __table_args__ = (CheckConstraint("qty > 0"), UniqueConstraint("shipment_line_id", "lot_id"))
    shipment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shipments.id"), index=True)
    shipment_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shipment_lines.id"))
    lot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_batch_lots.id"))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="Reserved")


class BatchEvent(Identity, Base):
    __tablename__ = "production_batch_events"
    __table_args__ = (UniqueConstraint("tenant_id", "request_id"),)
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("production_batches.id"))
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payload: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text, default="{}")


class InventoryAllocation(Identity, Base):
    """Older reserved stock has a warehouse origin, never an invented production batch."""
    __tablename__ = "shipment_inventory_allocations"
    __table_args__ = (CheckConstraint("qty > 0"), UniqueConstraint("shipment_id", "order_line_id", "stock_level_id"))
    shipment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shipments.id"), index=True)
    shipment_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shipment_lines.id"))
    order_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order_lines.id"))
    stock_level_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stock_levels.id"))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="Reserved")

"""Production ORM models."""
import datetime
import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ProductionOrder(Base):
    __tablename__ = "production_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Summary style label for the order (e.g. "Merino Crew Knit +1 more"); the
    # per-item breakdown lives in ProductionOrderLine.
    style: Mapped[str] = mapped_column(String(200))
    factory: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)          # total qty across all lines
    stage: Mapped[str] = mapped_column(String(20), default="Trims")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    sales_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationship to lines (lazy dynamic for per-line queries)
    lines: Mapped[list["ProductionOrderLine"]] = relationship(
        back_populates="order", lazy="dynamic", cascade="all, delete-orphan"
    )


class ProductionOrderLine(Base):
    """One style/item within a production order — each gets its own stage timeline."""
    __tablename__ = "production_order_lines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_orders.id"))
    sales_order_line_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(200))         # style / product name
    color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # NOTE: the table has a ROWVERSION column, but it is intentionally NOT mapped.
    # ROWVERSION is server-generated and cannot appear in an INSERT/UPDATE column
    # list — mapping it makes SQLAlchemy emit an explicit NULL and SQL Server
    # rejects it ("Cannot insert an explicit value into a timestamp column").

    order: Mapped["ProductionOrder"] = relationship(back_populates="lines")
    stages: Mapped[list["ProductionStage"]] = relationship(
        back_populates="line", lazy="dynamic", cascade="all, delete-orphan"
    )


class ProductionStage(Base):
    __tablename__ = "production_stages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("production_orders.id"), nullable=True)
    line_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("production_order_lines.id"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(40))
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="Pending")
    start_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    end_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    worker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(400), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped["ProductionOrder"] = relationship(lazy="joined")
    line: Mapped["ProductionOrderLine"] = relationship(back_populates="stages", lazy="joined")


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

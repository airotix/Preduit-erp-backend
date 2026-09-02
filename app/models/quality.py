"""Quality ORM models."""
import datetime
import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    inspection_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    item: Mapped[str | None] = mapped_column(String(200), nullable=True)  # per-item (production line)
    stage: Mapped[str] = mapped_column(String(20), default="Final")
    aql: Mapped[str | None] = mapped_column(String(10), nullable=True)
    defect_count: Mapped[int] = mapped_column(Integer, default=0)
    # Pending | In Progress | Pass | Fail | Cancelled  (Re-inspection is modelled
    # via parent_inspection_id; Conditional Pass reserved for a later phase).
    result: Mapped[str] = mapped_column(String(20), default="Pending")
    inspector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # ---- enriched header (V060) ----
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product: Mapped[str | None] = mapped_column(String(200), nullable=True)
    batch_lot: Mapped[str | None] = mapped_column(String(60), nullable=True)
    inspection_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prod_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_defects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inspection_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # ---- disposition + linkage (V060) ----
    disposition: Mapped[str | None] = mapped_column(String(24), nullable=True)
    disposition_notes: Mapped[str | None] = mapped_column(String(400), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True)
    due_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    parent_inspection_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shipment_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class InspectionCheck(Base):
    """One checklist criterion within an inspection."""
    __tablename__ = "inspection_checks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4,
                                                 server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    inspection_id: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    criterion: Mapped[str] = mapped_column(String(120))
    requirement: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    tolerance: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    actual: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result: Mapped[str] = mapped_column(String(12), default="Pending")  # Pass|Fail|Warning|NA|Pending
    notes: Mapped[str | None] = mapped_column(String(400), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class InspectionDefect(Base):
    """One defect logged against an inspection (links to the DefectType catalog)."""
    __tablename__ = "inspection_defects"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4,
                                                 server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    inspection_id: Mapped[int] = mapped_column(BigInteger)
    defect_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    defect_type_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    defect_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    qty_affected: Mapped[int] = mapped_column(Integer, default=1)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    corrective: Mapped[str | None] = mapped_column(String(400), nullable=True)
    image_doc_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class DefectType(Base):
    __tablename__ = "defect_types"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    frequency: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

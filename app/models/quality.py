"""Quality ORM models."""
import uuid

from sqlalchemy import BigInteger, Boolean, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    inspection_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    stage: Mapped[str] = mapped_column(String(20), default="Final")
    aql: Mapped[str | None] = mapped_column(String(10), nullable=True)
    defect_count: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(20), default="Pending")
    inspector: Mapped[str | None] = mapped_column(String(120), nullable=True)
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

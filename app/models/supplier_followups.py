import uuid
from datetime import datetime, date, timezone
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SupplierFollowup(Base):
    __tablename__ = 'supplier_followups'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('suppliers.id'))
    source_type: Mapped[str | None] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    summary: Mapped[str] = mapped_column(String(240))
    owner: Mapped[str] = mapped_column(String(256))
    due_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default='Open')
    resolution: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_payload: Mapped[str] = mapped_column(Text)

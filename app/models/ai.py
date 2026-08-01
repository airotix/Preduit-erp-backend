"""AI Insights ORM models — materialised snapshots of the forecasting engine."""
import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiSnapshot(Base):
    __tablename__ = "ai_snapshot"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(200), default="")
    data: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime.datetime] = mapped_column(DateTime)


class AiSyncState(Base):
    __tablename__ = "ai_sync_state"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    last_synced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="idle")
    message: Mapped[str | None] = mapped_column(String(400), nullable=True)

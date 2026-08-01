"""Document (file upload) ORM model."""
import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    doc_id: Mapped[str] = mapped_column(String(40))
    module: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    filename: Mapped[str] = mapped_column(String(260))
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_path: Mapped[str] = mapped_column(String(400))
    uploaded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

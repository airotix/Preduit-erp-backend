"""Approval history for Catalog and production-line Tech Pack PDFs."""
import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TechPackRevision(Base):
    __tablename__ = "tech_pack_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("products.id"), nullable=True)
    production_line_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("production_order_lines.id"), nullable=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"))
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    approved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

"""Admin ORM models."""
import uuid

from sqlalchemy import BigInteger, Boolean, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApprovalRule(Base):
    __tablename__ = "approval_rules"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(200))
    condition: Mapped[str | None] = mapped_column("condition", String(300), nullable=True)
    approver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

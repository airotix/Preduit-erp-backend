"""Core platform ORM models (subset used by onboarding)."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    base_currency_code: Mapped[str] = mapped_column(String(3))
    region: Mapped[str] = mapped_column(String(40), default="primary")
    status: Mapped[str] = mapped_column(String(20), default="Active")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    plan: Mapped[str] = mapped_column(String(40), default="trial")
    status: Mapped[str] = mapped_column(String(20), default="trialing")
    seat_limit: Mapped[int] = mapped_column(default=5)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    email: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_active: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, server_default=text("NEWSEQUENTIALID()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)

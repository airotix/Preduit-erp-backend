"""Catalog purchase prices by exact variant and supplier."""
import uuid
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SupplierPrice(Base):
    __tablename__ = "catalog_supplier_prices"
    __table_args__ = (UniqueConstraint("tenant_id", "variant_id", "supplier_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    variant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_variants.id"))
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

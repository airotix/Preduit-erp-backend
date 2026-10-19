import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SupplierReturn(Base):
    __tablename__ = "supplier_returns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_payload: Mapped[str] = mapped_column(Text)
    po_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_order_lines.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    lot_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("production_batch_lots.id"))
    stock_entry_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("inventory_stock_entries.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    resolution: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), default="Awaiting supplier")
    reference: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(400))
    actor: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    supplier_bill_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("supplier_bills.id"))
    credit_reference: Mapped[str | None] = mapped_column(String(80))
    credit_on: Mapped[date | None] = mapped_column(Date)
    credit_journal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("journal_entries.id"))
    refund_reference: Mapped[str | None] = mapped_column(String(80))
    refund_on: Mapped[date | None] = mapped_column(Date)
    refund_channel: Mapped[str | None] = mapped_column(String(10))
    refund_journal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("journal_entries.id"))
    inspection_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("inspections.id"))
    replaced: Mapped[bool] = mapped_column(Boolean, default=False)

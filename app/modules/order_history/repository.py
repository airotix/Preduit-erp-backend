"""Order History data access (tenant-scoped by RLS) — read-only.

A sales order counts as "in Order History" once it's far enough along its own
fulfillment status (Packed/Shipped) OR its linked shipment has independently
reached Delivered. These two signals aren't kept in sync elsewhere in the app
(nothing writes back to SalesOrder.status when a shipment is created), so both
are checked here.
"""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.production import ProductionOrder
from app.models.quality import Inspection
from app.models.sales import SalesOrder
from app.models.shipments import Shipment

_STATUSES = ("Packed", "Shipped")


def _shipped_condition():
    delivered = (
        select(func.count()).select_from(Shipment)
        .where(Shipment.order_ref == SalesOrder.order_no, Shipment.status == "Delivered",
               Shipment.is_deleted == False)  # noqa: E712
        .correlate(SalesOrder).scalar_subquery()
    )
    return or_(SalesOrder.status.in_(_STATUSES), delivered > 0)


def list_shipped_orders(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    cond = _shipped_condition()
    stmt = (
        select(SalesOrder.public_id, SalesOrder.order_no, SalesOrder.customer_name,
               SalesOrder.channel, SalesOrder.item_count, SalesOrder.total,
               SalesOrder.currency_code, SalesOrder.status, SalesOrder.order_date)
        .where(SalesOrder.is_deleted == False, cond)  # noqa: E712
        .order_by(SalesOrder.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(SalesOrder)
        .where(SalesOrder.is_deleted == False, cond)  # noqa: E712
    ).scalar_one()
    return rows, total


def order_by_public(session: Session, *, public_id: str) -> SalesOrder | None:
    return session.execute(
        select(SalesOrder).where(SalesOrder.public_id == public_id,
                                 SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def production_order_for_sales_order(session: Session, *, sales_order_id: int) -> ProductionOrder | None:
    return session.execute(
        select(ProductionOrder).where(ProductionOrder.sales_order_id == sales_order_id,
                                      ProductionOrder.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrder.id.desc())
    ).scalars().first()


def inspection_for_order(session: Session, *, order_no: str | None) -> Inspection | None:
    if not order_no:
        return None
    return session.execute(
        select(Inspection).where(Inspection.order_ref == order_no,
                                 Inspection.is_deleted == False)  # noqa: E712
        .order_by(Inspection.id.desc())
    ).scalars().first()


def shipment_for_order(session: Session, *, order_no: str | None) -> Shipment | None:
    if not order_no:
        return None
    return session.execute(
        select(Shipment).where(Shipment.order_ref == order_no,
                               Shipment.is_deleted == False)  # noqa: E712
        .order_by(Shipment.id.desc())
    ).scalars().first()

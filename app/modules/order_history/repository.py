"""Order History data access (tenant-scoped by RLS) — read-only.

A sales order counts as "in Order History" once it's far enough along its own
fulfillment status (Packed/Shipped) OR its linked shipment has independently
reached Delivered. These two signals aren't kept in sync elsewhere in the app
(nothing writes back to SalesOrder.status when a shipment is created), so both
are checked here.

An accepted warehouse receipt also creates an entry: the sales order behind the
purchase order, or the purchase order itself when it has no sales order.
"""
import datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.procurement import GoodsReceipt, PurchaseOrder
from app.models.production import ProductionOrder
from app.models.quality import Inspection
from app.models.sales import SalesOrder
from app.models.shipments import Shipment

_STATUSES = ("Packed", "Shipped")


def _received_condition():
    """A purchase order with at least one accepted warehouse receipt."""
    receipt = exists().where(GoodsReceipt.po_ref == PurchaseOrder.po_no, GoodsReceipt.status == "Received",
                             GoodsReceipt.is_deleted == False)  # noqa: E712
    return or_(PurchaseOrder.inventory_received.is_(True),
               PurchaseOrder.status.in_(("Received", "Partially received")), receipt)


def _shipped_condition():
    delivered = (
        select(func.count()).select_from(Shipment)
        .where(Shipment.order_ref == SalesOrder.order_no, Shipment.status == "Delivered",
               Shipment.is_deleted == False)  # noqa: E712
        .correlate(SalesOrder).scalar_subquery()
    )
    received_po = exists().where(PurchaseOrder.sales_order_id == SalesOrder.id,
                                 PurchaseOrder.is_deleted == False, _received_condition())  # noqa: E712
    return or_(SalesOrder.status.in_(_STATUSES), delivered > 0, received_po)


def list_entries(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    """Sales orders plus received purchase orders that have no sales order, newest first."""
    cond = _shipped_condition()
    sales = [dict(r._mapping, kind="sales") for r in session.execute(
        select(SalesOrder.public_id, SalesOrder.order_no, SalesOrder.customer_name,
               SalesOrder.channel, SalesOrder.item_count, SalesOrder.total,
               SalesOrder.currency_code, SalesOrder.status, SalesOrder.order_date, SalesOrder.id)
        .where(SalesOrder.is_deleted == False, cond)  # noqa: E712
        .order_by(SalesOrder.id.desc()))]
    first_receipt = (select(func.min(GoodsReceipt.received_on))
                     .where(GoodsReceipt.po_ref == PurchaseOrder.po_no, GoodsReceipt.is_deleted == False)  # noqa: E712
                     .correlate(PurchaseOrder).scalar_subquery())
    purchases = [dict(r._mapping, kind="purchase", channel="Purchase order") for r in session.execute(
        select(PurchaseOrder.public_id, PurchaseOrder.po_no.label("order_no"),
               PurchaseOrder.supplier_name.label("customer_name"), PurchaseOrder.item_count,
               PurchaseOrder.total, PurchaseOrder.currency_code, PurchaseOrder.status,
               first_receipt.label("order_date"), PurchaseOrder.id)
        .where(PurchaseOrder.is_deleted == False, PurchaseOrder.sales_order_id.is_(None),  # noqa: E712
               _received_condition())
        .order_by(PurchaseOrder.id.desc()))]
    rows = sorted(sales + purchases, key=lambda r: (r["order_date"] or datetime.date.min, r["id"]), reverse=True)
    return rows[offset:offset + limit], len(rows)


def order_by_public(session: Session, *, public_id: str) -> SalesOrder | None:
    return session.execute(
        select(SalesOrder).where(SalesOrder.public_id == public_id,
                                 SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def purchase_order_by_public(session: Session, *, public_id: str) -> PurchaseOrder | None:
    return session.execute(
        select(PurchaseOrder).where(PurchaseOrder.public_id == public_id,
                                    PurchaseOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def purchase_orders_for_sales_order(session: Session, *, sales_order_id: int) -> list[PurchaseOrder]:
    return list(session.scalars(
        select(PurchaseOrder).where(PurchaseOrder.sales_order_id == sales_order_id,
                                    PurchaseOrder.is_deleted == False)  # noqa: E712
        .order_by(PurchaseOrder.id)))


def production_order_for_purchase_orders(session: Session, *, po_ids: list[int]) -> ProductionOrder | None:
    if not po_ids:
        return None
    return session.execute(
        select(ProductionOrder).where(ProductionOrder.purchase_order_id.in_(po_ids),
                                      ProductionOrder.is_deleted == False)  # noqa: E712
        .order_by(ProductionOrder.id.desc())
    ).scalars().first()


def inspections_for_refs(session: Session, *, refs: list[str]) -> list[Inspection]:
    refs = [r for r in refs if r]
    if not refs:
        return []
    return list(session.scalars(
        select(Inspection).where(Inspection.order_ref.in_(refs), Inspection.is_deleted == False)  # noqa: E712
        .order_by(Inspection.id)))


def shipments_for(session: Session, *, sales_order_id: int | None, po_ids: list[int], refs: list[str]) -> list[Shipment]:
    refs = [r for r in refs if r]
    conds = []
    if sales_order_id is not None:
        conds.append(Shipment.sales_order_id == sales_order_id)
    if po_ids:
        conds.append(Shipment.purchase_order_id.in_(po_ids))
    if refs:
        conds.append(Shipment.order_ref.in_(refs))
    if not conds:
        return []
    return list(session.scalars(
        select(Shipment).where(Shipment.is_deleted == False, or_(*conds))  # noqa: E712
        .order_by(Shipment.id)))


def receipts_for_po_nos(session: Session, *, po_nos: list[str]) -> list[GoodsReceipt]:
    po_nos = [n for n in po_nos if n]
    if not po_nos:
        return []
    return list(session.scalars(
        select(GoodsReceipt).where(GoodsReceipt.po_ref.in_(po_nos), GoodsReceipt.is_deleted == False)  # noqa: E712
        .order_by(GoodsReceipt.id)))


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

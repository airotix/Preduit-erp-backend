"""Procurement data access. Tenant-filtered automatically by RLS."""
import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.catalog import Product
from app.models.procurement import (
    GoodsReceipt, GoodsReceiptLine, PoInvoice, PurchaseOrder, PurchaseOrderLine, Supplier,
)


def find_supplier(session: Session, *, name: str) -> Supplier | None:
    return session.execute(
        select(Supplier).where(Supplier.name == name)
    ).scalar_one_or_none()


# ---------- Suppliers / scorecard ----------

def list_suppliers(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(Supplier.public_id, Supplier.name, Supplier.region, Supplier.category,
               Supplier.lead_time, Supplier.on_time_pct, Supplier.defect_rate,
               Supplier.price_rating, Supplier.score, Supplier.status)
        .where(Supplier.is_deleted == False)  # noqa: E712
        .order_by(Supplier.name)
        .limit(limit).offset(offset)
    )  # public_id drives the supplier/scorecard detail drill-down
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Supplier).where(Supplier.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_supplier(session: Session, *, tenant_id: UUID, name: str, region: str,
                    lead_time: str, category: str) -> Supplier:
    sup = Supplier(tenant_id=tenant_id, name=name, region=region,
                   lead_time=lead_time, category=category, status="New")
    session.add(sup)
    session.flush()
    session.refresh(sup)
    return sup


def update_supplier(session: Session, *, public_id: str, name: str, region: str,
                    lead_time: str, category: str) -> Supplier | None:
    sup = session.execute(
        select(Supplier).where(Supplier.public_id == public_id,
                               Supplier.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if sup is None:
        return None
    sup.name = name
    sup.region = region
    sup.lead_time = lead_time
    sup.category = category
    session.flush()
    session.refresh(sup)
    return sup


# ---------- Purchase orders ----------

def list_pos(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(PurchaseOrder.public_id, PurchaseOrder.po_no, PurchaseOrder.supplier_name,
               PurchaseOrder.supplier_country, PurchaseOrder.item_count, PurchaseOrder.total,
               PurchaseOrder.expected, PurchaseOrder.status)
        .where(PurchaseOrder.is_deleted == False)  # noqa: E712
        .order_by(PurchaseOrder.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def list_pos_for_board(session: Session) -> list[dict]:
    stmt = (
        select(PurchaseOrder.public_id, PurchaseOrder.po_no, PurchaseOrder.supplier_name,
               PurchaseOrder.item_count, PurchaseOrder.total, PurchaseOrder.status,
               Supplier.category)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(PurchaseOrder.is_deleted == False)  # noqa: E712
        .order_by(PurchaseOrder.id.desc())
    )
    return [dict(r._mapping) for r in session.execute(stmt)]


def create_po(session: Session, *, tenant_id: UUID, supplier: Supplier | None,
              supplier_name: str, expected: str, lines: list[dict]) -> PurchaseOrder:
    item_count = sum(int(l["qty"]) for l in lines)
    total = sum((Decimal(str(l["price"])) * int(l["qty"]) for l in lines), Decimal("0"))
    po = PurchaseOrder(
        tenant_id=tenant_id,
        supplier_id=supplier.id if supplier else None,
        supplier_name=supplier_name,
        supplier_country=supplier.country_code if supplier else None,
        item_count=item_count, total=total, expected=expected, status="Pending approval",
    )
    session.add(po)
    session.flush()
    po.po_no = f"PO-{5500 + po.id}"
    for l in lines:
        qty = int(l["qty"])
        price = Decimal(str(l["price"]))
        session.add(PurchaseOrderLine(
            tenant_id=tenant_id, po_id=po.id, name=l["name"], color=l.get("color"),
            size=l.get("size"), sku=l.get("sku"), qty=qty, price=price,
            line_total=price * qty,
        ))
    session.flush()
    session.refresh(po)
    return po


def get_po_detail(session: Session, *, public_id: str) -> dict | None:
    po = session.execute(
        select(PurchaseOrder).where(PurchaseOrder.public_id == public_id,
                                    PurchaseOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if po is None:
        return None
    lines = [dict(r._mapping) for r in session.execute(
        select(PurchaseOrderLine.name, PurchaseOrderLine.color, PurchaseOrderLine.size,
               PurchaseOrderLine.sku, PurchaseOrderLine.qty, PurchaseOrderLine.price,
               PurchaseOrderLine.line_total)
        .where(PurchaseOrderLine.po_id == po.id)
    )]
    supplier = session.get(Supplier, po.supplier_id) if po.supplier_id else None
    return {"po": po, "lines": lines, "supplier": supplier}


# ---------- PO commercial invoices ----------

def po_by_no(session: Session, *, po_no: str) -> PurchaseOrder | None:
    return session.execute(
        select(PurchaseOrder).where(func.upper(PurchaseOrder.po_no) == po_no.upper().strip(),
                                    PurchaseOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def po_lines(session: Session, *, po_id: int) -> list[dict]:
    return [dict(r._mapping) for r in session.execute(
        select(PurchaseOrderLine.name, PurchaseOrderLine.color, PurchaseOrderLine.size,
               PurchaseOrderLine.sku, PurchaseOrderLine.qty, PurchaseOrderLine.price)
        .where(PurchaseOrderLine.po_id == po_id)
        .order_by(PurchaseOrderLine.id)
    )]


def tenant_name(session: Session) -> str | None:
    return session.execute(text(
        "SELECT name FROM dbo.tenants WHERE id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)"
    )).scalar()


def product_images(session: Session) -> dict[str, str]:
    """Map product title → image URL (for pulling article photos onto invoices)."""
    rows = session.execute(
        select(Product.title, Product.image_url)
        .where(Product.is_deleted == False, Product.image_url.isnot(None))  # noqa: E712
    ).all()
    return {t: u for t, u in rows if u}


def create_po_invoice(session: Session, *, tenant_id: UUID, invoice_no, po_no, supplier_name,
                      currency_code, total, data: str) -> PoInvoice:
    inv = PoInvoice(tenant_id=tenant_id, invoice_no=invoice_no, po_no=po_no,
                    supplier_name=supplier_name, currency_code=currency_code, total=total,
                    status="Draft", data=data, created_at=datetime.datetime.utcnow())
    session.add(inv)
    session.flush()
    if not inv.invoice_no:
        inv.invoice_no = f"CI-{4400 + inv.id}"
        session.flush()
    return inv


def list_po_invoices(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(PoInvoice.public_id, PoInvoice.invoice_no, PoInvoice.po_no,
               PoInvoice.supplier_name, PoInvoice.currency_code, PoInvoice.total,
               PoInvoice.status, PoInvoice.created_at)
        .where(PoInvoice.is_deleted == False)  # noqa: E712
        .order_by(PoInvoice.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(PoInvoice).where(PoInvoice.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def get_po_invoice(session: Session, *, public_id: str) -> PoInvoice | None:
    return session.execute(
        select(PoInvoice).where(PoInvoice.public_id == public_id,
                                PoInvoice.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


# ---------- Goods receipts ----------

def list_receipts(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(GoodsReceipt.public_id, GoodsReceipt.grn_no, GoodsReceipt.po_ref,
               GoodsReceipt.supplier_name, GoodsReceipt.supplier_country,
               GoodsReceipt.line_count, GoodsReceipt.received_count, GoodsReceipt.status)
        .where(GoodsReceipt.is_deleted == False)  # noqa: E712
        .order_by(GoodsReceipt.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_receipt(session: Session, *, tenant_id: UUID, po_ref: str, supplier: Supplier | None,
                   supplier_name: str, lines: int) -> GoodsReceipt:
    grn = GoodsReceipt(
        tenant_id=tenant_id, po_ref=po_ref, supplier_name=supplier_name,
        supplier_country=supplier.country_code if supplier else None,
        line_count=lines, received_count=0, status="Expected",
    )
    session.add(grn)
    session.flush()
    grn.grn_no = f"GRN-{3300 + grn.id}"
    session.flush()
    session.refresh(grn)
    return grn


# ---------- Detail fetchers ----------

def _set_status(session: Session, model, public_id: str, status: str):
    obj = session.execute(
        select(model).where(model.public_id == public_id, model.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if obj is None:
        return None
    obj.status = status
    session.flush()
    return obj


def set_po_status(session, *, public_id, status):
    return _set_status(session, PurchaseOrder, public_id, status)


def set_receipt_status(session, *, public_id, status):
    return _set_status(session, GoodsReceipt, public_id, status)


def get_supplier_detail(session: Session, *, public_id: str) -> dict | None:
    supplier = session.execute(
        select(Supplier).where(Supplier.public_id == public_id,
                               Supplier.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if supplier is None:
        return None
    pos = [dict(r._mapping) for r in session.execute(
        select(PurchaseOrder.po_no, PurchaseOrder.item_count, PurchaseOrder.total,
               PurchaseOrder.status)
        .where(PurchaseOrder.supplier_id == supplier.id,
               PurchaseOrder.is_deleted == False)  # noqa: E712
        .order_by(PurchaseOrder.id.desc())
    )]
    ytd = sum((p["total"] or 0) for p in pos)
    open_pos = sum(1 for p in pos if p["status"] in ("Pending approval", "Approved"))
    return {"supplier": supplier, "pos": pos, "ytd": ytd, "open_pos": open_pos}


def get_receipt_detail(session: Session, *, public_id: str) -> dict | None:
    grn = session.execute(
        select(GoodsReceipt).where(GoodsReceipt.public_id == public_id,
                                   GoodsReceipt.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if grn is None:
        return None
    lines = [dict(r._mapping) for r in session.execute(
        select(GoodsReceiptLine.name, GoodsReceiptLine.sku,
               GoodsReceiptLine.ordered, GoodsReceiptLine.received)
        .where(GoodsReceiptLine.grn_id == grn.id)
    )]
    return {"grn": grn, "lines": lines}

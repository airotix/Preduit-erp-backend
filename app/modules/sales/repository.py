"""Sales data access. Tenant-filtered automatically by RLS."""
import datetime
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, aliased

from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.sales import (
    Customer, Invoice, InvoiceLine, SalesInvoice, SalesOrder, SalesOrderLine, SalesReturn,
)


def list_customers(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(Customer.public_id, Customer.name, Customer.email, Customer.kind,
               Customer.region, Customer.phone, Customer.address)
        .where(Customer.is_deleted == False)  # noqa: E712
        .order_by(Customer.name)
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Customer).where(Customer.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def _apply_customer_fields(c: Customer, *, name, email, kind, region, phone, address) -> None:
    c.name = name
    c.email = email
    c.kind = kind
    c.region = region
    c.phone = phone
    c.address = address


def create_customer(session: Session, *, tenant_id: UUID, **fields) -> Customer:
    customer = Customer(tenant_id=tenant_id)
    _apply_customer_fields(customer, **fields)
    session.add(customer)
    session.flush()
    session.refresh(customer)
    return customer


def update_customer(session: Session, *, public_id: str, **fields) -> Customer | None:
    c = session.execute(
        select(Customer).where(Customer.public_id == public_id,
                               Customer.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if c is None:
        return None
    _apply_customer_fields(c, **fields)
    session.flush()
    session.refresh(c)
    return c


def find_customer_id(session: Session, *, name: str) -> int | None:
    return session.execute(
        select(Customer.id).where(Customer.name == name)
    ).scalar_one_or_none()


def order_stats_by_customer(session: Session) -> dict[str, tuple[int, float]]:
    """Per-customer order count + lifetime value, keyed by customer name."""
    rows = session.execute(
        select(SalesOrder.customer_name, func.count(),
               func.coalesce(func.sum(SalesOrder.total), 0))
        .where(SalesOrder.is_deleted == False)  # noqa: E712
        .group_by(SalesOrder.customer_name)
    ).all()
    return {name: (int(cnt), float(tot or 0)) for name, cnt, tot in rows}


# ---------- Orders ----------

def list_orders(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(
            SalesOrder.public_id, SalesOrder.order_no, SalesOrder.customer_name,
            SalesOrder.channel, SalesOrder.item_count, SalesOrder.total,
            SalesOrder.currency_code, SalesOrder.status,
        )
        .where(SalesOrder.is_deleted == False)  # noqa: E712
        .order_by(SalesOrder.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(SalesOrder).where(SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_order(session: Session, *, tenant_id: UUID, customer_id: int | None,
                 customer_name: str, channel: str, lines: list[dict]) -> SalesOrder:
    item_count = sum(int(l["qty"]) for l in lines)
    total = sum((Decimal(str(l["price"])) * int(l["qty"]) for l in lines), Decimal("0"))
    order = SalesOrder(
        tenant_id=tenant_id, customer_id=customer_id, customer_name=customer_name,
        channel=channel, item_count=item_count, total=total, status="New",
        order_date=date.today(),
    )
    session.add(order)
    session.flush()  # id now available
    order.order_no = f"SO-{12000 + order.id}"
    for l in lines:
        qty = int(l["qty"])
        price = Decimal(str(l["price"]))
        session.add(SalesOrderLine(
            tenant_id=tenant_id, order_id=order.id, name=l["name"],
            color=l.get("color"), size=l.get("size"),
            sku=l.get("sku"), qty=qty, price=price, line_total=price * qty,
        ))
    session.flush()
    session.refresh(order)
    return order


# ---------- Invoices ----------

def list_invoices(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(Invoice.public_id, Invoice.invoice_no, Invoice.customer_name,
               Invoice.issued_date, Invoice.due_date, Invoice.amount,
               Invoice.currency_code, Invoice.status)
        .where(Invoice.is_deleted == False)  # noqa: E712
        .order_by(Invoice.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Invoice).where(Invoice.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_invoice(session: Session, *, tenant_id: UUID, customer_id: int | None,
                   customer_name: str, amount, due_date: str, due_on=None) -> Invoice:
    invoice = Invoice(
        tenant_id=tenant_id, customer_id=customer_id, customer_name=customer_name,
        amount=amount, due_date=due_date, due_on=due_on, status="Open",
    )
    session.add(invoice)
    session.flush()
    invoice.invoice_no = f"INV-{8800 + invoice.id}"
    session.flush()
    session.refresh(invoice)
    return invoice


# ---------- Returns ----------

def list_returns(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(SalesReturn.public_id, SalesReturn.rma_no, SalesReturn.order_ref,
               SalesReturn.customer_name, SalesReturn.reason, SalesReturn.refund,
               SalesReturn.currency_code, SalesReturn.status)
        .where(SalesReturn.is_deleted == False)  # noqa: E712
        .order_by(SalesReturn.id.desc())
        .limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(SalesReturn).where(SalesReturn.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def create_return(session: Session, *, tenant_id: UUID, order_ref: str,
                  customer_name: str, reason: str, refund) -> SalesReturn:
    ret = SalesReturn(
        tenant_id=tenant_id, order_ref=order_ref, customer_name=customer_name,
        reason=reason, refund=refund, status="Inspecting",
    )
    session.add(ret)
    session.flush()
    ret.rma_no = f"RMA-{400 + ret.id}"
    session.flush()
    session.refresh(ret)
    return ret


# ---------- Detail fetchers ----------

def _customer_by_id(session: Session, customer_id: int | None) -> Customer | None:
    if not customer_id:
        return None
    return session.get(Customer, customer_id)


def get_order_detail(session: Session, *, public_id: str) -> dict | None:
    order = session.execute(
        select(SalesOrder).where(SalesOrder.public_id == public_id,
                                 SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if order is None:
        return None
    lines = [dict(r._mapping) for r in session.execute(
        select(SalesOrderLine.name, SalesOrderLine.sku, SalesOrderLine.qty,
               SalesOrderLine.price, SalesOrderLine.line_total)
        .where(SalesOrderLine.order_id == order.id)
    )]
    return {"order": order, "customer": _customer_by_id(session, order.customer_id), "lines": lines}


def get_invoice_detail(session: Session, *, public_id: str) -> dict | None:
    invoice = session.execute(
        select(Invoice).where(Invoice.public_id == public_id,
                              Invoice.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if invoice is None:
        return None
    lines = [dict(r._mapping) for r in session.execute(
        select(InvoiceLine.name, InvoiceLine.sku, InvoiceLine.qty,
               InvoiceLine.price, InvoiceLine.line_total)
        .where(InvoiceLine.invoice_id == invoice.id)
    )]
    return {"invoice": invoice, "customer": _customer_by_id(session, invoice.customer_id), "lines": lines}


def _set_status(session: Session, model, public_id: str, status: str):
    obj = session.execute(
        select(model).where(model.public_id == public_id, model.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if obj is None:
        return None
    obj.status = status
    session.flush()
    return obj


def set_order_status(session, *, public_id, status):
    return _set_status(session, SalesOrder, public_id, status)


def set_invoice_status(session, *, public_id, status):
    return _set_status(session, Invoice, public_id, status)


def set_return_status(session, *, public_id, status):
    return _set_status(session, SalesReturn, public_id, status)


def get_customer_detail(session: Session, *, public_id: str) -> dict | None:
    customer = session.execute(
        select(Customer).where(Customer.public_id == public_id,
                               Customer.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if customer is None:
        return None
    orders = [dict(r._mapping) for r in session.execute(
        select(SalesOrder.order_no, SalesOrder.item_count, SalesOrder.channel,
               SalesOrder.total, SalesOrder.status)
        .where(SalesOrder.customer_id == customer.id, SalesOrder.is_deleted == False)  # noqa: E712
        .order_by(SalesOrder.id.desc())
    )]
    lifetime = sum((o["total"] or 0) for o in orders)
    open_invoices = session.execute(
        select(func.count()).select_from(Invoice).where(
            Invoice.customer_id == customer.id, Invoice.status == "Open",
            Invoice.is_deleted == False,  # noqa: E712
        )
    ).scalar_one()
    return {"customer": customer, "orders": orders,
            "lifetime": lifetime, "open_invoices": open_invoices}


# ---------- Commercial / retail invoices generated from a sales order ----------

def order_by_no(session: Session, *, order_no: str) -> SalesOrder | None:
    return session.execute(
        select(SalesOrder).where(func.upper(SalesOrder.order_no) == order_no.upper().strip(),
                                 SalesOrder.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()


def order_lines(session: Session, *, order_id: int) -> list[dict]:
    return [dict(r._mapping) for r in session.execute(
        select(SalesOrderLine.name, SalesOrderLine.color, SalesOrderLine.size,
               SalesOrderLine.sku, SalesOrderLine.qty, SalesOrderLine.price)
        .where(SalesOrderLine.order_id == order_id)
        .order_by(SalesOrderLine.id)
    )]


def variants_by_sku(session: Session, *, skus: list[str]) -> dict[str, dict]:
    """Map SKU → variant detail (colour, size, style/product info, per-channel
    prices, image). Used to enrich sales-order lines that only carry a SKU."""
    skus = [s for s in {(s or "").strip() for s in skus} if s]
    if not skus:
        return {}
    color = aliased(AttributeValue)
    size = aliased(AttributeValue)
    rows = session.execute(
        select(
            ProductVariant.sku, ProductVariant.retail_price, ProductVariant.online_price,
            ProductVariant.wholesale_price, ProductVariant.price,
            Product.title, Product.composition, Product.hs_code, Product.image_url,
            color.value.label("color"), size.value.label("size"),
            size.sort_order.label("size_sort"),
        )
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(color, color.id == ProductVariant.color_id)
        .outerjoin(size, size.id == ProductVariant.size_id)
        .where(ProductVariant.sku.in_(skus))
    ).all()
    out: dict[str, dict] = {}
    for r in rows:
        m = r._mapping
        out[m["sku"]] = {
            "color": m["color"], "size": m["size"], "size_sort": m["size_sort"],
            "title": m["title"], "fabric": m["composition"], "hs_code": m["hs_code"],
            "image": m["image_url"],
            "retail_price": m["retail_price"], "online_price": m["online_price"],
            "wholesale_price": m["wholesale_price"], "base_price": m["price"],
        }
    return out


def tenant_name(session: Session) -> str | None:
    return session.execute(text(
        "SELECT name FROM dbo.tenants WHERE id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)"
    )).scalar()


def company_info(session: Session) -> dict:
    """Our own company's profile fields — for the invoice letterhead block."""
    row = session.execute(text(
        "SELECT name, legal_name, tax_registration, registration_number, base_currency_code, "
        "country, city, [state], postal, street, business_email, phone, support_line, website, logo_doc_id "
        "FROM dbo.tenants WHERE id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)"
    )).mappings().first()
    return dict(row) if row else {}


def create_sales_invoice(session: Session, *, tenant_id: UUID, invoice_no, order_no,
                         customer_name, invoice_type, currency_code, total,
                         data: str) -> SalesInvoice:
    inv = SalesInvoice(
        tenant_id=tenant_id, invoice_no=invoice_no, order_no=order_no,
        customer_name=customer_name, invoice_type=invoice_type,
        currency_code=currency_code, total=total, status="Draft", data=data,
        created_at=datetime.datetime.utcnow(),
    )
    session.add(inv)
    session.flush()
    if not inv.invoice_no:
        inv.invoice_no = f"INV-{7700 + inv.id}"
        session.flush()
    return inv


def list_sales_invoices(session: Session, *, limit: int, offset: int) -> tuple[list[dict], int]:
    stmt = (
        select(SalesInvoice.public_id, SalesInvoice.invoice_no, SalesInvoice.order_no,
               SalesInvoice.customer_name, SalesInvoice.invoice_type,
               SalesInvoice.currency_code, SalesInvoice.total, SalesInvoice.status,
               SalesInvoice.created_at)
        .where(SalesInvoice.is_deleted == False)  # noqa: E712
        .order_by(SalesInvoice.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(SalesInvoice).where(SalesInvoice.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def get_sales_invoice(session: Session, *, public_id: str) -> SalesInvoice | None:
    return session.execute(
        select(SalesInvoice).where(SalesInvoice.public_id == public_id,
                                   SalesInvoice.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()

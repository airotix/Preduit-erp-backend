"""Tech-pack attachments reuse tenant-scoped document metadata and file storage."""
import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.catalog import Product, ProductVariant
from app.models.document import Document
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.sales import SalesOrderLine
from app.modules.documents import service as documents
from app.core.config import get_settings

TECH_PACK = "Tech Pack"
DONE = ("Completed", "Received")


def product_by_public(session, public_id, tenant_id=None):
    query = select(Product).where(Product.public_id == UUID(str(public_id)), Product.is_deleted.is_(False))
    if tenant_id is not None:
        query = query.where(Product.tenant_id == UUID(str(tenant_id)))
    product = session.scalar(query)
    if product is None:
        raise HTTPException(404, "Article not found")
    return product


def line_by_public(session, public_id, tenant_id=None):
    query = select(ProductionOrderLine).where(ProductionOrderLine.public_id == UUID(str(public_id)),
                                              ProductionOrderLine.is_deleted.is_(False))
    if tenant_id is not None:
        query = query.where(ProductionOrderLine.tenant_id == UUID(str(tenant_id)))
    line = session.scalar(query)
    if line is None:
        raise HTTPException(404, "Production item not found")
    order = session.get(ProductionOrder, line.order_id)
    if order is None or order.is_deleted or order.tenant_id != line.tenant_id:
        raise HTTPException(404, "Production order not found")
    return line


def product_for_line(session, line):
    variant = None
    if line.sales_order_line_id:
        source = session.get(SalesOrderLine, line.sales_order_line_id)
        if source and source.tenant_id == line.tenant_id and source.variant_id:
            variant = session.scalar(select(ProductVariant).where(ProductVariant.id == source.variant_id,
                ProductVariant.tenant_id == line.tenant_id))
    if variant is None and line.sku:
        variant = session.scalar(select(ProductVariant).where(ProductVariant.sku == line.sku,
            ProductVariant.tenant_id == line.tenant_id))
    if variant:
        return session.scalar(select(Product).where(Product.id == variant.product_id,
            Product.tenant_id == line.tenant_id, Product.is_deleted.is_(False)))
    # Legacy/manual items can link by an exact, unambiguous article title.
    matches = session.scalars(select(Product).where(Product.tenant_id == line.tenant_id,
        Product.title == line.name, Product.is_deleted.is_(False)).limit(2)).all()
    return matches[0] if len(matches) == 1 else None


def _document(session, tenant_id, module, ref):
    return session.scalar(select(Document).where(Document.tenant_id == tenant_id,
        Document.module == module, Document.entity_type == "tech-pack", Document.entity_ref == str(ref),
        Document.is_deleted.is_(False), Document.storage_path != "").order_by(Document.id.desc()).limit(1))


def _payload(document, product=None):
    return {"status": "Received" if document else "Pending",
            "productPublicId": str(product.public_id) if product else None,
            "document": {"public_id": str(document.public_id), "filename": document.filename,
                         "size_bytes": document.size_bytes} if document else None}


def product_pack(session, product):
    return _payload(_document(session, product.tenant_id, "catalog", product.public_id), product)


def line_pack(session, line):
    product = product_for_line(session, line)
    document = _document(session, product.tenant_id, "catalog", product.public_id) if product else None
    document = document or _document(session, line.tenant_id, "production", line.public_id)
    return _payload(document, product)


def sync_order(session, order):
    from app.modules.production import repository as production
    lines = production.list_lines(session, order.id)
    if not lines:
        lines = [production.create_porder_line(session, tenant_id=order.tenant_id,
                    order_id=order.id, name=order.style, qty=order.qty)]
        # Preserve an older order-level timeline when making its item addressable.
        for stage in production.list_stages(session, order.id):
            if stage.line_id is None:
                stage.line_id = lines[0].id
        session.flush()
    for line in lines:
        stages = production.list_line_stages(session, line.id)
        pack_stage = next((stage for stage in stages if stage.name == TECH_PACK), None)
        if stages and pack_stage is None and not all(stage.status in DONE for stage in stages):
            pack_stage = ProductionStage(tenant_id=order.tenant_id, order_id=order.id,
                line_id=line.id, seq=0, name=TECH_PACK, duration_days=0, status="Pending")
            session.add(pack_stage)
        pack = line_pack(session, line)
        if pack_stage:
            pack_stage.seq = 0
            pack_stage.status = pack["status"]
            if pack["status"] == "Received" and pack_stage.end_on is None:
                pack_stage.end_on = datetime.date.today()
        if not stages and pack["status"] != "Received":
            order.stage = TECH_PACK
    session.flush()
    if any(stage.name != TECH_PACK for stage in production.list_stages(session, order.id)):
        production._recompute_order(session, order.id)
    session.flush()


def sync_orders(session, tenant_id=None):
    query = select(ProductionOrder).where(ProductionOrder.is_deleted.is_(False))
    if tenant_id is not None:
        query = query.where(ProductionOrder.tenant_id == UUID(str(tenant_id)))
    for order in session.scalars(query).all():
        sync_order(session, order)


def upload_product(session, *, tenant_id, public_id, filename, content_type, data):
    product = product_by_public(session, public_id, tenant_id)
    documents.validate_upload(filename=filename, content_type=content_type, size_bytes=len(data))
    documents.store_document(session, tenant_id=tenant_id, module="catalog", entity_type="tech-pack",
        entity_ref=str(product.public_id), filename=filename, content_type=content_type, data=data,
        database_storage=not bool(get_settings().s3_bucket))
    sync_orders(session, tenant_id)
    from app.modules.production import service as production
    for line in session.scalars(select(ProductionOrderLine).where(
            ProductionOrderLine.tenant_id == product.tenant_id,
            ProductionOrderLine.is_deleted.is_(False))).all():
        linked = product_for_line(session, line)
        order = session.get(ProductionOrder, line.order_id)
        if linked and linked.id == product.id and order and not order.is_deleted and production._line_is_complete(session, line.id):
            production._open_line_inspection(session, tenant_id=line.tenant_id,
                order_ref=order.order_no, line=line)
    return product_pack(session, product)


def upload_line(session, *, tenant_id, public_id, filename, content_type, data):
    line = line_by_public(session, public_id, tenant_id)
    product = product_for_line(session, line)
    if product:
        result = upload_product(session, tenant_id=tenant_id, public_id=product.public_id,
            filename=filename, content_type=content_type, data=data)
    else:
        documents.validate_upload(filename=filename, content_type=content_type, size_bytes=len(data))
        documents.store_document(session, tenant_id=tenant_id, module="production", entity_type="tech-pack",
            entity_ref=str(line.public_id), filename=filename, content_type=content_type, data=data,
            database_storage=not bool(get_settings().s3_bucket))
        sync_order(session, session.get(ProductionOrder, line.order_id))
        result = line_pack(session, line)
    # If the attachment is the final missing step, retain automatic final QC.
    from app.modules.production import service as production
    if production._line_is_complete(session, line.id):
        production._open_line_inspection(session, tenant_id=line.tenant_id,
            order_ref=session.get(ProductionOrder, line.order_id).order_no, line=line)
    return result

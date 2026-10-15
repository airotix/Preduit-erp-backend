"""Tech-pack attachments reuse tenant-scoped document metadata and file storage."""
import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.catalog import Product, ProductVariant
from app.models.document import Document
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.batches import ProductionBatch, BatchStage
from app.models.sales import SalesOrderLine
from app.models.techpack import TechPackRevision
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
    # Do not auto-attach a production line to a product by name alone. A title match can
    # make a new line inherit a different article's tech-pack and incorrectly mark TNA as received.
    return None


def _revisions(session, *, tenant_id, product_id=None, line_id=None):
    scope = TechPackRevision.product_id == product_id if product_id is not None else TechPackRevision.production_line_id == line_id
    return session.scalars(select(TechPackRevision).where(
        TechPackRevision.tenant_id == tenant_id, scope)
        .order_by(TechPackRevision.revision.desc())).all()


def _pack(session, revisions, *, product=None, owner="catalog"):
    approved = next((r for r in revisions if r.status == "Approved"), None)
    pending = next((r for r in revisions if r.status == "Draft"), None)
    ids = [r.document_id for r in revisions]
    docs = {doc.id: doc for doc in session.scalars(select(Document).where(
        Document.id.in_(ids), Document.is_deleted.is_(False))).all()} if ids else {}
    def item(revision):
        doc = docs.get(revision.document_id)
        return {"id": str(revision.public_id), "revision": revision.revision,
                "status": revision.status, "approvedBy": revision.approved_by,
                "document": {"public_id": str(doc.public_id), "filename": doc.filename,
                             "size_bytes": doc.size_bytes} if doc else None}
    active_doc = docs.get(approved.document_id) if approved else None
    return {"status": "Received" if active_doc else "Pending",
            "productPublicId": str(product.public_id) if product else None,
            "approvalOwner": owner,
            "approvedRevision": approved.revision if active_doc else None,
            "pendingRevision": item(pending) if pending else None,
            "revisions": [item(revision) for revision in revisions],
            "document": {"public_id": str(active_doc.public_id), "filename": active_doc.filename,
                         "size_bytes": active_doc.size_bytes} if active_doc else None}


def product_pack(session, product):
    return _pack(session, _revisions(session, tenant_id=product.tenant_id,
        product_id=product.id), product=product)


def line_pack(session, line):
    product = product_for_line(session, line)
    direct = _revisions(session, tenant_id=line.tenant_id, line_id=line.id)
    if any(r.status == "Approved" for r in direct) or product is None:
        return _pack(session, direct, product=product, owner="production")
    return product_pack(session, product)


def _new_revision(session, *, tenant_id, document_public_id, product=None, line=None):
    owner = product or line
    session.scalar(select(type(owner)).where(type(owner).id == owner.id).with_for_update())
    prior = _revisions(session, tenant_id=tenant_id,
        product_id=product.id if product else None, line_id=line.id if line else None)
    for old in prior:
        if old.status == "Draft":
            old.status = "Superseded"
    doc = session.scalar(select(Document).where(Document.public_id == document_public_id,
        Document.tenant_id == tenant_id))
    revision = TechPackRevision(tenant_id=tenant_id,
        product_id=product.id if product else None,
        production_line_id=line.id if line else None,
        document_id=doc.id, revision=(prior[0].revision + 1 if prior else 1), status="Draft")
    session.add(revision); session.flush()
    return revision


def sync_order(session, order):
    if session.scalar(select(ProductionBatch.id).where(ProductionBatch.order_id == order.id,
            ProductionBatch.tenant_id == order.tenant_id).limit(1)):
        for stage in session.scalars(select(BatchStage).join(ProductionBatch,
                ProductionBatch.id == BatchStage.batch_id).where(
                ProductionBatch.order_id == order.id, BatchStage.tenant_id == order.tenant_id,
                BatchStage.name == TECH_PACK)).all():
            line = session.get(ProductionOrderLine, stage.production_line_id) if stage.production_line_id else None
            if line and stage.status in ("Pending", "Received"):
                received = line_pack(session, line)["status"] == "Received"
                stage.status = "Received" if received else "Pending"
                if received and stage.actual_end_on is None:
                    stage.actual_end_on = datetime.date.today()
        session.flush()
        return
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
    stored = documents.store_document(session, tenant_id=tenant_id, module="catalog", entity_type="tech-pack",
        entity_ref=str(product.public_id), filename=filename, content_type=content_type, data=data,
        database_storage=not bool(get_settings().s3_bucket))
    _new_revision(session, tenant_id=product.tenant_id,
        document_public_id=UUID(stored["public_id"]), product=product)
    return product_pack(session, product)


def upload_line(session, *, tenant_id, public_id, filename, content_type, data):
    line = line_by_public(session, public_id, tenant_id)
    product = product_for_line(session, line)
    if product:
        result = upload_product(session, tenant_id=tenant_id, public_id=product.public_id,
            filename=filename, content_type=content_type, data=data)
    else:
        documents.validate_upload(filename=filename, content_type=content_type, size_bytes=len(data))
        stored = documents.store_document(session, tenant_id=tenant_id, module="production", entity_type="tech-pack",
            entity_ref=str(line.public_id), filename=filename, content_type=content_type, data=data,
            database_storage=not bool(get_settings().s3_bucket))
        _new_revision(session, tenant_id=line.tenant_id,
            document_public_id=UUID(stored["public_id"]), line=line)
        result = line_pack(session, line)
    return result


def _after_approval(session, *, product=None, line=None):
    if product:
        sync_orders(session, product.tenant_id)
        candidates = session.scalars(select(ProductionOrderLine).where(
            ProductionOrderLine.tenant_id == product.tenant_id,
            ProductionOrderLine.is_deleted.is_(False))).all()
    else:
        sync_order(session, session.get(ProductionOrder, line.order_id))
        candidates = [line]
    from app.modules.production import service as production
    for candidate in candidates:
        if product:
            linked = product_for_line(session, candidate)
            if linked is None or linked.id != product.id:
                continue
        order = session.get(ProductionOrder, candidate.order_id)
        if order and not order.is_deleted and not session.scalar(select(ProductionBatch.id).where(
                ProductionBatch.order_id == order.id, ProductionBatch.tenant_id == order.tenant_id).limit(1)) \
                and production._line_is_complete(session, candidate.id):
            production._open_line_inspection(session, tenant_id=candidate.tenant_id,
                order_ref=order.order_no, line=candidate)


def _approve(session, *, tenant_id, revision_id, product=None, line=None, actor=None):
    owner = product or line
    session.scalar(select(type(owner)).where(type(owner).id == owner.id).with_for_update())
    scope = TechPackRevision.product_id == product.id if product else TechPackRevision.production_line_id == line.id
    revision = session.scalar(select(TechPackRevision).where(
        TechPackRevision.public_id == UUID(str(revision_id)),
        TechPackRevision.tenant_id == tenant_id, scope).with_for_update())
    if revision is None:
        raise HTTPException(404, "Tech Pack revision not found")
    if revision.status != "Draft":
        raise HTTPException(409, "Only a draft Tech Pack revision can be approved.")
    newer = session.scalar(select(TechPackRevision.id).where(
        TechPackRevision.tenant_id == tenant_id, scope,
        TechPackRevision.revision > revision.revision,
        TechPackRevision.status == "Draft").limit(1))
    if newer is not None:
        raise HTTPException(409, "A newer draft exists. Approve the latest revision.")
    doc = session.scalar(select(Document).where(Document.id == revision.document_id,
        Document.tenant_id == tenant_id, Document.is_deleted.is_(False), Document.storage_path != ""))
    if doc is None:
        raise HTTPException(409, "The Tech Pack file is unavailable.")
    for old in session.scalars(select(TechPackRevision).where(
            TechPackRevision.tenant_id == tenant_id, scope,
            TechPackRevision.status == "Approved").with_for_update()).all():
        old.status = "Superseded"
    session.flush()
    revision.status = "Approved"
    revision.approved_at = datetime.datetime.now(datetime.timezone.utc)
    revision.approved_by = actor
    session.flush()
    _after_approval(session, product=product, line=line)
    return product_pack(session, product) if product else line_pack(session, line)


def approve_product_revision(session, *, tenant_id, public_id, revision_id, actor=None):
    product = product_by_public(session, public_id, tenant_id)
    return _approve(session, tenant_id=product.tenant_id, revision_id=revision_id,
        product=product, actor=actor)


def approve_line_revision(session, *, tenant_id, public_id, revision_id, actor=None):
    line = line_by_public(session, public_id, tenant_id)
    if product_for_line(session, line):
        raise HTTPException(409, "Approve this article's Tech Pack in Catalog.")
    return _approve(session, tenant_id=line.tenant_id, revision_id=revision_id,
        line=line, actor=actor)

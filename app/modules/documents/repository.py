"""Document metadata data access (tenant-scoped by RLS)."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document

# module → short prefix for the human-readable doc id
MODULE_PREFIX = {
    "catalog": "CAT", "inventory": "INV", "sales": "SAL", "procurement": "PRC",
    "finance": "FIN", "production": "PRD", "quality": "QLT", "shipments": "SHP",
    "commerce": "CHN", "ai": "AII", "admin": "ADM",
}


def create_document(session: Session, *, tenant_id: UUID, module: str, entity_type: str | None,
                    entity_ref: str | None, filename: str, content_type: str | None,
                    size_bytes: int, storage_path: str) -> Document:
    doc = Document(
        tenant_id=tenant_id, doc_id="PENDING", module=module, entity_type=entity_type,
        entity_ref=entity_ref, filename=filename, content_type=content_type,
        size_bytes=size_bytes, storage_path=storage_path,
    )
    session.add(doc)
    session.flush()  # id available
    prefix = MODULE_PREFIX.get(module, "DOC")
    doc.doc_id = f"{prefix}-{doc.id:06d}"
    session.flush()
    session.refresh(doc)
    return doc


def list_documents(session: Session, *, module: str | None, entity_ref: str | None) -> list[dict]:
    stmt = select(Document).where(Document.is_deleted == False)  # noqa: E712
    if module:
        stmt = stmt.where(Document.module == module)
    if entity_ref:
        stmt = stmt.where(Document.entity_ref == entity_ref)
    stmt = stmt.order_by(Document.id.desc())
    return [
        {"public_id": str(d.public_id), "doc_id": d.doc_id, "filename": d.filename,
         "content_type": d.content_type, "size_bytes": d.size_bytes,
         "created_at": d.created_at.isoformat() if d.created_at else None}
        for d in session.execute(stmt).scalars()
    ]


def get_document(session: Session, *, public_id: str) -> Document | None:
    return session.execute(
        select(Document).where(Document.public_id == public_id,
                               Document.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()

"""Document service — stores files and records metadata."""
import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.core import storage
from app.core.audit import write_audit
from app.modules.documents import repository as repo


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def store_document(session: Session, *, tenant_id: str | UUID, module: str,
                   entity_type: str | None, entity_ref: str | None,
                   filename: str, content_type: str | None, data: bytes) -> dict:
    tid = _tid(tenant_id)
    # Create the row first to get a doc_id, then persist the file under it.
    doc = repo.create_document(
        session, tenant_id=tid, module=module, entity_type=entity_type,
        entity_ref=entity_ref, filename=filename, content_type=content_type,
        size_bytes=len(data), storage_path="",
    )
    doc.storage_path = storage.save_file(
        tenant_id=tid, doc_id=doc.doc_id, filename=filename, data=data,
    )
    session.flush()
    write_audit(session, tenant_id=tid, action="UPLOAD", entity_type="document",
                entity_id=doc.doc_id, detail=f"Uploaded {filename} ({doc.doc_id})")
    return {
        "public_id": str(doc.public_id), "doc_id": doc.doc_id,
        "filename": doc.filename, "size_bytes": doc.size_bytes,
    }


def list_documents(session: Session, *, module: str | None, entity_ref: str | None) -> list[dict]:
    return repo.list_documents(session, module=module, entity_ref=entity_ref)

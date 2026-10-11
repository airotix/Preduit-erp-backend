"""Document service — stores files and records metadata."""
import os
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core import storage
from app.core.audit import write_audit
from app.core.config import get_settings
from app.modules.documents import repository as repo

settings = get_settings()


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def validate_upload(*, filename: str, content_type: str | None, size_bytes: int) -> None:
    """Reject oversized files or types outside the allowlist. Raises 413/415."""
    if size_bytes <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file.")
    if size_bytes > settings.upload_max_bytes:
        mb = settings.upload_max_bytes // (1024 * 1024)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"File exceeds the {mb} MB upload limit.")
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    if ext not in settings.upload_ext_set:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"File type “.{ext or '?'}” is not allowed.")
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime and mime not in settings.upload_mime_set:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"Content type “{mime}” is not allowed.")


def validate_tech_pack(*, filename, content_type, data):
    validate_upload(filename=filename, content_type=content_type, size_bytes=len(data))
    mime = (content_type or "").split(";")[0].strip().lower()
    if os.path.splitext(filename)[1].lower() != ".pdf" or mime not in ("", "application/pdf", "application/octet-stream"):
        raise HTTPException(415, "Tech packs must be PDF files.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "The uploaded tech pack does not contain a PDF header.")


def store_document(session: Session, *, tenant_id: str | UUID, module: str,
                   entity_type: str | None, entity_ref: str | None,
                   filename: str, content_type: str | None, data: bytes,
                   database_storage: bool = False) -> dict:
    tid = _tid(tenant_id)
    if entity_type == "tech-pack":
        validate_tech_pack(filename=filename, content_type=content_type, data=data)
        content_type = "application/pdf"
    # Create the row first to get a doc_id, then persist the file under it.
    doc = repo.create_document(
        session, tenant_id=tid, module=module, entity_type=entity_type,
        entity_ref=entity_ref, filename=filename, content_type=content_type,
        size_bytes=len(data), storage_path="",
    )
    if database_storage:
        from app.models.document import DocumentContent
        session.add(DocumentContent(document_id=doc.id, tenant_id=tid, data=data))
        doc.storage_path = f"db://{doc.public_id}"
    else:
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

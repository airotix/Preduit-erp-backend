"""Reusable document upload/list/download routes — usable by any module."""
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core import storage
from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.documents import repository as repo
from app.modules.documents import service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
def list_documents(module: str | None = Query(None), entity_ref: str | None = Query(None),
                   db: Session = Depends(tenant_db)):
    return service.list_documents(db, module=module, entity_ref=entity_ref)


@router.post("/upload", status_code=status.HTTP_201_CREATED)
def upload(
    module: str = Form(...),
    entity_type: str | None = Form(None),
    entity_ref: str | None = Form(None),
    file: UploadFile = File(...),
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    data = file.file.read()
    return service.store_document(
        db, tenant_id=principal.tenant_id, module=module, entity_type=entity_type,
        entity_ref=entity_ref, filename=file.filename or "file",
        content_type=file.content_type, data=data,
    )


@router.get("/{public_id}/download")
def download(public_id: str, db: Session = Depends(tenant_db)):
    doc = repo.get_document(db, public_id=public_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    data = storage.read_file(doc.storage_path)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )

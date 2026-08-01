"""File storage helper.

Local filesystem for dev; the same interface maps to Azure Blob Storage in
production (see docs/BACKEND_ARCHITECTURE_PLAN.md §8) — swap the two functions
for BlobServiceClient calls without touching callers.
"""
import pathlib
import re

from app.core.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _base() -> pathlib.Path:
    return pathlib.Path(get_settings().doc_storage_dir)


def save_file(*, tenant_id, doc_id: str, filename: str, data: bytes) -> str:
    safe = _SAFE.sub("_", filename) or "file"
    folder = _base() / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}__{safe}"
    path.write_bytes(data)
    return str(path)


def read_file(storage_path: str) -> bytes:
    return pathlib.Path(storage_path).read_bytes()

"""File storage helper.

Local filesystem for dev; S3 in production.
Set S3_BUCKET to enable S3 mode — when blank, files are stored locally.
"""
import io
import pathlib
import re

from app.core.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _s3_client():
    import boto3
    settings = get_settings()
    kwargs = {}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_region:
        kwargs["region_name"] = settings.s3_region
    return boto3.client("s3", **kwargs)


def _s3_key(tenant_id, doc_id: str, filename: str) -> str:
    safe = _SAFE.sub("_", filename) or "file"
    return f"{tenant_id}/{doc_id}__{safe}"


def _base() -> pathlib.Path:
    return pathlib.Path(get_settings().doc_storage_dir)


def save_file(*, tenant_id, doc_id: str, filename: str, data: bytes) -> str:
    settings = get_settings()
    if settings.s3_bucket:
        key = _s3_key(tenant_id, doc_id, filename)
        _s3_client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
        )
        return f"s3://{settings.s3_bucket}/{key}"

    safe = _SAFE.sub("_", filename) or "file"
    folder = _base() / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}__{safe}"
    path.write_bytes(data)
    return str(path)


def read_file(storage_path: str) -> bytes:
    if storage_path.startswith("s3://"):
        parts = storage_path[5:].split("/", 1)
        bucket, key = parts[0], parts[1]
        resp = _s3_client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    return pathlib.Path(storage_path).read_bytes()

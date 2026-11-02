"""Private generated-document storage with a safe local fallback."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_BUCKET = "timesheet-documents"
PDF_MIMETYPE = "application/pdf"
XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CONTENT_TYPES = {".pdf": PDF_MIMETYPE, ".xlsx": XLSX_MIMETYPE}
_client: Any | None = None


class DocumentStorageError(RuntimeError):
    """Raised when a generated document cannot be persisted remotely."""


def is_configured() -> bool:
    """Return whether the production Supabase credentials are present."""
    return bool(
        os.environ.get("SUPABASE_URL")
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )


def _storage_key(storage_key: str) -> str:
    if not isinstance(storage_key, str) or not storage_key or "\\" in storage_key:
        raise ValueError("Storage key must be a relative POSIX path")
    path = PurePosixPath(storage_key)
    if (
        path.is_absolute()
        or any(part in (".", "..") for part in path.parts)
        or path.as_posix() != storage_key
    ):
        raise ValueError("Storage key must be a relative POSIX path")
    return storage_key


def _validate_content_type(storage_key: str, content_type: str) -> None:
    expected = _CONTENT_TYPES.get(PurePosixPath(storage_key).suffix.lower())
    if expected is None or content_type != expected:
        raise ValueError("Unsupported document content type")


def _bucket():
    global _client
    if not is_configured():
        return None
    if _client is None:
        try:
            from supabase import create_client
        except ImportError as exc:
            raise DocumentStorageError(
                "Document storage is unavailable because Supabase is not installed"
            ) from exc
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    return _client.storage.from_(os.environ.get("SUPABASE_STORAGE_BUCKET", DEFAULT_BUCKET))


def upload(local_path: Path, storage_key: str, content_type: str) -> None:
    """Store a document remotely, or leave local development files untouched."""
    storage_key = _storage_key(storage_key)
    _validate_content_type(storage_key, content_type)
    bucket = _bucket()
    if bucket is None:
        return
    try:
        with Path(local_path).open("rb") as document:
            bucket.upload(
                path=storage_key,
                file=document,
                file_options={"content-type": content_type, "upsert": "true"},
            )
    except Exception as exc:
        raise DocumentStorageError("Unable to store generated document") from exc


def check_ready():
    """Verify that the configured document bucket exists and stays private."""
    _bucket()  # Initialize the same SDK client used by document writes.
    if _client is None:
        raise DocumentStorageError('Persistent document storage is not configured')
    bucket = _client.storage.get_bucket(os.environ.get('SUPABASE_STORAGE_BUCKET', DEFAULT_BUCKET))
    public = bucket.get('public') if isinstance(bucket, dict) else bucket.public
    if public:
        raise DocumentStorageError('The document bucket must be private')


def download(storage_key: str) -> bytes | None:
    """Return None only for a missing object; surface outages as retryable."""
    try:
        storage_key = _storage_key(storage_key)
    except ValueError:
        return None
    try:
        bucket = _bucket()
        if bucket is None:
            raise DocumentStorageError('Persistent document storage is not configured')
        return bytes(bucket.download(storage_key))
    except Exception as exc:
        if getattr(exc, 'status', None) in (404, '404'):
            return None
        raise DocumentStorageError('Document storage is temporarily unavailable') from exc


def delete(storage_key: str) -> None:
    """Best-effort removal of a private document."""
    try:
        storage_key = _storage_key(storage_key)
        bucket = _bucket()
        if bucket is None:
            return
        bucket.remove([storage_key])
    except Exception:
        return

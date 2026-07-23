"""Pluggable object storage.

- ``StorageBackend``      : abstract interface.
- ``LocalStorageBackend`` : filesystem backend for dev/tests; entries expire
  after 24h and are swept lazily.
- ``R2StorageBackend``    : Cloudflare R2 (S3-compatible). Never instantiated —
  and therefore never contacted — unless full configuration is present.

R2 *lifecycle* activation (bucket lifecycle rules) is intentionally deferred to
a later task; this backend only performs object puts/gets/deletes.
"""

from __future__ import annotations

import abc
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Settings
from .logging_config import get_logger

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

log = get_logger(__name__)


class StorageBackend(abc.ABC):
    """Minimal object-storage contract used by the renderer."""

    @abc.abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        """Store bytes under ``key`` and return the storage key."""

    @abc.abstractmethod
    async def get(self, key: str) -> bytes:
        """Return stored bytes, raising ``FileNotFoundError`` if absent/expired."""

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Delete an object (no error if already gone)."""

    @abc.abstractmethod
    async def purge_expired(self) -> int:
        """Remove expired objects; return the count removed."""


class LocalStorageBackend(StorageBackend):
    """Filesystem backend. Files older than ``ttl_seconds`` are treated as gone."""

    def __init__(self, base_dir: str, ttl_seconds: int = 24 * 60 * 60) -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        # Keys are opaque; keep them flat and safe.
        safe = key.replace("/", "_")
        return self.base / safe

    def _expired(self, path: Path) -> bool:
        return (time.time() - path.stat().st_mtime) > self.ttl_seconds

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        path = self._path(key)
        path.write_bytes(data)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        if self._expired(path):
            path.unlink(missing_ok=True)
            raise FileNotFoundError(f"{key} (expired)")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def purge_expired(self) -> int:
        removed = 0
        for path in self.base.iterdir():
            if path.is_file() and self._expired(path):
                path.unlink(missing_ok=True)
                removed += 1
        return removed


class R2StorageBackend(StorageBackend):
    """Cloudflare R2 via boto3. Requires full configuration to construct."""

    def __init__(self, settings: Settings) -> None:
        if not settings.r2_configured:
            raise RuntimeError("R2StorageBackend requires full R2 configuration")
        import boto3

        self.bucket: str = settings.r2_bucket  # type: ignore[assignment]
        # Client construction is lazy w.r.t. network: no call is made here.
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    async def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    async def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    async def purge_expired(self) -> int:
        # Object expiry on R2 is handled by bucket lifecycle rules, whose
        # activation is deferred to a later task.
        return 0


def build_storage_backend(settings: Settings) -> StorageBackend:
    """Select a backend. R2 is only touched when fully configured."""
    if settings.r2_configured:
        log.info("storage.backend", backend="r2")
        return R2StorageBackend(settings)
    log.info("storage.backend", backend="local", dir=settings.storage_dir)
    return LocalStorageBackend(settings.storage_dir, ttl_seconds=settings.output_ttl_seconds)

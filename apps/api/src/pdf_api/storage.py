"""Pluggable local and Cloudflare R2 object storage backends."""

from __future__ import annotations

import abc
import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Settings
from .logging_config import get_logger

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

log = get_logger(__name__)


class StorageBackend(abc.ABC):
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
    """Filesystem backend. Blocking file operations run in worker threads."""

    def __init__(self, base_dir: str, ttl_seconds: int = 24 * 60 * 60) -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        return self.base / key.replace("/", "_")

    def _expired(self, path: Path) -> bool:
        return (time.time() - path.stat().st_mtime) > self.ttl_seconds

    def _get_sync(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        if self._expired(path):
            path.unlink(missing_ok=True)
            raise FileNotFoundError(f"{key} (expired)")
        return path.read_bytes()

    def _purge_sync(self) -> int:
        removed = 0
        for path in self.base.iterdir():
            if path.is_file() and self._expired(path):
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        await asyncio.to_thread(self._path(key).write_bytes, data)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_sync, key)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def purge_expired(self) -> int:
        return await asyncio.to_thread(self._purge_sync)


class R2StorageBackend(StorageBackend):
    """Cloudflare R2 via boto3, with blocking SDK calls off the event loop."""

    def __init__(self, settings: Settings) -> None:
        if not settings.r2_configured:
            raise RuntimeError("R2StorageBackend requires full R2 configuration")
        import boto3

        self.bucket: str = settings.r2_bucket  # type: ignore[assignment]
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=key)
        body = obj["Body"]
        try:
            return await asyncio.to_thread(body.read)
        finally:
            await asyncio.to_thread(body.close)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

    async def purge_expired(self) -> int:
        return 0


def build_storage_backend(settings: Settings) -> StorageBackend:
    if settings.r2_configured:
        log.info("storage.backend", backend="r2")
        return R2StorageBackend(settings)
    log.info("storage.backend", backend="local", dir=settings.storage_dir)
    return LocalStorageBackend(settings.storage_dir, ttl_seconds=settings.output_ttl_seconds)

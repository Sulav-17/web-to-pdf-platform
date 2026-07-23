from __future__ import annotations

import os
import tempfile
import time

import pytest

from pdf_api.config import Settings
from pdf_api.storage import (
    LocalStorageBackend,
    R2StorageBackend,
    build_storage_backend,
)


async def test_local_storage_roundtrip() -> None:
    backend = LocalStorageBackend(tempfile.mkdtemp())
    await backend.put("jobs/x.pdf", b"%PDF-1.7 data")
    assert await backend.get("jobs/x.pdf") == b"%PDF-1.7 data"
    await backend.delete("jobs/x.pdf")
    with pytest.raises(FileNotFoundError):
        await backend.get("jobs/x.pdf")


async def test_local_storage_expiry_24h() -> None:
    backend = LocalStorageBackend(tempfile.mkdtemp(), ttl_seconds=24 * 60 * 60)
    path = backend._path("jobs/old.pdf")
    await backend.put("jobs/old.pdf", b"stale")
    # Backdate the file beyond the 24h TTL.
    old = time.time() - (25 * 60 * 60)
    os.utime(path, (old, old))
    with pytest.raises(FileNotFoundError):
        await backend.get("jobs/old.pdf")
    assert await backend.purge_expired() == 0  # already removed on access


def test_build_backend_uses_local_without_r2_config() -> None:
    settings = Settings(
        r2_account_id=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_endpoint_url=None,
    )
    assert settings.r2_configured is False
    backend = build_storage_backend(settings)
    assert isinstance(backend, LocalStorageBackend)


def test_r2_not_contacted_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    # If any boto3 client were constructed, this would raise.
    import boto3

    def _fail(*_a: object, **_k: object) -> object:
        raise AssertionError("boto3 must not be called when R2 is unconfigured")

    monkeypatch.setattr(boto3, "client", _fail)

    settings = Settings()  # no R2 env in tests
    backend = build_storage_backend(settings)
    assert isinstance(backend, LocalStorageBackend)

    # Constructing R2 directly without config must fail before any network call.
    with pytest.raises(RuntimeError):
        R2StorageBackend(settings)

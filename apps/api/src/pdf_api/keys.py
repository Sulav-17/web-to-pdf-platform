"""API key generation and hashing.

Only SHA-256 hashes are ever persisted. Raw keys are shown to the user exactly
once, at creation time.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "sk_live_"


def generate_api_key() -> str:
    """Return a fresh, cryptographically secure raw API key."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw_key: str) -> str:
    """Return the hex SHA-256 digest used for storage and lookup."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_key(raw_key: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented key against a stored hash."""
    return secrets.compare_digest(hash_key(raw_key), stored_hash)

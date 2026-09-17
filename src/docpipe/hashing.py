"""Content hashing helpers.

Every identifier in docpipe is derived from bytes, never from wall-clock time
or insertion order. SHA-256 is used throughout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 65536


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 digest of UTF-8 encoded text."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file's contents, streamed in fixed blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()

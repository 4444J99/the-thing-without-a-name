"""Dependency-free identities for rebuildable corpus cache artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path


def source_digest(path: Path) -> bytes:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def room_cache_key(items) -> str:
    """Bind the decoded stack to every original and matte byte."""
    h = hashlib.sha256()
    for frame_id, raw, mask, _ in items:
        h.update(frame_id.encode())
        h.update(source_digest(raw))
        h.update(source_digest(mask))
    return h.hexdigest()[:20]

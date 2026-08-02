"""Dependency-free identities for rebuildable corpus cache artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def frame_inventory(work: Path):
    """Return complete raw/mask/pose tuples plus raw frames missing derivatives."""
    complete = []
    missing = []
    for raw in sorted((work / "raw").iterdir()):
        if raw.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        mask = work / "vision" / "mask" / f"{raw.stem}.png"
        pose = work / "vision" / "pose" / f"{raw.stem}.json"
        absent = [path for path in (mask, pose) if not path.is_file()]
        if absent:
            missing.append((raw, absent))
        else:
            complete.append((raw.stem, raw, mask, pose))
    return complete, missing


def missing_measurement_inputs(images, room_frame: Path | None = None) -> list[Path]:
    requested = [*images, *([room_frame] if room_frame is not None else [])]
    return [path for path in requested if not path.is_file()]


def block_shape_error(width: int, height: int, block: int) -> str | None:
    if block <= 0:
        return "block size must be positive"
    if width % block or height % block:
        return f"block size {block} must evenly divide {width}×{height}"
    return None


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

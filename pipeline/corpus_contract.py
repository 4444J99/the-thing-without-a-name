"""Dependency-free identities for rebuildable corpus cache artifacts."""

from __future__ import annotations

import hashlib
import json
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


def source_set_receipt(paths) -> list[dict[str, str]]:
    return [{"name": path.name, "sha256": source_digest(path).hex()} for path in paths]


def corpus_source_identity(items) -> str:
    """One content identity for every raw/matte input to encoded tiers."""
    h = hashlib.sha256()
    for frame_id, raw, mask, _ in items:
        h.update(frame_id.encode())
        h.update(source_digest(raw))
        h.update(source_digest(mask))
    return h.hexdigest()


def tier_source_identity(source_sha256: str, spec: dict, matte_quality: int) -> str:
    """Bind a tier to the shared source set and its encoder parameters."""
    payload = {"source_sha256": source_sha256, "spec": spec, "matte_quality": matte_quality}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def tier_output_identity(root: Path, tier: str, frame_ids) -> str | None:
    """Bind an exact tier inventory to every encoded output byte.

    Returning ``None`` is deliberate: a missing, duplicate, extra, non-file, or
    unreadable artifact makes the tier ineligible for retention. The receipt is
    compact, but its identity covers the relative path and SHA-256 of every
    expected plate and matte.
    """
    ids = list(frame_ids)
    expected = [
        Path(kind) / tier / f"{frame_id}.webp"
        for kind in ("plates", "mattes")
        for frame_id in ids
    ]
    expected_names = [path.as_posix() for path in expected]
    if not tier or Path(tier).name != tier or len(set(expected_names)) != len(expected_names):
        return None

    actual_names = []
    for kind in ("plates", "mattes"):
        directory = root / kind / tier
        if not directory.is_dir() or directory.is_symlink():
            return None
        try:
            entries = list(directory.iterdir())
        except OSError:
            return None
        for path in entries:
            if not path.is_file() or path.is_symlink():
                return None
            actual_names.append(path.relative_to(root).as_posix())

    if set(actual_names) != set(expected_names) or len(actual_names) != len(expected_names):
        return None

    records = []
    try:
        for relative in sorted(expected, key=lambda path: path.as_posix()):
            records.append(
                {
                    "path": relative.as_posix(),
                    "sha256": source_digest(root / relative).hex(),
                }
            )
    except OSError:
        return None
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def room_cache_key(items) -> str:
    """Bind the decoded stack to every original and matte byte."""
    h = hashlib.sha256()
    for frame_id, raw, mask, _ in items:
        h.update(frame_id.encode())
        h.update(source_digest(raw))
        h.update(source_digest(mask))
    return h.hexdigest()[:20]

"""Dependency-free identities for rebuildable corpus cache artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# One dependency-free owner for the tier contract. The encoder, renderer, and
# delivery preflight all need these exact parameters to agree on provenance.
TIER_SPECS = {
    "browse": {"width": 512, "quality": 80, "eager": True, "ship": True},
    "screen": {"width": 1024, "quality": 82, "eager": False, "ship": True},
    # Local-only full-camera plates for the 4K master; never shipped in git.
    "film": {"width": 3264, "quality": 92, "eager": False, "ship": False},
}
MATTE_QUALITY = 70  # soft-edged masks tolerate harder compression than plates


def frame_inventory(work: Path):
    """Return complete raw/mask/pose tuples plus raw frames missing derivatives."""
    complete = []
    missing = []
    generation_marker = work / "vision" / ".incomplete"
    generation_incomplete = generation_marker.exists() or generation_marker.is_symlink()
    for raw in sorted((work / "raw").iterdir()):
        if raw.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        mask = work / "vision" / "mask" / f"{raw.stem}.png"
        pose = work / "vision" / "pose" / f"{raw.stem}.json"
        absent = [path for path in (mask, pose) if not path.is_file()]
        if generation_incomplete:
            absent.append(generation_marker)
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
    if not root.is_dir() or root.is_symlink():
        return None

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
        kind_directory = root / kind
        directory = kind_directory / tier
        if kind_directory.is_symlink() or not directory.is_dir() or directory.is_symlink():
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


def _sha256_hex(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(char in "0123456789abcdef" for char in value)
    )


def tier_receipt_is_current(
    root: Path,
    tier: str,
    frame_ids,
    expected_source_sha256: str | None = None,
) -> bool:
    """Require a regular v2 receipt that binds the exact tier output bytes."""
    receipt_directory = root / "tier-receipts"
    receipt = receipt_directory / f"{tier}.json"
    if (
        not receipt_directory.is_dir()
        or receipt_directory.is_symlink()
        or not receipt.is_file()
        or receipt.is_symlink()
    ):
        return False
    try:
        data = json.loads(receipt.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not (
        isinstance(data, dict)
        and data.get("schema") == "danse.corpus.tier-receipt.v2"
        and data.get("tier") == tier
        and _sha256_hex(data.get("source_sha256"))
        and _sha256_hex(data.get("output_sha256"))
    ):
        return False
    if expected_source_sha256 is not None and data["source_sha256"] != expected_source_sha256:
        return False
    output_sha256 = tier_output_identity(root, tier, frame_ids)
    return output_sha256 is not None and data["output_sha256"] == output_sha256


def tier_manifest_entry(tier: str, nbytes: int) -> dict:
    spec = TIER_SPECS[tier]
    return {
        "width": spec["width"],
        "height": round(spec["width"] * 3 / 4),
        "eager": spec["eager"],
        "local": not spec["ship"],
        "plates": f"plates/{tier}/<id>.webp",
        "mattes": f"mattes/{tier}/<id>.webp",
        "bytes": nbytes,
    }


def authorize_render_tier(corpus: Path, work: Path, tier: str) -> tuple[bool, str]:
    """Validate one declared tier at the pixel-consumption boundary.

    Shipped receipts are tracked alongside their derivative bytes and can be
    checked without private inputs. A local tier additionally has to match the
    currently hydrated raw+matte source set, so an ignored receipt cannot simply
    be rewritten to bless altered film plates.
    """
    spec = TIER_SPECS.get(tier)
    if spec is None:
        return False, f"unknown corpus tier {tier}"

    public_path = corpus / "manifest.json"
    if not public_path.is_file() or public_path.is_symlink():
        return False, "public corpus manifest is missing or not a regular file"
    try:
        public = json.loads(public_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, "public corpus manifest is unreadable"
    public_frames = public.get("frames") if isinstance(public, dict) else None
    public_tiers = public.get("tiers") if isinstance(public, dict) else None
    if not (
        isinstance(public, dict)
        and public.get("schema") == "danse.corpus.v1"
        and isinstance(public_frames, list)
        and all(isinstance(frame, dict) and isinstance(frame.get("id"), str) for frame in public_frames)
        and isinstance(public_tiers, dict)
    ):
        return False, "public corpus manifest does not satisfy danse.corpus.v1"
    frame_ids = [frame["id"] for frame in public_frames]
    if not frame_ids or len(set(frame_ids)) != len(frame_ids):
        return False, "public corpus frame inventory is empty or duplicated"

    local_path = corpus / "manifest.local.json"
    local_tiers = {}
    if local_path.exists() or local_path.is_symlink():
        if not local_path.is_file() or local_path.is_symlink():
            return False, "local corpus manifest is not a regular file"
        try:
            local = json.loads(local_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False, "local corpus manifest is unreadable"
        if not (
            isinstance(local, dict)
            and local.get("schema") == "danse.corpus.local.v1"
            and isinstance(local.get("tiers"), dict)
        ):
            return False, "local corpus manifest does not satisfy danse.corpus.local.v1"
        local_tiers = local["tiers"]

    if tier in local_tiers and tier in public_tiers:
        return False, f"local corpus manifest overrides shipped tier {tier}"
    local_selected = tier in local_tiers
    declared = local_tiers.get(tier) if local_selected else public_tiers.get(tier)
    if not isinstance(declared, dict):
        return False, f"corpus tier {tier} is not declared"
    if local_selected == bool(spec["ship"]):
        return False, f"corpus tier {tier} is declared in the wrong manifest"

    total_bytes = 0
    try:
        for kind in ("plates", "mattes"):
            total_bytes += sum(path.stat().st_size for path in (corpus / kind / tier).glob("*.webp"))
    except OSError:
        return False, f"corpus tier {tier} bytes are unreadable"
    if declared != tier_manifest_entry(tier, total_bytes):
        return False, f"corpus tier {tier} manifest entry does not match its canonical contract"

    expected_source = None
    try:
        items, incomplete = frame_inventory(work)
    except OSError:
        items, incomplete = [], []
    hydrated_ids = [item[0] for item in items]
    if not incomplete and hydrated_ids == frame_ids:
        try:
            expected_source = tier_source_identity(corpus_source_identity(items), spec, MATTE_QUALITY)
        except OSError:
            return False, f"corpus tier {tier} source bytes are unreadable"
    elif local_selected:
        return False, f"local corpus tier {tier} lacks its complete hydrated source set"

    if not tier_receipt_is_current(corpus, tier, frame_ids, expected_source):
        return False, f"corpus tier {tier} receipt does not match source and output bytes"
    return True, f"{len(frame_ids)} exact plate+matte pairs"


def room_cache_key(items) -> str:
    """Bind the decoded stack to every original and matte byte."""
    h = hashlib.sha256()
    for frame_id, raw, mask, _ in items:
        h.update(frame_id.encode())
        h.update(source_digest(raw))
        h.update(source_digest(mask))
    return h.hexdigest()[:20]

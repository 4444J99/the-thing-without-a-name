#!/usr/bin/env python3
"""Every deliverable the call asks for, from one command. Idempotent.

The river is a pure `f(seed, t)` and the captures in `program.json` are presets
for RECORDING the river starting at a given `--start` offset, so most of this is
not rendering — it is SELECTING from the recorded river. That is the whole
leverage of the spine, and it shows up here as arithmetic:

    passage           RENDERED. 4K ProRes 422 HQ (one whole passage at 4K),
                      the primary submission recording.
    midnight-moment   sliced from the passage recording. ProRes is all-intra,
                      so every frame is a keyframe and a cut is frame-exact with
                      no re-encode at all — Times Square gets literally the film's
                      own frames.
    screener          the passage recording, scaled to 1080p.
    trailer           sliced, then scaled to 1080p.
    reel              RENDERED. The one capture preset that cannot be derived,
                      because 1080x1920 is a vertical aspect and `cover`
                      projection therefore chooses a different field of view.
    stills            six one-frame renders at distinct seeds, named by seed.

SOUND IS SLICED, NEVER RE-SCORED. `score.py --window trailer` is a legitimate
standalone composition, but it starts its bed and its voice phrasing at the
capture's own start time, so the same absolute moment would sound different in the
passage recording and in the Times Square cut. Slicing one passage score means a
moment sounds the way it sounds, in every crop of the film that contains it.

    render/deliver.py                 # everything
    render/deliver.py --only stills
    render/deliver.py --start 120.0   # select the passage containing river time 120s
    render/deliver.py --force reel    # re-make one that already exists
    render/deliver.py --out <scratch-render-root> --package <package-root>
    render/deliver.py --preflight      # same dependency plan, no writes or rendering
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DANSE = HERE.parent
PROGRAM = HERE / "program.json"
DEFAULT_OUT = HERE / "out"
OUT = DEFAULT_OUT
PACKAGE = DEFAULT_OUT / "package"
SCORE = DANSE / "sound" / "score.py"
RENDER = HERE / "render.py"
REGISTER = DANSE / "submission" / "screendance-2027.yaml"
RAW = DANSE / "pipeline" / ".work" / "raw"
BANK = DANSE / "sound" / "bank" / "bank.json"
sys.path.insert(0, str(DANSE / "sound"))
sys.path.insert(0, str(DANSE / "pipeline"))
from bank_contract import audit_bank  # noqa: E402
from corpus_contract import authorize_render_tier  # noqa: E402

# Captures that are sub-spans or scaled versions of the primary 4K `passage` capture,
# so they can be cut/scaled from it. `copy` means stream-copy (no re-encode at all).
DERIVED = {
    "midnight-moment": {"suffix": ".mov", "mode": "copy", "audio": "pcm_s24le"},
    "trailer": {"suffix": ".mp4", "mode": "scale", "audio": "aac"},
    "screener": {"suffix": ".mp4", "mode": "scale", "audio": "aac"},
}

# Six moments, chosen to span the arc rather than to flatter one cut: the
# composite intact, the composite coming apart, the engine at full stride twice,
# a body that never existed, and a reseed.
STILL_FRACTIONS = (0.08, 0.22, 0.38, 0.54, 0.70, 0.88)

SELECTORS = ("master", "derived", "reel", "stills", "origin", "text")
FORCE_ITEMS = (*SELECTORS, *DERIVED)
AUDIO_ITEMS = {
    "master.mov",
    "midnight-moment.mov",
    "trailer.mp4",
    "screener.mp4",
    "reel.mp4",
}
PASSAGE_SELECTORS = {"master", "derived", "reel", "stills"}
FIXED_WINDOW_ITEMS = {"midnight-moment.mov", "trailer.mp4", "reel.mp4"}


def sh(cmd: list, **kw) -> subprocess.CompletedProcess:
    rendered = [str(c) for c in cmd]
    try:
        return subprocess.run(rendered, capture_output=True, text=True, **kw)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(rendered, 127, stdout="", stderr=str(exc))


def ffmpeg(args: list) -> None:
    done = sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])
    if done.returncode != 0:
        raise SystemExit(f"ffmpeg failed:\n{' '.join(str(a) for a in args)}\n{done.stderr.strip()}")


def probe(path: Path) -> dict | None:
    if not path.is_file() or shutil.which("ffprobe") is None:
        return None
    done = sh(
        # fmt: off
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,channels",
            "-of",
            "json",
            path,
        ]
        # fmt: on
    )
    if done.returncode != 0:
        return None
    raw = json.loads(done.stdout)
    out = {"seconds": float(raw["format"]["duration"]), "bytes": int(raw["format"]["size"])}
    for s in raw.get("streams", []):
        if s["codec_type"] == "video" and "width" not in out:
            num, den = s["r_frame_rate"].split("/")
            out |= {"width": s["width"], "height": s["height"], "fps": round(int(num) / max(int(den), 1), 3)}
            out["vcodec"] = s["codec_name"]
        elif s["codec_type"] == "audio" and "acodec" not in out:
            out |= {"acodec": s["codec_name"], "channels": s.get("channels")}
    return out


def probe_required(path: Path) -> dict | None:
    """Probe media without mistaking a missing tool for an invalid artifact."""
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is required for media delivery; run deliver.py --preflight")
    return probe(path)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@functools.lru_cache(maxsize=None)
def delivery_source_sha256(tier: str) -> str:
    """Identity of every tracked or derived byte that can change a package artifact."""
    roots = [
        DANSE / "film.html",
        DANSE / "arrival.js",
        PROGRAM,
        HERE / "deliver.py",
        HERE / "render.py",
        HERE / "browser.py",
        DANSE / "pipeline/corpus_contract.py",
        DANSE / "corpus/manifest.json",
        DANSE / "corpus/room.webp",
        DANSE / "corpus/score-2017.json",
        DANSE / "corpus/manifest.local.json",
        DANSE / "corpus" / "tier-receipts" / f"{tier}.json",
        DANSE / "sound/control.mjs",
        DANSE / "sound/score.py",
        DANSE / "sound/rng.py",
        DANSE / "sound/bank_contract.py",
        BANK,
    ]
    roots.extend(sorted((DANSE / "engine").glob("*.js")))
    for kind in ("plates", "mattes"):
        roots.extend(sorted((DANSE / "corpus" / kind / tier).glob("*.webp")))
    h = hashlib.sha256()
    for path in roots:
        if path.is_file():
            h.update(str(path.relative_to(DANSE)).encode())
            h.update(bytes.fromhex(digest(path)))
    return h.hexdigest()


def captures(program: dict) -> dict:
    return {k: v for k, v in program.get("captures", {}).items() if isinstance(v, dict)}


def hexseed(seed: int) -> str:
    return f"0x{seed:X}"


@functools.lru_cache(maxsize=None)
def _capture_span_items(capture_name: str, seed: int | None = None, start: float = 0.0) -> tuple:
    """Cache the immutable representation of one control-track query."""
    cmd = [
        "node",
        str(DANSE / "sound" / "control.mjs"),
        "--window",
        capture_name,
        "--from",
        str(start),
        "--rate",
        "0",
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    done = sh(cmd)
    if done.returncode != 0:
        raise SystemExit(f"failed to query capture span for {capture_name}:\n{done.stderr.strip()}")
    data = json.loads(done.stdout)
    return tuple(
        {
            "t0": data["t0"],
            "t1": data["t1"],
            "duration": data["duration"],
            "seed": data["passageSeed"],
            "river_seed": data["seed"],
            "passage": data["passage"],
            "capture": data["capture"],
            "origin": data.get("origin"),
        }.items()
    )


def query_capture_span(capture_name: str, seed: int | None = None, start: float = 0.0) -> dict:
    """Return a fresh mapping while reusing the pure control-track subprocess."""
    return dict(_capture_span_items(capture_name, seed, start))


def hydrated_work_root() -> Path:
    """Honor the same external private-work mount as export and origin delivery."""
    configured = os.environ.get("DANSE_WORK")
    return Path(configured).expanduser() if configured else RAW.parent


def registered_origin() -> Path:
    """The submission register is the sole owner of the source photograph."""
    register = yaml.safe_load(REGISTER.read_text()) or {}
    spec = (register.get("package") or {}).get("origin_still") or {}
    filename = spec.get("source_filename")
    if not filename:
        raise SystemExit(f"{REGISTER} does not declare package.origin_still.source_filename")
    if spec.get("copy_mode") != "byte-identical":
        raise SystemExit(f"{REGISTER} must declare package.origin_still.copy_mode: byte-identical")
    source_sha256 = spec.get("source_sha256")
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise SystemExit(f"{REGISTER} must declare package.origin_still.source_sha256")
    return hydrated_work_root() / "raw" / filename


def registered_origin_source_sha256() -> str:
    """The previously approved byte identity of the source photograph."""
    register = yaml.safe_load(REGISTER.read_text()) or {}
    source_sha256 = (((register.get("package") or {}).get("origin_still") or {}).get("source_sha256"))
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise SystemExit(f"{REGISTER} must declare package.origin_still.source_sha256")
    return source_sha256.lower()


def registered_audio_sources() -> list[str]:
    """The only recordings a delivery score may claim, from the register."""
    register = yaml.safe_load(REGISTER.read_text()) or {}
    return list((((register.get("package") or {}).get("audio") or {}).get("source_recordings") or []))


def registered_audio_source_digests() -> dict[str, str]:
    register = yaml.safe_load(REGISTER.read_text()) or {}
    audio = ((register.get("package") or {}).get("audio") or {})
    declared = audio.get("source_sha256") or {}
    return {name: declared.get(name, "") for name in audio.get("source_recordings") or []}


def bank_provenance() -> dict | None:
    """Current usable grain-bank identity, bound to the registered sources."""
    audit = audit_bank(BANK, registered_audio_source_digests())
    if not audit.valid or audit.fingerprint is None:
        return None
    return {"bank_fingerprint": audit.fingerprint, "sources": list(audit.sources)}


def score_receipt_path(score: Path) -> Path:
    return score.with_suffix(".json")


def score_provenance(score: Path, span: dict) -> dict | None:
    """Provenance bound to the exact cached score bytes and absolute span."""
    receipt = score_receipt_path(score)
    if not score.is_file() or not receipt.is_file():
        return None
    try:
        data = json.loads(receipt.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    sources = data.get("sources") or []
    fingerprint = data.get("bank_fingerprint")
    try:
        valid = (
            data.get("schema") == "danse.score.receipt.v1"
            and isinstance(fingerprint, str)
            and bool(fingerprint)
            and all(isinstance(source, str) for source in sources)
            and data.get("sha256") == digest(score)
            and abs(float(data.get("t0", -1)) - span["t0"]) < 1e-9
            and abs(float(data.get("duration", -1)) - span["duration"]) < 1e-3
            and sorted(sources) == sorted(registered_audio_sources())
        )
    except (TypeError, ValueError):
        return None
    if not valid:
        return None
    return {"bank_fingerprint": fingerprint, "sources": sources, "score_sha256": data["sha256"]}


def write_score_receipt(score: Path, span: dict, provenance: dict) -> None:
    payload = {
        "schema": "danse.score.receipt.v1",
        "sha256": digest(score),
        "t0": span["t0"],
        "t1": span["t1"],
        "duration": span["duration"],
        **provenance,
    }
    score_receipt_path(score).write_text(json.dumps(payload, indent=2) + "\n")


def capture_root(root: Path, span: dict, start: float) -> Path:
    """Keep restartable intermediates for different absolute spans disjoint."""
    offset = f"{start:.3f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p") or "0"
    return root / f"passage-{span['seed']:08X}-from-{offset}"


def is_forced(force: set[str], name: str, group: str | None = None) -> bool:
    return name in force or (group is not None and group in force)


def recognized_package_media(package: Path) -> list[Path]:
    """Known delivery media that cannot be adopted without a manifest."""
    paths = [package / name for name in sorted(AUDIO_ITEMS)]
    stills = package / "stills"
    if stills.is_dir():
        paths.extend(stills.glob("seed-0x*.*"))
        paths.append(stills / "origin-2017.jpg")
    return sorted({path for path in paths if path.is_file()})


def regular_directory_slot(path: Path) -> bool:
    """True when a delivery directory is absent or a real directory."""
    return not path.is_symlink() and (not path.exists() or path.is_dir())


def package_provenance_matches(
    package: Path,
    span: dict,
    start: float | None = None,
    source_tree_sha256: str | None = None,
) -> bool:
    manifest = package / "manifest.json"
    if not manifest.is_file():
        return not recognized_package_media(package)
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    items = data.get("items", [])
    if not isinstance(items, list):
        return False
    item_names = {
        item.get("name") for item in items if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    passage_items = {name for name in item_names if name in AUDIO_ITEMS or name.startswith("stills/seed-")}
    if not passage_items:
        unmanifested_passage_media = [
            path
            for path in recognized_package_media(package)
            if path.name != "origin-2017.jpg"
        ]
        return not unmanifested_passage_media
    try:
        passage_matches = (
            data.get("passage_seed") == hexseed(span["seed"])
            and data.get("passage") == span["passage"]
            and abs(float(data.get("t0", -1)) - span["t0"]) < 1e-9
            and abs(float(data.get("t1", -1)) - span["t1"]) < 1e-9
            and abs(float(data.get("duration", -1)) - span["duration"]) < 1e-3
        )
        start_matches = not (passage_items & FIXED_WINDOW_ITEMS) or (
            start is not None and abs(float(data.get("start", -1)) - start) < 1e-9
        )
        source_matches = source_tree_sha256 is None or data.get("source_tree_sha256") == source_tree_sha256
        return passage_matches and start_matches and source_matches
    except (TypeError, ValueError):
        return False


def still_destinations(program: dict, package: Path) -> list[Path]:
    sys.path.insert(0, str(DANSE / "sound"))
    from rng import hash32

    return [
        package / "stills" / f"seed-{hexseed(hash32(program['seed'], 0x57111, i) & 0xFFFFFF)}.jpg"
        for i in range(len(STILL_FRACTIONS))
    ]


def pending(program: dict, only: set[str], force: set[str], package: Path) -> dict:
    """The outputs that would actually be rebuilt for this invocation."""
    score_forced = "master" in force
    derived = {
        name
        for name, spec in DERIVED.items()
        if "derived" in only
        and (
            score_forced
            or is_forced(force, name, "derived")
            or not (package / f"{name}{spec['suffix']}").is_file()
        )
    }
    stills = still_destinations(program, package)
    return {
        "master": "master" in only and (is_forced(force, "master") or not (package / "master.mov").is_file()),
        "derived": derived,
        "reel": "reel" in only
        and (score_forced or is_forced(force, "reel") or not (package / "reel.mp4").is_file()),
        "stills": "stills" in only and (is_forced(force, "stills") or not all(path.is_file() for path in stills)),
    }


def expand_rebuilt_score_dependents(work: dict, only: set[str]) -> None:
    """A new score invalidates every selected artifact that embeds its bytes."""
    if "master" in only:
        work["master"] = True
    if "derived" in only:
        work["derived"] = set(DERIVED)
    if "reel" in only:
        work["reel"] = True


def capture_span_error(name: str, passage_span: dict, start: float) -> str | None:
    """Explain when a fixed capture would overrun its selected passage."""
    span = query_capture_span(name, start=start)
    if span["t0"] < passage_span["t0"] - 1e-9 or span["t1"] > passage_span["t1"] + 1e-9:
        return (
            f"{name} [{span['t0']:.3f}, {span['t1']:.3f}] does not fit passage "
            f"[{passage_span['t0']:.3f}, {passage_span['t1']:.3f}]"
        )
    return None


def preflight(
    program: dict,
    span: dict | None,
    only: set[str],
    force: set[str],
    tier: str,
    render_root: Path,
    package: Path,
    origin: Path | None,
    start: float = 0.0,
    span_error: str | None = None,
    passage_requested: bool = True,
) -> int:
    """Validate a delivery invocation without creating a directory or rendering."""
    rows: list[tuple[bool, str, str]] = []

    def add(ok: bool, name: str, detail: str) -> None:
        rows.append((ok, name, detail))

    package_root_ok = regular_directory_slot(package)
    add(package_root_ok, "package root", str(package))
    add(program.get("schema") == "danse.program.v2", "program", str(program.get("schema")))
    add(
        not passage_requested or (span is not None and span["duration"] > 0),
        "capture span",
        "not needed for passage-independent outputs"
        if not passage_requested
        else (f"{span['duration']:.3f}s from {span['t0']:.3f}s" if span else span_error or "unavailable"),
    )
    node = shutil.which("node")
    add(not passage_requested or node is not None, "node", node or ("not needed" if not passage_requested else "missing"))
    add(
        not passage_requested
        or (
            package_root_ok
            and span is not None
            and package_provenance_matches(package, span, start, delivery_source_sha256(tier))
        ),
        "package passage provenance",
        "preserved" if not passage_requested else str(package / "manifest.json"),
    )

    work = pending(program, only, force, package)
    span_names = sorted(work["derived"] | ({"reel"} if work["reel"] else set()))
    for name in span_names:
        if span is None:
            add(False, f"{name} fits selected passage", span_error or "capture span unavailable")
            continue
        try:
            error = capture_span_error(name, span, start)
        except SystemExit as exc:
            error = str(exc)
        add(error is None, f"{name} fits selected passage", error or f"within {span['duration']:.3f}s passage")
    need_picture = work["master"] or bool(work["derived"])
    need_score = need_picture or work["reel"]
    need_renderer = work["reel"] or work["stills"]

    picture = render_root / "passage-default.mov"
    picture_info = probe(picture)
    cap = captures(program)["passage"]
    picture_candidate = bool(
        span
        and picture_info
        and abs(picture_info.get("seconds", 0) * cap.get("fps", 30) - span["duration"] * cap.get("fps", 30)) < 2
        and not is_forced(force, "master")
    )
    picture_ready = False
    if need_picture and picture_candidate:
        checked = subprocess.run(
            [
                sys.executable,
                str(RENDER),
                "--capture",
                "passage",
                "--start",
                str(span["t0"]),
                "--tier",
                tier,
                "--codec",
                "prores",
                "--quiet",
                "--out",
                str(render_root),
                "--check-concat",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        picture_ready = checked.returncode == 0
    need_renderer = need_renderer or (need_picture and not picture_ready)

    score = render_root / "passage-score.wav"
    score_info = probe(score)
    score_ready = bool(
        span
        and score_info
        and abs(score_info.get("seconds", 0) - span["duration"]) < 0.1
        and score_provenance(score, span)
        and not is_forced(force, "master")
    )
    need_bank = need_score and not score_ready

    if need_picture or need_score or work["reel"] or work["stills"]:
        for command in ("ffmpeg", "ffprobe"):
            add(shutil.which(command) is not None, command, shutil.which(command) or "missing")

    if only & PASSAGE_SELECTORS:
        tier_ok, tier_detail = authorize_render_tier(DANSE / "corpus", hydrated_work_root(), tier)
        add(tier_ok, f"corpus tier {tier} receipt", tier_detail)

    if need_renderer:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        add(importlib.util.find_spec("playwright") is not None, "Playwright", "Python module")
        add(chrome.is_file(), "Google Chrome", str(chrome))

    if work["stills"]:
        add(importlib.util.find_spec("PIL") is not None, "Python module Pillow", "package still dependency")

    if need_bank:
        for module in ("numpy", "scipy"):
            add(importlib.util.find_spec(module) is not None, f"Python module {module}", "score dependency")
        declared = registered_audio_source_digests()
        audit = audit_bank(BANK, declared)
        add(BANK.is_file(), "grain bank", str(BANK))
        add(
            not audit.provenance_errors,
            "confirmed apartment recordings",
            f"{len(audit.sources)}/{len(declared)} exact sources declared in {REGISTER.name}"
            if not audit.provenance_errors
            else "; ".join(audit.provenance_errors),
        )
        add(not audit.index_errors, "grain bank contract", "; ".join(audit.index_errors) or audit.summary())
        add(not audit.payload_errors, "grain payloads", "; ".join(audit.payload_errors[:4]) or "every indexed WAV exists")

    if "origin" in only:
        origin_dest = package / "stills" / "origin-2017.jpg"
        origin_slot_ok = (
            package_root_ok
            and regular_directory_slot(origin_dest.parent)
            and not origin_dest.is_symlink()
            and (not origin_dest.exists() or origin_dest.is_file())
        )
        add(origin_slot_ok, "staged origin is a regular file", str(origin_dest))
        need_origin_source = is_forced(force, "origin") or not origin_dest.is_file()
        candidate = origin if need_origin_source else origin_dest
        candidate_exists = (
            origin_slot_ok
            and candidate is not None
            and candidate.is_file()
            and (need_origin_source or not candidate.is_symlink())
        )
        expected_origin = registered_origin_source_sha256()
        add(
            candidate_exists,
            "unaltered origin photograph",
            str(candidate),
        )
        origin_identity_ok = False
        origin_identity_detail = expected_origin
        if candidate_exists:
            try:
                origin_identity_ok = digest(candidate) == expected_origin
            except OSError as exc:
                origin_identity_detail = f"{candidate}: source bytes are unreadable ({exc})"
        add(
            origin_identity_ok,
            "registered origin photograph identity",
            origin_identity_detail,
        )
    if "text" in only:
        text_root = DANSE / "submission" / "text"
        names = {
            "synopsis_short",
            "synopsis_long",
            "artist_statement",
            "bio",
            "technical_note",
            "rights_declaration",
        }
        missing = sorted(name for name in names if not (text_root / f"{name}.txt").is_file())
        add(not missing, "tracked text package", f"{len(names) - len(missing)}/{len(names)} sources")

    print("delivery preflight\n")
    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL':>4}] {name} — {detail}")
    failures = sum(not ok for ok, _, _ in rows)
    print(f"\n{'READY' if not failures else 'NOT READY'} — {failures} failure(s); no files changed")
    return 1 if failures else 0


# ── the expensive half ─────────────────────────────────────────────────────────


def passage_picture(program: dict, tier: str, force: bool, start: float = 0.0) -> Path:
    """Render the primary 4K passage recording, or keep it. `render.py --resume` decides per segment."""
    stem = OUT / "passage-default"
    dest = stem.with_suffix(".mov")
    span = query_capture_span("passage", start=start)
    cap = captures(program)["passage"]
    fps = cap.get("fps", 30)
    want = int(round(span["duration"] * fps))
    render_command = [
        sys.executable,
        str(RENDER),
        "--capture",
        "passage",
        "--start",
        str(span["t0"]),
        "--tier",
        tier,
        "--codec",
        "prores",
        "--quiet",
        "--out",
        str(OUT),
    ]
    if not force:
        got = probe_required(dest) if dest.is_file() else None
        if got and abs(got["seconds"] * fps - want) < 2:
            checked = subprocess.run([*render_command, "--check-concat"], capture_output=True, text=True, check=False)
            if checked.returncode == 0:
                print(f"  passage picture · kept · {got['width']}×{got['height']} @{got['fps']} · {got['seconds']:.1f}s")
                return dest
    print("  passage picture · rendering (this is the long one)")
    done = subprocess.run(
        [*render_command, "--resume"],
        check=False,
    )
    if done.returncode != 0 or not dest.is_file():
        raise SystemExit("the passage picture would not render")
    return dest


def passage_sound(force: bool, start: float = 0.0) -> tuple[Path, dict, bool]:
    """One score for the passage recording. Every derived capture is cut from it."""
    dest = OUT / "passage-score.wav"
    span = query_capture_span("passage", start=start)
    if not force:
        got = probe_required(dest) if dest.is_file() else None
        provenance = score_provenance(dest, span) if got else None
        current_bank = bank_provenance() if provenance else None
        bank_matches = bool(
            provenance
            and current_bank
            and provenance.get("bank_fingerprint") == current_bank.get("bank_fingerprint")
            and provenance.get("sources") == current_bank.get("sources")
        )
        if got and provenance and bank_matches and abs(got["seconds"] - span["duration"]) < 0.1:
            print(f"  passage score · kept · {got['seconds']:.1f}s")
            return dest, provenance, False
    provenance = bank_provenance()
    if provenance is None:
        raise SystemExit(f"{BANK} must contain only the recordings declared by {REGISTER.name}")
    print("  passage score · rendering")
    done = subprocess.run(
        [sys.executable, str(SCORE), "--window", "passage", "--from", str(span["t0"]), "--out", str(dest)],
        check=False,
    )
    if done.returncode != 0 or not dest.is_file():
        raise SystemExit("the score would not render")
    write_score_receipt(dest, span, provenance)
    return dest, {**provenance, "score_sha256": digest(dest)}, True


def mux(video: Path, audio: Path, dest: Path, acodec: str, vcopy: bool = True, vfilter: str | None = None) -> None:
    args = ["-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
    if vcopy:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if vfilter:
        args += ["-vf", vfilter]
    args += ["-c:a", acodec] + (["-b:a", "320k"] if acodec == "aac" else []) + ["-shortest", dest]
    ffmpeg(args)


def cut_audio(source: Path, t0: float, seconds: float, dest: Path, fade: float = 0.3) -> None:
    """A capture's sound, from the passage score, with edges that do not click."""
    filters = [] if fade <= 0 else [f"afade=t=in:st=0:d={fade}", f"afade=t=out:st={max(0.0, seconds - fade)}:d={fade}"]
    args = ["-ss", t0, "-t", seconds, "-i", source]
    if filters:
        args += ["-af", ",".join(filters)]
    ffmpeg([*args, dest])


# ── deliverables ───────────────────────────────────────────────────────────────


def deliver_passage(picture: Path, sound: Path, force: bool) -> Path:
    dest = PACKAGE / "master.mov"
    if dest.is_file() and not force:
        return dest
    print("  master.mov (4K passage) · muxing")
    mux(picture, sound, dest, "pcm_s24le")
    return dest


def deliver_derived(
    name: str, spec: dict, program: dict, picture: Path, sound: Path, force: bool, start: float = 0.0
) -> Path:
    cap = captures(program)[name]
    span = query_capture_span(name, start=start)
    passage_span = query_capture_span("passage", start=start)
    error = capture_span_error(name, passage_span, start)
    if error:
        raise SystemExit(error)

    rel_t0 = max(0.0, span["t0"] - passage_span["t0"])
    seconds = span["duration"]
    fps = cap.get("fps", 30)
    w_out, h_out = cap.get("w", 1920), cap.get("h", 1080)

    dest = PACKAGE / f"{name}{spec['suffix']}"
    if dest.is_file() and not force:
        return dest
    print(f"  {dest.name} · {'slicing' if spec['mode'] == 'copy' else 'slicing + scaling'} from the passage recording")

    tmp_v = OUT / f".{name}-v{spec['suffix']}"
    tmp_a = OUT / f".{name}-a.wav"
    if spec["mode"] == "copy":
        ffmpeg(["-ss", rel_t0, "-t", seconds, "-i", picture, "-c", "copy", tmp_v])
    else:
        ffmpeg(["-ss", rel_t0, "-t", seconds, "-i", picture, "-c", "copy", OUT / f".{name}-raw.mov"])
        tmp_v = OUT / f".{name}-raw.mov"

    cut_audio(sound, rel_t0, seconds, tmp_a, fade=0.0 if name == "screener" else 0.3)
    scale = None if spec["mode"] == "copy" else f"scale={w_out}:{h_out}:flags=lanczos"
    mux(tmp_v, tmp_a, dest, spec["audio"], vcopy=(spec["mode"] == "copy"), vfilter=scale)
    for junk in (OUT / f".{name}-v{spec['suffix']}", OUT / f".{name}-a.wav", OUT / f".{name}-raw.mov"):
        junk.unlink(missing_ok=True)

    got = probe_required(dest)
    if not got:
        raise SystemExit(f"ffprobe could not inspect {dest.name} after muxing")
    want_frames = int(round(seconds * fps))
    have = int(round(got["seconds"] * got.get("fps", fps)))
    if abs(have - want_frames) > 1:
        raise SystemExit(f"{dest.name} is {have} frames, the capture declares {want_frames} — the slice is wrong")
    print(f"      {got['seconds']:.3f}s · {have} frames (declared {want_frames})")
    return dest


def deliver_reel(program: dict, sound: Path, tier: str, force: bool, start: float = 0.0) -> Path:
    """The one capture preset that must be rendered — vertical aspect is a different field of view."""
    dest = PACKAGE / "reel.mp4"
    if dest.is_file() and not force:
        return dest
    span = query_capture_span("reel", start=start)
    passage_span = query_capture_span("passage", start=start)
    error = capture_span_error("reel", passage_span, start)
    if error:
        raise SystemExit(error)
    rel_t0 = max(0.0, span["t0"] - passage_span["t0"])
    seconds = span["duration"]

    print("  reel.mp4 · rendering (vertical is a different field of view, not a crop)")
    with tempfile.TemporaryDirectory(prefix=".reel-", dir=OUT) as render_tmp:
        render_out = Path(render_tmp)
        stem = render_out / "reel-default"
        render_command = [
            sys.executable,
            str(RENDER),
            "--capture",
            "reel",
            "--start",
            str(span["t0"]),
            "--tier",
            tier,
            "--codec",
            "h264",
            "--quiet",
            "--out",
            str(render_out),
        ]
        done = subprocess.run(render_command, check=False)
        picture = stem.with_suffix(".mp4")
        if done.returncode == 0 and not picture.is_file():
            # A one-part full plan is left at its segment path. Ask the
            # renderer to validate every planned segment and create the
            # canonical output rather than adopting a segment directly.
            done = subprocess.run([*render_command, "--concat"], check=False)
        if done.returncode != 0 or not picture.is_file():
            raise SystemExit("the reel would not render")
        tmp_a = render_out / "reel-a.wav"
        cut_audio(sound, rel_t0, seconds, tmp_a)
        with tempfile.TemporaryDirectory(prefix=".reel-publish-", dir=PACKAGE) as publish_tmp:
            staged = Path(publish_tmp) / dest.name
            mux(picture, tmp_a, staged, "aac")
            got = probe_required(staged)
            fps = captures(program).get("reel", {}).get("fps", 30)
            want_frames = int(round(seconds * fps))
            have = int(round(got["seconds"] * got.get("fps", fps))) if got else -1
            if not got or abs(have - want_frames) > 1:
                raise SystemExit(
                    f"reel.mp4 is {have} frames, the capture declares {want_frames} — the render is wrong"
                )
            staged.replace(dest)
            print(f"      {got['seconds']:.3f}s · {have} frames (declared {want_frames})")
    return dest


def deliver_stills(program: dict, tier: str, force: bool, start: float = 0.0) -> list[Path]:
    """Six frames, six seeds. The filename IS the provenance — `seed-0x….jpg`
    says this is one of the films, not the film."""
    (PACKAGE / "stills").mkdir(parents=True, exist_ok=True)
    cap = captures(program)["passage"]
    fps = cap.get("fps", 30)
    span = query_capture_span("passage", start=start)
    made = []
    for fraction, dest in zip(STILL_FRACTIONS, still_destinations(program, PACKAGE), strict=True):
        seed = int(dest.stem.removeprefix("seed-"), 0)
        still_span = query_capture_span("passage", seed=seed, start=span["t0"])
        t = still_span["duration"] * fraction
        if dest.is_file() and not force:
            continue
        frame = int(round(t * fps))
        print(f"  {dest.name} · t={t:.0f}s")
        for junk in OUT.glob(f"passage-{seed}*"):
            junk.unlink(missing_ok=True)
        done = subprocess.run(
            # fmt: off
            [
                sys.executable,
                str(RENDER),
                "--capture",
                "passage",
                "--start",
                str(still_span["t0"]),
                "--tier",
                tier,
                "--codec",
                "prores",
                "--seed",
                str(seed),
                "--segment",
                str(frame),
                "--segment-frames",
                "1",
                "--quiet",
                "--out",
                str(OUT),
            ],
            # fmt: on
            check=False,
        )
        one = OUT / f"passage-{seed}-seg-{frame:03d}.mov"
        if done.returncode != 0 or not one.is_file():
            raise SystemExit(f"still at t={t} would not render")
        ffmpeg(["-i", one, "-frames:v", "1", "-q:v", "2", dest])
        one.unlink(missing_ok=True)
        made.append(dest)
    return made


def deliver_text() -> list[Path]:
    """The written half, from its git-tracked source.

    These live in `submission/text/` and are COPIED here, never authored here:
    the package is a build artifact and gets wiped, and a synopsis is not
    something that should be recoverable only from a directory nobody backs up.
    """
    source = DANSE / "submission" / "text"
    if not source.is_dir():
        print(f"  text · MISSING SOURCE at {source}")
        return []
    dest = PACKAGE / "text"
    dest.mkdir(parents=True, exist_ok=True)
    made = []
    for path in sorted(source.glob("*.txt")):
        shutil.copy2(path, dest / path.name)
        made.append(dest / path.name)
    print(f"  text/ · {len(made)} files · {sum(len(p.read_text().split()) for p in made)} words")
    return made


def deliver_origin(origin: Path, force: bool) -> Path:
    dest = PACKAGE / "stills" / "origin-2017.jpg"
    expected = registered_origin_source_sha256()
    if (
        not regular_directory_slot(PACKAGE)
        or not regular_directory_slot(dest.parent)
        or dest.is_symlink()
        or (dest.exists() and not dest.is_file())
    ):
        raise SystemExit(f"staged origin photograph must be a regular non-symlink file: {dest}")
    if dest.is_file() and not force:
        if digest(dest) != expected:
            raise SystemExit(f"staged origin photograph does not match {REGISTER}; rerun with --force origin")
        # Return a verified reuse so the caller rewrites its manifest item from
        # the canonical register. Exact bytes are sufficient custody to repair
        # a missing or stale package receipt without the private raw mount.
        return dest
    if not origin.is_file():
        raise SystemExit(f"origin photograph source is missing at {origin}")
    actual = digest(origin)
    if actual != expected:
        raise SystemExit(f"registered origin identity mismatch for {origin.name}: {actual}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  origin-2017.jpg · byte-identical copy of {origin.name}")
    shutil.copy2(origin, dest)
    if (
        not regular_directory_slot(PACKAGE)
        or not regular_directory_slot(dest.parent)
        or not dest.is_file()
        or dest.is_symlink()
        or digest(dest) != expected
    ):
        raise SystemExit(f"copied origin photograph does not match registered identity: {dest}")
    return dest


def attestation_template() -> str:
    reg = yaml.safe_load(REGISTER.read_text()) or {}
    requirements = [
        item
        for section in ("requirements", "approvals")
        for item in reg.get(section, [])
        if item.get("check") == "manual"
    ]
    lines = [
        "# Human assertions. The package build creates nulls; only a human who",
        "# performed or verified an act may set its value to true.",
        "# check.py reads only the cumulative requirements owned by --phase.",
    ]
    requirements = [item for item in requirements if item.get("id")]
    for item in requirements:
        lines.append(f"#   {item['id']:<30} [{item.get('phase', 'UNOWNED')}] {item.get('rule', '')}")
    lines.extend(f"{item['id']}: null" for item in requirements)
    return "\n".join(lines) + "\n"


def main() -> int:
    global OUT, PACKAGE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="film", help="corpus tier for rendered items")
    ap.add_argument("--start", type=float, default=0.0, help="where in the river to begin recording (in seconds)")
    ap.add_argument("--only", action="append", choices=SELECTORS, help="build one output group (repeatable)")
    ap.add_argument("--force", action="append", choices=FORCE_ITEMS, default=[], help="re-make an existing item")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="root for restartable render intermediates")
    ap.add_argument("--package", type=Path, help="staged package root (default: <out>/package)")
    ap.add_argument("--preflight", action="store_true", help="validate this invocation without rendering or writing")
    args = ap.parse_args()
    if args.start < 0:
        ap.error("--start must be non-negative")

    program = json.loads(PROGRAM.read_text())
    only = set(args.only or SELECTORS)
    force = set(args.force)
    package = args.package or (args.out / "package")
    origin = registered_origin() if "origin" in only else None
    passage_requested = bool(only & PASSAGE_SELECTORS)
    span_error = None
    span = None
    if passage_requested:
        try:
            span = query_capture_span("passage", start=args.start)
        except SystemExit as exc:
            if not args.preflight:
                raise
            span_error = str(exc)
    render_root = capture_root(args.out, span, span["t0"]) if span else args.out
    if args.preflight:
        return preflight(
            program,
            span,
            only,
            force,
            args.tier,
            render_root,
            package,
            origin,
            args.start,
            span_error,
            passage_requested,
        )
    if not regular_directory_slot(package):
        raise SystemExit(f"package root must be an absent or regular non-symlink directory: {package}")
    source_tree = delivery_source_sha256(args.tier) if passage_requested else None
    if passage_requested and not package_provenance_matches(package, span, args.start, source_tree):
        raise SystemExit(f"{package}/manifest.json belongs to a different passage; choose a fresh --package root")

    work = pending(program, only, force, package)
    selected_fixed_windows = {
        name for name in DERIVED if "derived" in only
    } | ({"reel"} if "reel" in only else set())
    for name in sorted(selected_fixed_windows):
        assert span is not None
        error = capture_span_error(name, span, args.start)
        if error:
            raise SystemExit(error)
    audio_selected = bool(only & {"master", "derived", "reel"})
    media_pending = bool(work["master"] or work["derived"] or work["reel"] or work["stills"])
    if (media_pending or audio_selected) and shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is required for media delivery; run deliver.py --preflight")

    OUT = render_root
    PACKAGE = package
    OUT.mkdir(parents=True, exist_ok=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)

    if span:
        print(
            f"{program['title']} · seed {hexseed(program['seed'])} · passage seed {hexseed(span['seed'])} · "
            f"{span['duration']:.1f}s (start at {args.start:.1f}s)\n"
        )
    else:
        print(f"{program['title']} · passage-independent package update\n")

    score_forced = "master" in force
    sound, sound_provenance, score_rebuilt = (
        passage_sound(score_forced, start=args.start) if audio_selected else (None, None, False)
    )
    if score_rebuilt:
        expand_rebuilt_score_dependents(work, only)
    need_picture = work["master"] or bool(work["derived"])
    picture = passage_picture(program, args.tier, score_forced, start=args.start) if need_picture else None
    made: list[Path] = []

    if "master" in only and work["master"]:
        made.append(deliver_passage(picture, sound, score_forced))
    if "derived" in only:
        for name, spec in DERIVED.items():
            if name in work["derived"]:
                made.append(
                    deliver_derived(
                        name,
                        spec,
                        program,
                        picture,
                        sound,
                        score_forced or is_forced(force, name, "derived"),
                        start=args.start,
                    )
                )
    if "reel" in only and work["reel"]:
        made.append(deliver_reel(program, sound, args.tier, score_forced or "reel" in force, start=args.start))
    if "stills" in only:
        made += deliver_stills(program, args.tier, "stills" in force, start=args.start)
    if "text" in only:
        made += deliver_text()
    if "origin" in only:
        assert origin is not None
        got = deliver_origin(origin, "origin" in force)
        if got:
            made.append(got)

    attest = PACKAGE / "attest.yaml"
    if not attest.exists():
        attest.write_text(attestation_template())
        print("  attest.yaml · scaffold written — every line is a human's to set")

    print()
    manifest_path = PACKAGE / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    previous_items = {
        item["name"]: item
        for item in previous.get("items", [])
        if isinstance(item, dict) and item.get("name") and (PACKAGE / item["name"]).is_file()
    }
    previous_sound = previous.get("sound") if isinstance(previous.get("sound"), dict) else None
    rebuilt_audio = {
        *({"master.mov"} if work["master"] else set()),
        *(f"{name}{DERIVED[name]['suffix']}" for name in work["derived"]),
        *({"reel.mp4"} if work["reel"] else set()),
    }
    manifest = {
        "schema": "danse.delivery.manifest.v1",
        "title": program["title"],
        "seed": hexseed(program["seed"]),
        "items": [],
    }
    passage_fields = ("passage_seed", "passage", "start", "t0", "t1", "duration")
    if span:
        manifest |= {
            "passage_seed": hexseed(span["seed"]),
            "passage": span["passage"],
            "start": args.start,
            "t0": span["t0"],
            "t1": span["t1"],
            "duration": span["duration"],
            "source_tree_sha256": source_tree,
        }
    else:
        manifest |= {key: previous[key] for key in (*passage_fields, "source_tree_sha256") if key in previous}
    for path in made:
        if not path.is_file():
            continue
        size = path.stat().st_size
        name = str(path.relative_to(PACKAGE))
        # ffprobe accepts arbitrary text through its `ansi` demuxer and treats
        # still images as one-frame video. Only time-based delivery media belongs
        # in this receipt; text and photographs have their own package predicates.
        info = (probe(path) or {}) if name in AUDIO_ITEMS else {}
        prior = previous_items.get(name) or {}
        item = {"name": name, "bytes": size, "sha256": digest(path), **info}
        if name in AUDIO_ITEMS:
            item_sound = (
                sound_provenance if name in rebuilt_audio else None
            ) or prior.get("sound") or previous_sound
            if item_sound:
                item["sound"] = item_sound
        if name == "stills/origin-2017.jpg":
            assert origin is not None
            item |= {
                "source": origin.name,
                "source_sha256": registered_origin_source_sha256(),
                "copy_mode": "byte-identical",
            }
        previous_items[name] = item
        shape = f"{info.get('width', '?')}×{info.get('height', '?')}"
        rate = f"@{info['fps']}" if "fps" in info else ""
        secs = f"{info['seconds']:.1f}s " if "seconds" in info else ""
        media = f"{secs}{shape} {rate}" if info else ""
        print(f"  {name:<28} {size / 1e6:>8.1f} MB  {media}")
    manifest["items"] = [previous_items[name] for name in sorted(previous_items)]
    master_sound = (previous_items.get("master.mov") or {}).get("sound")
    if master_sound:
        manifest["sound"] = master_sound
    elif previous_sound:
        manifest["sound"] = previous_sound
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(i["bytes"] for i in manifest["items"])
    print(f"\n  {len(manifest['items'])} items · {total / 1e9:.2f} GB · {PACKAGE}")
    if shutil.which("python3"):
        print("\nnext: submission/check.py --phase package --package " + str(PACKAGE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Is the ScreenDance package filable? Exit 0 ⟺ yes.

The register (`screendance-2027.yaml`) holds every fact about the call. This holds
none of them — it reads them. That separation is the point: a requirement can only
be wrong in one place, and it announces the date it was last checked.

Three kinds of check, and they fail differently on purpose:

    machine    ffprobe / PIL measure the artifact         PASS | FAIL
    attested   a human asserts it in package/attest.yaml  PASS | FAIL | MISSING
    unstated   the call never said; a phone call closes   OPEN

An OPEN blocking unknown is not a warning. It exits non-zero, because "we assumed
6:30 was fine" is exactly the failure that is only discovered after the deadline.

    ./check.py                        # register-level: deadline + open unknowns
    ./check.py --package .work/submission --phase package
    ./check.py --package .work/submission --phase uploaded
    ./check.py --package .work/submission --phase submitted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

HERE = Path(__file__).resolve().parent
REGISTER = HERE / "screendance-2027.yaml"

PASS, FAIL, OPEN, SKIP = "PASS", "FAIL", "OPEN", "SKIP"
GLYPH = {PASS: "\033[32m ok \033[0m", FAIL: "\033[31mFAIL\033[0m", OPEN: "\033[33mOPEN\033[0m", SKIP: "skip"}
PHASES = ("package", "uploaded", "submitted")

VIDEO_SUFFIXES = {".mov", ".mp4", ".mxf", ".m4v"}


class Report:
    """Results, and the exit code they imply."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, section: str, name: str, status: str, detail: str = "") -> None:
        self.rows.append((section, name, status, detail))

    def print(self) -> None:
        section = None
        for sec, name, status, detail in self.rows:
            if sec != section:
                print(f"\n\033[1m{sec}\033[0m")
                section = sec
            print(f"  [{GLYPH[status]}] {name}" + (f" — {detail}" if detail else ""))

    @property
    def failures(self) -> int:
        return sum(1 for _, _, s, _ in self.rows if s in (FAIL, OPEN))


# ── measurement ────────────────────────────────────────────────────────────────


def probe(path: Path) -> dict | None:
    """Video geometry and duration, or None if ffprobe is unavailable."""
    if not shutil.which("ffprobe"):
        return None
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,channels,sample_rate:"
            "stream_disposition=attached_pic:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return None
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
        ),
        {},
    )
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": round(fps, 3),
        "seconds": float((data.get("format") or {}).get("duration") or 0.0),
        "vcodec": video.get("codec_name"),
        "vprofile": video.get("profile"),
        "pix_fmt": video.get("pix_fmt"),
        "acodec": audio.get("codec_name"),
        "channels": audio.get("channels"),
        "sample_rate": int(audio.get("sample_rate") or 0),
    }


def loudness(path: Path) -> dict | None:
    """Integrated loudness and true peak measured from the staged artifact."""
    if not shutil.which("ffmpeg"):
        return None
    try:
        out = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-af",
                "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return None
    blocks = re.findall(r"\{\s*\"input_i\".*?\}", out.stderr, flags=re.DOTALL)
    if not blocks:
        return None
    try:
        measured = json.loads(blocks[-1])
        return {"lufs": float(measured["input_i"]), "true_peak_dbtp": float(measured["input_tp"])}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def words(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").split())


def find_one(root: Path, stem: str) -> Path | None:
    """The single file whose stem matches, of any video extension."""
    hits = [p for p in root.iterdir() if p.is_file() and p.stem == stem and p.suffix.lower() in VIDEO_SUFFIXES]
    return hits[0] if len(hits) == 1 else None


def read_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def manifest_items(root: Path) -> dict[str, dict]:
    return {
        item["name"]: item
        for item in read_manifest(root).get("items", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


# ── register-level checks (no package needed) ──────────────────────────────────


def check_deadline(reg: dict, phase: str, rep: Report, now: datetime | None = None) -> None:
    d = reg["deadline"]
    wall = datetime.fromisoformat(d["hard_wall"])
    now = now or datetime.now(ZoneInfo("America/New_York"))
    left = wall - now
    days = left.days + left.seconds / 86400

    if days < 0:
        if phase != "submitted":
            rep.add("deadline", "hard wall", FAIL, f"passed {abs(days):.1f} days ago ({d['stated']})")
            return
        rep.add(
            "deadline",
            "hard wall",
            PASS,
            f"passed {abs(days):.1f} days ago; the submitted attestation owns historical filing proof",
        )
    else:
        # The register's wall is already the cautious reading of an ambiguous
        # "EST" on a date when Miami runs EDT. Report against that, never against
        # the stated string.
        rep.add("deadline", "hard wall", PASS, f"{days:.1f} days left → {wall:%a %d %b %H:%M %Z}")

    target = datetime.fromisoformat(d["target_file_date"] + "T12:00:00-04:00")
    tdays = (target - now).days
    status = PASS if tdays >= 0 or phase == "submitted" else OPEN
    detail = (
        "target passed; submitted-phase receipt now owns closure"
        if tdays < 0 and phase == "submitted"
        else "file early; panel sees timestamps"
    )
    rep.add(
        "deadline",
        "target file date",
        status,
        f"{d['target_file_date']} ({tdays:+d} days) — {detail}",
    )


def check_unknowns(reg: dict, rep: Report) -> None:
    """The call is silent on these. Blocking ones exit non-zero; the rest report
    what stands in for the missing answer — evidence where we found some, a bare
    assumption where we did not — so the two never read alike."""
    for item in reg.get("unstated", []):
        if item.get("blocking", False):
            rep.add("unpublished by the call", item["id"], OPEN, item["resolve"])
            continue
        detail = (
            f"de-blocked by evidence — {item['evidence']}"
            if "evidence" in item
            else f"assuming {item.get('assume', item.get('assume_master', 'default'))}"
        )
        rep.add("unpublished by the call", item["id"], SKIP, detail)


# ── package checks ─────────────────────────────────────────────────────────────


def check_requirement_phases(reg: dict, rep: Report) -> None:
    rep.add(
        "register",
        "schema",
        PASS if reg.get("schema") == "danse.submission.v2" else FAIL,
        str(reg.get("schema")),
    )
    owned = [item for section in ("requirements", "approvals") for item in reg.get(section, [])]
    invalid = [item.get("id", "<unnamed>") for item in owned if item.get("phase") not in PHASES]
    rep.add(
        "register",
        "requirement phase ownership",
        PASS if not invalid else FAIL,
        f"{len(owned)} owned requirements and approvals"
        if not invalid
        else f"missing/invalid phase: {', '.join(invalid)}",
    )


def check_attestations(reg: dict, root: Path, phase: str, rep: Report) -> None:
    path = root / "attest.yaml"
    attested = yaml.safe_load(path.read_text()) if path.exists() else {}
    attested = attested or {}
    selected = PHASES.index(phase)
    for req in [item for section in ("requirements", "approvals") for item in reg.get(section, [])]:
        owner = req.get("phase")
        if req.get("check") != "manual" or owner not in PHASES or PHASES.index(owner) > selected:
            continue
        value = attested.get(req["id"])
        if value is True:
            rep.add(f"attested through {phase}", req["id"], PASS, req["rule"])
        elif value is False:
            rep.add(f"attested through {phase}", req["id"], FAIL, req["rule"])
        else:
            rep.add(
                f"attested through {phase}",
                req["id"],
                FAIL,
                f"unattested in attest.yaml (owned by {owner}) — {req['rule']}",
            )


def check_master(spec: dict, reg: dict, root: Path, rep: Report) -> None:
    path = find_one(root, "master")
    if not path:
        rep.add("package", "master", FAIL, "no unique master.<mov|mp4|mxf> in package")
        return
    info = probe(path)
    if not info:
        rep.add("package", "master", OPEN, f"{path.name} present; ffprobe unavailable — cannot verify")
        return

    w, h, fps, secs = info["width"], info["height"], info["fps"], info["seconds"]
    rep.add("package", "master present", PASS, f"{path.name} · {w}×{h} · {fps}fps · {secs / 60:.2f} min")

    ratio = (w / h) if h else 0
    want = 16 / 9
    ok_aspect = abs(ratio - want) < 0.01
    rep.add("package", "aspect 16:9", PASS if ok_aspect else FAIL, f"{ratio:.4f}")

    ok_fps = any(abs(fps - f) < 0.5 for f in spec["fps_allowed"])
    rep.add("package", "frame rate", PASS if ok_fps else FAIL, f"{fps} — allowed {spec['fps_allowed']}")

    ok_size = (w or 0) >= spec["min_width"] and (h or 0) >= spec["min_height"]
    rep.add(
        "package",
        "master resolution",
        PASS if ok_size else FAIL,
        f"{w}×{h} (min {spec['min_width']}×{spec['min_height']})",
    )
    ok_codec = (
        info["vcodec"] == spec["video_codec"] and str(info["vprofile"]).lower() == str(spec["video_profile"]).lower()
    )
    rep.add(
        "package",
        "master codec",
        PASS if ok_codec else FAIL,
        f"{info['vcodec']} {info['vprofile']} (want {spec['video_codec']} {spec['video_profile']})",
    )
    ok_audio = info["acodec"] == spec["audio_codec"] and info["channels"] == spec["audio_channels"]
    rep.add(
        "package",
        "master audio stream",
        PASS if ok_audio else FAIL,
        f"{info['acodec']} · {info['channels']} channels",
    )

    manifest = read_manifest(root)
    item = manifest_items(root).get(path.name) or {}
    expected_seconds = manifest.get("duration")
    duration_matches = isinstance(expected_seconds, (int, float)) and abs(secs - expected_seconds) * fps <= 2
    rep.add(
        "package",
        "master is one whole manifested passage",
        PASS if duration_matches else FAIL,
        f"{secs:.3f}s staged vs {expected_seconds!r}s manifested",
    )
    actual_digest = sha256(path)
    digest_matches = item.get("sha256") == actual_digest
    rep.add(
        "package",
        "master bytes match delivery manifest",
        PASS if digest_matches else FAIL,
        f"{actual_digest[:16]}…" + ("" if digest_matches else " — missing or stale manifest digest"),
    )

    cap = next((u.get("assume_max_seconds") for u in reg.get("unstated", []) if u["id"] == "runtime-cap"), None)
    if cap:
        # OPEN, not PASS: the cap is our assumption, not the festival's stated rule.
        status = OPEN if secs > cap else PASS
        rep.add(
            "package",
            "runtime vs assumed cap",
            status,
            f"{secs:.0f}s vs assumed {cap}s — cap is UNCONFIRMED, call {reg['phone']}",
        )


def check_screener(spec: dict, root: Path, rep: Report) -> None:
    path = find_one(root, "screener")
    if not path:
        rep.add("package", "screener", FAIL, "no unique screener.<mov|mp4> in package")
        return
    info = probe(path)
    if not info:
        rep.add("package", "screener", OPEN, f"{path.name} present; ffprobe unavailable")
        return
    ok = (info["width"] or 0) >= spec["min_width"] and (info["height"] or 0) >= spec["min_height"]
    rep.add(
        "package",
        "screener",
        PASS if ok else FAIL,
        f"{path.name} · {info['width']}×{info['height']} (min {spec['min_width']}×{spec['min_height']})",
    )
    ok_codec = info["vcodec"] == spec["video_codec"]
    rep.add(
        "package",
        "screener codec",
        PASS if ok_codec else FAIL,
        f"{info['vcodec']} (want {spec['video_codec']})",
    )
    ok_audio = info["acodec"] == spec["audio_codec"] and info["channels"] == spec["audio_channels"]
    rep.add(
        "package",
        "screener audio stream",
        PASS if ok_audio else FAIL,
        f"{info['acodec']} · {info['channels']} channels",
    )


def check_stills(spec: dict, root: Path, rep: Report, exempt: set[str] = frozenset()) -> None:
    folder = root / "stills"
    if not folder.is_dir():
        rep.add("package", "stills", FAIL, "no stills/ directory")
        return

    pattern = re.compile(spec["filename_pattern"])
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    named = [p for p in files if pattern.match(p.name)]
    # The origin photograph lives here too and is checked by name elsewhere; it is
    # not a seed still and must not read as a naming violation.
    misnamed = [p.name for p in files if not pattern.match(p.name) and p.name not in exempt]

    ok_count = len(named) >= spec["count_min"]
    rep.add(
        "package",
        "stills count",
        PASS if ok_count else FAIL,
        f"{len(named)} conforming of {len(files)} (min {spec['count_min']})"
        + (f"; misnamed: {', '.join(misnamed[:4])}" if misnamed else ""),
    )

    if spec.get("distinct_seeds"):
        seeds = {p.stem.lower() for p in named}
        ok = len(seeds) == len(named)
        rep.add("package", "stills distinct seeds", PASS if ok else FAIL, f"{len(seeds)} distinct of {len(named)}")

    undersized = []
    unmeasured = 0
    for p in named:
        size = image_size(p)
        if size is None:
            unmeasured += 1
        elif size[0] < spec["min_width"] or size[1] < spec["min_height"]:
            undersized.append(f"{p.name} {size[0]}×{size[1]}")
    if unmeasured:
        rep.add("package", "stills resolution", OPEN, f"{unmeasured} unmeasurable (Pillow missing?)")
    else:
        rep.add(
            "package",
            "stills resolution",
            FAIL if undersized else PASS,
            "; ".join(undersized[:4]) if undersized else f"all ≥ {spec['min_width']}×{spec['min_height']}",
        )


def check_origin_still(spec: dict, root: Path, rep: Report) -> None:
    path = root / "stills" / spec["filename"]
    exists = path.exists()
    rep.add(
        "package",
        "unaltered 2017 photograph",
        PASS if exists else FAIL,
        f"stills/{spec['filename']}" + ("" if exists else " — missing"),
    )
    if not exists:
        return
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    item = next(
        (entry for entry in manifest.get("items", []) if entry.get("name") == f"stills/{spec['filename']}"),
        {},
    )
    actual = sha256(path)
    copied = (
        item.get("source") == spec["source_filename"]
        and item.get("copy_mode") == spec["copy_mode"]
        and item.get("sha256") == actual
        and item.get("source_sha256") == actual
    )
    rep.add(
        "package",
        "origin is byte-identical to its registered source",
        PASS if copied else FAIL,
        f"{item.get('source', 'unrecorded')} · {actual[:16]}…",
    )


def check_trailer(spec: dict, root: Path, rep: Report) -> None:
    path = find_one(root, "trailer")
    if not path:
        rep.add("package", "trailer", SKIP, "optional, not staged")
        return
    info = probe(path)
    if not info:
        rep.add("package", "trailer", OPEN, "present; ffprobe unavailable")
        return
    ok = info["seconds"] <= spec["max_seconds"]
    rep.add("package", "trailer", PASS if ok else FAIL, f"{info['seconds']:.0f}s (max {spec['max_seconds']}s)")


def check_audio(spec: dict, root: Path, rep: Report) -> None:
    master = find_one(root, "master")
    if not master:
        return
    measured = loudness(master)
    if measured is None:
        rep.add("audio", "loudness", OPEN, "ffmpeg loudnorm measurement unavailable")
    else:
        delta = abs(measured["lufs"] - spec["target_lufs"])
        rep.add(
            "audio",
            "integrated loudness",
            PASS if delta <= spec["tolerance_lu"] else FAIL,
            f"{measured['lufs']:.2f} LUFS (target {spec['target_lufs']:.1f} ± {spec['tolerance_lu']:.1f})",
        )
        rep.add(
            "audio",
            "true peak",
            PASS if measured["true_peak_dbtp"] <= spec["max_true_peak_dbtp"] else FAIL,
            f"{measured['true_peak_dbtp']:.2f} dBTP (max {spec['max_true_peak_dbtp']:.1f})",
        )

    manifest = read_manifest(root)
    sources = set((manifest.get("sound") or {}).get("sources") or [])
    expected = set(spec["source_recordings"])
    rep.add(
        "audio",
        "registered apartment sources only",
        PASS if sources == expected else FAIL,
        f"{len(sources)}/{len(expected)} exact sources · bank {(manifest.get('sound') or {}).get('bank_fingerprint', 'missing')}",
    )

    items = manifest_items(root)
    audio_paths = [
        path
        for name in ("master.mov", "midnight-moment.mov", "trailer.mp4", "screener.mp4", "reel.mp4")
        if (path := root / name).is_file()
    ]
    stale: list[str] = []
    fingerprints: set[str] = set()
    for path in audio_paths:
        item = items.get(path.name) or {}
        sound = item.get("sound") if isinstance(item.get("sound"), dict) else {}
        fingerprint = sound.get("bank_fingerprint")
        if set(sound.get("sources") or []) != expected or not isinstance(fingerprint, str) or not fingerprint:
            stale.append(path.name)
            continue
        fingerprints.add(fingerprint)
    consistent = not stale and len(fingerprints) == 1 and bool(audio_paths)
    detail = (
        f"{len(audio_paths)} artifact(s) · bank {next(iter(fingerprints))}"
        if consistent
        else f"missing/stale: {', '.join(stale) or 'mixed bank fingerprints'}"
    )
    rep.add("audio", "per-artifact score provenance", PASS if consistent else FAIL, detail)


def check_text(spec: dict, root: Path, rep: Report) -> None:
    folder = root / "text"
    for name, rule in spec.items():
        path = folder / f"{name}.txt"
        if not path.exists():
            rep.add("text", name, FAIL if rule.get("required") else SKIP, f"text/{name}.txt missing")
            continue
        n = words(path)
        lo, hi = rule["words_min"], rule["words_max"]
        rep.add("text", name, PASS if lo <= n <= hi else FAIL, f"{n} words (want {lo}–{hi})")


# ── entry ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", type=Path, help="staged submission directory")
    ap.add_argument("--register", type=Path, default=REGISTER)
    ap.add_argument("--phase", choices=PHASES, default="package", help="cumulative delivery phase to validate")
    args = ap.parse_args()

    reg = yaml.safe_load(args.register.read_text())
    rep = Report()

    print(f"\033[1m{reg['call']}\033[0m — {reg['presenter']}")

    check_requirement_phases(reg, rep)
    check_deadline(reg, args.phase, rep)
    check_unknowns(reg, rep)

    if args.package:
        root = args.package
        if not root.is_dir():
            rep.add("package", "directory", FAIL, f"{root} does not exist")
        else:
            pkg = reg["package"]
            check_attestations(reg, root, args.phase, rep)
            check_master(pkg["master"], reg, root, rep)
            check_screener(pkg["screener"], root, rep)
            check_stills(pkg["stills"], root, rep, exempt={pkg["origin_still"]["filename"]})
            check_origin_still(pkg["origin_still"], root, rep)
            check_trailer(pkg["trailer"], root, rep)
            check_audio(pkg["audio"], root, rep)
            check_text(pkg["text"], root, rep)
    else:
        rep.add("package", "not staged", OPEN, "re-run with --package <dir> once the cut exists")

    rep.print()

    n = rep.failures
    print()
    if n == 0:
        print(f"\033[32m{args.phase.upper()} PHASE READY — every owned requirement met, no open blockers\033[0m")
        return 0
    print(f"\033[31m{args.phase.upper()} PHASE NOT READY — {n} item(s) failing or open\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())

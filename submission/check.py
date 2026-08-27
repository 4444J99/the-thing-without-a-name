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
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

HERE = Path(__file__).resolve().parent
REGISTER = HERE / "screendance-2027.yaml"
OPPORTUNITY_CHECKER = HERE.parent / "scripts" / "check-opportunities.py"
RIGHTS_CHECKER = HERE.parent / "scripts" / "rights_contract.py"

PASS, FAIL, OPEN, SKIP = "PASS", "FAIL", "OPEN", "SKIP"
GLYPH = {PASS: "\033[32m ok \033[0m", FAIL: "\033[31mFAIL\033[0m", OPEN: "\033[33mOPEN\033[0m", SKIP: "skip"}
PHASES = ("package", "uploaded", "submitted")
OWNED_SECTIONS = ("requirements", "approvals", "terms")

VIDEO_SUFFIXES = {".mov", ".mp4", ".mxf", ".m4v"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
AUDIO_IDENTITY_HASH_FIELDS = (
    "audio_uses_sha256",
    "score_file_sha256",
    "score_contract_sha256",
    "choreography_file_sha256",
    "choreography_contract_sha256",
    "midi_sha256",
    "adaptation_sha256",
    "toolchain_sha256",
    "mix_sha256",
    "soundfont_sha256",
    "audio_render_receipt_sha256",
    "master_sha256",
)
AUDIO_SOUND_FIELDS = (
    "profile",
    *AUDIO_IDENTITY_HASH_FIELDS,
    "sources",
    "stems",
    "credit",
)


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


def safe_contract_file(root: Path, relative: object, label: str) -> Path:
    """Resolve one regular repository/package file without following links."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"{label} has no safe relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise ValueError(f"{label} escapes its contract root")
    current = root.resolve()
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} traverses a symlink")
    if not current.is_file():
        raise ValueError(f"{label} is missing")
    try:
        current.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} escapes its contract root") from exc
    return current


def read_contract_json(root: Path, relative: object, label: str) -> tuple[dict, Path]:
    path = safe_contract_file(root, relative, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value, path


def competition_audio_profile(spec: dict) -> tuple[dict, str, list[str]]:
    """Load the digest-bound usage manifest selected by the submission register."""
    errors: list[str] = []
    reference = spec.get("usage_contract")
    if not isinstance(reference, dict) or set(reference) != {
        "path",
        "sha256",
        "schema",
        "profile",
    }:
        return {}, "", ["submission audio has no typed usage contract"]
    try:
        uses, path = read_contract_json(HERE.parent, reference.get("path"), "audio usage contract")
    except ValueError as exc:
        return {}, "", [str(exc)]
    actual = sha256(path)
    if reference.get("sha256") != actual or not HEX64.fullmatch(str(reference.get("sha256", ""))):
        errors.append("audio usage contract digest is missing or stale")
    if uses.get("schema") != reference.get("schema") or reference.get("schema") != "danse.audio.uses.v1":
        errors.append("audio usage contract schema has drifted")
    profile_id = reference.get("profile")
    if profile_id != uses.get("competition_profile") or profile_id != "competition-classical":
        errors.append("submission does not select the canonical competition-classical profile")
    profiles = uses.get("profiles")
    profile = profiles.get(profile_id) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        errors.append("competition-classical profile is absent")
        profile = {}
    if profile.get("package_eligible") is not True:
        errors.append("competition-classical profile is package-ineligible")
    declared = profile.get("declared_sources")
    required_stems = profile.get("required_stems")
    forbidden = profile.get("forbidden_source_kinds")
    if (
        not isinstance(declared, list)
        or not all(
            isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("kind"), str)
            for row in declared
        )
        or not isinstance(required_stems, list)
        or not all(isinstance(value, str) for value in required_stems)
        or not isinstance(forbidden, list)
        or not all(isinstance(value, str) for value in forbidden)
    ):
        errors.append("competition-classical sources, stems, or forbidden kinds are malformed")
    elif {row["kind"] for row in declared} & set(forbidden):
        errors.append("competition-classical profile admits a forbidden source kind")
    hybrid = spec.get("hybrid_apartment")
    hybrid_profile = profiles.get("hybrid-apartment") if isinstance(profiles, dict) else None
    if (
        not isinstance(hybrid, dict)
        or hybrid.get("profile") != "hybrid-apartment"
        or hybrid.get("package_eligible") is not False
        or not isinstance(hybrid_profile, dict)
        or hybrid_profile.get("package_eligible") is not False
    ):
        errors.append("hybrid-apartment must remain explicitly package-ineligible")
    return profile, actual, errors


def competition_sound_errors(
    sound: object,
    spec: dict,
    profile: dict,
    audio_uses_sha256: str,
) -> list[str]:
    """Validate the full competition sound identity without accepting aliases."""
    if not isinstance(sound, dict):
        return ["manifest has no typed competition sound identity"]
    errors: list[str] = []
    if set(sound) != set(AUDIO_SOUND_FIELDS):
        errors.append("sound identity has fields outside its typed contract")
    if sound.get("profile") != "competition-classical":
        errors.append("sound identity selects a package-ineligible or unknown profile")
    for field in AUDIO_IDENTITY_HASH_FIELDS:
        if not isinstance(sound.get(field), str) or not HEX64.fullmatch(sound[field]):
            errors.append(f"sound identity has no exact {field}")
    if sound.get("audio_uses_sha256") != audio_uses_sha256:
        errors.append("sound identity names a different audio-use contract")
    declared = profile.get("declared_sources") if isinstance(profile, dict) else None
    expected_sources = [row.get("id") for row in declared] if isinstance(declared, list) else []
    if sound.get("sources") != expected_sources:
        errors.append("sound identity does not name the declared competition sources")
    by_id = {
        row.get("id"): row
        for row in declared or []
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if sound.get("midi_sha256") != (by_id.get("delibes-chamber-midi") or {}).get("sha256"):
        errors.append("sound identity names a different adapted MIDI")
    if sound.get("soundfont_sha256") != (by_id.get("musescore-general-sf3") or {}).get("sha256"):
        errors.append("sound identity names a different soundfont")
    stems = sound.get("stems")
    expected_stems = profile.get("required_stems") if isinstance(profile, dict) else None
    if not isinstance(stems, list) or not isinstance(expected_stems, list) or len(stems) != len(expected_stems):
        errors.append("sound identity has no exact stem census")
    else:
        for stem, expected_id in zip(stems, expected_stems, strict=True):
            if (
                not isinstance(stem, dict)
                or set(stem) != {"id", "sha256"}
                or stem.get("id") != expected_id
                or not isinstance(stem.get("sha256"), str)
                or not HEX64.fullmatch(stem["sha256"])
            ):
                errors.append("sound identity has a malformed or reordered stem")
                break
    if sound.get("credit") != spec.get("credit"):
        errors.append("sound identity does not carry the exact approved Delibes credit")
    return errors


def copied_score_receipt(root: Path, manifest: dict, spec: dict) -> tuple[dict, list[str]]:
    """Resolve the one copied v2 score receipt through production.json."""
    errors: list[str] = []
    reference = manifest.get("production")
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        return {}, ["manifest has no exact production receipt reference"]
    if reference.get("path") != spec.get("production_receipt"):
        errors.append("manifest names a noncanonical production receipt")
    try:
        production, production_path = read_contract_json(
            root,
            reference.get("path"),
            "package production receipt",
        )
    except ValueError as exc:
        return {}, [*errors, str(exc)]
    if reference.get("sha256") != sha256(production_path):
        errors.append("package production receipt digest is stale")
    repository_head = manifest.get("repository_head")
    if not isinstance(repository_head, str) or not GIT_OID.fullmatch(repository_head):
        errors.append("package manifest has no exact repository head")
    if production.get("repository_head") != repository_head:
        errors.append("package production receipt names a different repository head")
    if production.get("sound") != manifest.get("sound"):
        errors.append("package production receipt does not equal manifest.sound")
    producers = production.get("producers")
    score_rows = [
        row
        for row in producers or []
        if isinstance(row, dict) and row.get("kind") == "score"
    ]
    if not isinstance(producers, list) or len(score_rows) != 1:
        return {}, [*errors, "production receipt does not name exactly one score producer"]
    receipt_reference = score_rows[0].get("receipt")
    if not isinstance(receipt_reference, dict) or set(receipt_reference) != {"path", "sha256"}:
        return {}, [*errors, "score producer has no exact copied receipt"]
    relative = receipt_reference.get("path")
    if not isinstance(relative, str) or not relative.startswith("provenance/producer-receipts/"):
        errors.append("score producer receipt is outside its package boundary")
    try:
        receipt, receipt_path = read_contract_json(root, relative, "copied score receipt")
    except ValueError as exc:
        return {}, [*errors, str(exc)]
    if receipt_reference.get("sha256") != sha256(receipt_path):
        errors.append("copied score receipt digest is stale")
    return receipt, errors


def durable_audio_render_receipt_errors(
    root: Path,
    manifest: dict,
    spec: dict,
    items: dict[str, dict],
) -> list[str]:
    """Authenticate the package copy of the otherwise ignored render receipt."""
    errors: list[str] = []
    relative = spec.get("audio_render_receipt")
    try:
        receipt, path = read_contract_json(root, relative, "packaged audio-render receipt")
    except ValueError as exc:
        return [str(exc)]
    item = items.get(relative) if isinstance(relative, str) else None
    if not isinstance(item, dict):
        return ["audio-render receipt is absent from the manifest"]
    actual = sha256(path)
    if item.get("sha256") != actual:
        errors.append("audio-render receipt manifest digest is stale")
    if item.get("bytes") != path.stat().st_size:
        errors.append("audio-render receipt manifest byte count is stale")
    manifest_sound = manifest.get("sound")
    expected = (
        manifest_sound.get("audio_render_receipt_sha256")
        if isinstance(manifest_sound, dict)
        else None
    )
    if item.get("sha256") != expected:
        errors.append("audio-render receipt does not equal manifest.sound identity")
    if receipt.get("schema") != "danse.audio.render.v1":
        errors.append("packaged audio-render receipt has the wrong schema")
    return errors


# ── register-level checks (no package needed) ──────────────────────────────────


def check_deadline(reg: dict, phase: str, rep: Report, now: datetime | None = None) -> None:
    d = reg["deadline"]
    try:
        zone = ZoneInfo(reg["opportunity_snapshot"]["timezone"])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        rep.add("deadline", "timezone", FAIL, "canonical named timezone is missing or unavailable")
        return
    wall = datetime.fromisoformat(d["hard_wall"])
    now = now or datetime.now(zone)
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
        # The register's wall is an internal filing buffer chosen ahead of both
        # conflicting official statements. It is not a conversion of either one.
        # Report against that operational wall, never against the stated string.
        rep.add("deadline", "hard wall", PASS, f"{days:.1f} days left → {wall:%a %d %b %H:%M %Z}")

    target = datetime.fromisoformat(d["target_file_date"] + "T12:00:00").replace(tzinfo=zone)
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
    owned = [item for section in OWNED_SECTIONS for item in reg.get(section, [])]
    invalid = [item.get("id", "<unnamed>") for item in owned if item.get("phase") not in PHASES]
    rep.add(
        "register",
        "requirement phase ownership",
        PASS if not invalid else FAIL,
        f"{len(owned)} owned requirements and approvals"
        if not invalid
        else f"missing/invalid phase: {', '.join(invalid)}",
    )
    term_errors = []
    for item in reg.get("terms", []):
        name = item.get("id", "<unnamed>")
        source = item.get("source")
        if item.get("status") != "verified" or not item.get("checked") or not (
            isinstance(source, str) and source.startswith("https://")
        ):
            term_errors.append(f"{name}: provenance")
        check_kind = item.get("check")
        values = item.get("values")
        if check_kind == "choice":
            if not (
                isinstance(values, list)
                and len(values) >= 2
                and all(isinstance(value, str) and value for value in values)
                and len(values) == len(set(values))
            ):
                term_errors.append(f"{name}: choices")
        elif check_kind != "manual":
            term_errors.append(f"{name}: check")
    rep.add(
        "register",
        "published term provenance and choice contract",
        PASS if not term_errors else FAIL,
        f"{len(reg.get('terms', []))} source-verified term(s)"
        if not term_errors
        else "; ".join(term_errors),
    )


def check_opportunity_snapshot(register_path: Path, rep: Report) -> None:
    """Bind filing facts to the exact source-verified release snapshot.

    The opportunity checker owns the schema, source census, digest receipt, and
    issue #2/#12 consumer contract. Importing it here keeps those rules in one
    executable home while making every submission phase fail closed on drift.
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "danse_opportunity_checker", OPPORTUNITY_CHECKER
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("checker module could not be loaded")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        snapshot, receipt = checker.validate_all(consumer_path=register_path)
    except Exception as exc:
        rep.add("register", "frozen opportunity snapshot", FAIL, str(exc))
        return
    rep.add(
        "register",
        "frozen opportunity snapshot",
        PASS,
        f"{snapshot['snapshot_id']} · {receipt['snapshot']['sha256'][:16]}… · issue #2 bound / #12 pending",
    )


def check_rights(package: Path, phase: str, rep: Report) -> None:
    """Require the exact redacted issue-16 contract for every staged phase."""
    try:
        spec = importlib.util.spec_from_file_location("danse_rights_checker", RIGHTS_CHECKER)
        if spec is None or spec.loader is None:
            raise RuntimeError("checker module could not be loaded")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        _, receipt = checker.validate_all(phase=phase, package=package)
    except Exception as exc:
        # Exceptions may carry a caller-owned package or machine-local path.
        # The detailed diagnostic remains available from the local rights CLI;
        # the submission report itself is a public-safe receipt surface.
        rep.add(
            "rights",
            "redacted exact-manifest contract",
            FAIL,
            f"rights validation failed ({type(exc).__name__}); run scripts/check-rights.py locally",
        )
        return
    blockers = receipt["blockers"]
    detail = (
        f"{receipt['inventory']['assets']} assets · register {receipt['register']['sha256'][:16]}…"
        if not blockers
        else f"{len(blockers)} blocker(s): " + "; ".join(blockers[:3])
    )
    rep.add("rights", "redacted exact-manifest contract", PASS if not blockers else FAIL, detail)


def check_attestations(reg: dict, root: Path, phase: str, rep: Report) -> None:
    path = root / "attest.yaml"
    attested = yaml.safe_load(path.read_text()) if path.exists() else {}
    attested = attested or {}
    selected = PHASES.index(phase)
    for req in [item for section in OWNED_SECTIONS for item in reg.get(section, [])]:
        owner = req.get("phase")
        check_kind = req.get("check")
        if check_kind not in ("manual", "choice") or owner not in PHASES or PHASES.index(owner) > selected:
            continue
        value = attested.get(req["id"])
        if check_kind == "choice" and value in req.get("values", []):
            rep.add(f"attested through {phase}", req["id"], PASS, f"{req['rule']} — {value}")
        elif check_kind == "choice":
            choices = ", ".join(req.get("values", []))
            rep.add(
                f"attested through {phase}",
                req["id"],
                FAIL,
                f"choose exactly one of [{choices}] in attest.yaml — {req['rule']}",
            )
        elif value is True:
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
    duration_matches = (
        isinstance(expected_seconds, (int, float))
        and not isinstance(expected_seconds, bool)
        and fps > 0
        and abs(secs - expected_seconds) * fps <= 2
    )
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
    manifest = read_manifest(root)
    item = manifest_items(root).get(path.name) or {}
    actual_digest = sha256(path)
    digest_matches = item.get("sha256") == actual_digest
    rep.add(
        "package",
        "screener bytes match delivery manifest",
        PASS if digest_matches else FAIL,
        f"{actual_digest[:16]}…" + ("" if digest_matches else " — missing or stale manifest digest"),
    )
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
    expected_seconds = manifest.get("duration")
    duration_matches = isinstance(expected_seconds, (int, float)) and abs(info["seconds"] - expected_seconds) <= 0.1
    rep.add(
        "package",
        "screener is one whole manifested passage",
        PASS if duration_matches else FAIL,
        f"{info['seconds']:.3f}s staged vs {expected_seconds!r}s manifested",
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

    manifested = manifest_items(root)
    stale = [p.name for p in named if (manifested.get(f"stills/{p.name}") or {}).get("sha256") != sha256(p)]
    rep.add(
        "package",
        "stills bytes match delivery manifest",
        FAIL if stale else PASS,
        "; ".join(stale[:4]) if stale else f"{len(named)} seed still receipt(s) match",
    )

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
    exists = (
        root.is_dir()
        and not root.is_symlink()
        and path.parent.is_dir()
        and not path.parent.is_symlink()
        and path.is_file()
        and not path.is_symlink()
    )
    rep.add(
        "package",
        "unaltered 2017 photograph",
        PASS if exists else FAIL,
        f"stills/{spec['filename']}" + ("" if exists else " — missing"),
    )
    if not exists:
        return
    item = manifest_items(root).get(f"stills/{spec['filename']}") or {}
    actual = sha256(path)
    registered = spec.get("source_sha256")
    copied = (
        isinstance(registered, str)
        and bool(re.fullmatch(r"[0-9a-fA-F]{64}", registered))
        and actual == registered.lower()
        and item.get("source") == spec["source_filename"]
        and item.get("copy_mode") == spec["copy_mode"]
        and item.get("sha256") == actual
        and item.get("source_sha256") == registered.lower()
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
    measured = loudness(master) if master else None
    if master is None:
        rep.add("audio", "loudness", OPEN, "master audio artifact not staged")
    elif measured is None:
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
    profile, audio_uses_digest, profile_errors = competition_audio_profile(spec)
    expected_sources = [
        row.get("id")
        for row in profile.get("declared_sources", [])
        if isinstance(row, dict)
    ]
    rep.add(
        "audio",
        "package-eligible competition-classical usage profile",
        FAIL if profile_errors else PASS,
        "; ".join(profile_errors)
        if profile_errors
        else f"{len(expected_sources)} declared sources · {len(profile.get('required_stems', []))} required stems",
    )

    manifest_sound = manifest.get("sound")
    identity_errors = competition_sound_errors(
        manifest_sound,
        spec,
        profile,
        audio_uses_digest,
    )
    items = manifest_items(root)
    audio_paths = [
        path
        for stem in ("master", "midnight-moment", "trailer", "screener", "reel")
        if (path := find_one(root, stem))
    ]
    surface_errors: list[str] = list(identity_errors)
    if not audio_paths:
        surface_errors.append("no audio artifact staged")
    for path in audio_paths:
        item = items.get(path.name)
        if not isinstance(item, dict):
            surface_errors.append(f"{path.name} is absent from the manifest")
            continue
        if item.get("sound") != manifest_sound:
            surface_errors.append(f"{path.name} has a different sound identity")
        if item.get("sha256") != sha256(path):
            surface_errors.append(f"{path.name} digest is stale")
        if path.stem == "screener":
            info = probe(path)
            passage_seconds = manifest.get("duration")
            if not (
                info
                and isinstance(passage_seconds, (int, float))
                and not isinstance(passage_seconds, bool)
                and abs(info.get("seconds", -1) - passage_seconds) <= 0.1
            ):
                surface_errors.append(f"{path.name} passage duration is stale")

    score_relative = spec.get("score_source")
    score_item = items.get(score_relative) if isinstance(score_relative, str) else None
    try:
        score_path = safe_contract_file(root, score_relative, "manifested score source")
    except ValueError as exc:
        score_path = None
        surface_errors.append(str(exc))
    if not isinstance(score_item, dict):
        surface_errors.append("score source is absent from the manifest")
    else:
        if score_item.get("sound") != manifest_sound:
            surface_errors.append("score source has a different sound identity")
        if score_path is not None and score_item.get("sha256") != sha256(score_path):
            surface_errors.append("score source manifest digest is stale")
        if isinstance(manifest_sound, dict) and (
            score_item.get("sha256") != manifest_sound.get("master_sha256")
        ):
            surface_errors.append("score source does not equal the rendered audio master")
    rep.add(
        "audio",
        "identical timed-audio sound identity",
        FAIL if surface_errors else PASS,
        "; ".join(surface_errors[:6])
        if surface_errors
        else f"{len(audio_paths) + 1} timed artifact(s) share one full identity",
    )

    audio_render_errors = durable_audio_render_receipt_errors(
        root,
        manifest,
        spec,
        items,
    )
    rep.add(
        "audio",
        "durable audio-render receipt identity",
        FAIL if audio_render_errors else PASS,
        "; ".join(audio_render_errors[:6])
        if audio_render_errors
        else f"{spec.get('audio_render_receipt')} · exact manifested bytes",
    )

    receipt, receipt_errors = copied_score_receipt(root, manifest, spec)
    expected_receipt_fields = {
        "schema",
        "sha256",
        "t0",
        "t1",
        "duration",
        *AUDIO_SOUND_FIELDS,
    }
    if set(receipt) != expected_receipt_fields:
        receipt_errors.append("copied score receipt has fields outside its v2 contract")
    if receipt.get("schema") != spec.get("score_receipt_schema"):
        receipt_errors.append("copied score receipt has the wrong schema")
    receipt_sound = {field: receipt.get(field) for field in AUDIO_SOUND_FIELDS}
    if receipt_sound != manifest_sound:
        receipt_errors.append("copied score receipt does not equal manifest.sound")
    sound_master_sha256 = (
        manifest_sound.get("master_sha256") if isinstance(manifest_sound, dict) else None
    )
    if not isinstance(sound_master_sha256, str) or len(sound_master_sha256) != 64:
        receipt_errors.append("package manifest sound has no valid master_sha256")
    if receipt.get("sha256") != sound_master_sha256:
        receipt_errors.append("copied score receipt does not bind the rendered master WAV")
    for field in ("t0", "t1", "duration"):
        if receipt.get(field) != manifest.get(field):
            receipt_errors.append(f"copied score receipt has a different {field}")
    rep.add(
        "audio",
        "copied score receipt v2 identity",
        FAIL if receipt_errors else PASS,
        "; ".join(receipt_errors[:6])
        if receipt_errors
        else f"{spec.get('score_receipt_schema')} · master {sound_master_sha256[:16]}…",
    )


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
    check_opportunity_snapshot(args.register, rep)
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
            check_rights(root, args.phase, rep)
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

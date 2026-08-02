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
import shutil
import subprocess
import sys
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
from bank_contract import audit_bank  # noqa: E402

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


def registered_origin() -> Path:
    """The submission register is the sole owner of the source photograph."""
    register = yaml.safe_load(REGISTER.read_text()) or {}
    spec = (register.get("package") or {}).get("origin_still") or {}
    filename = spec.get("source_filename")
    if not filename:
        raise SystemExit(f"{REGISTER} does not declare package.origin_still.source_filename")
    if spec.get("copy_mode") != "byte-identical":
        raise SystemExit(f"{REGISTER} must declare package.origin_still.copy_mode: byte-identical")
    return RAW / filename


def registered_audio_sources() -> list[str]:
    """The only recordings a delivery score may claim, from the register."""
    register = yaml.safe_load(REGISTER.read_text()) or {}
    return list((((register.get("package") or {}).get("audio") or {}).get("source_recordings") or []))


def bank_provenance() -> dict | None:
    """Current usable grain-bank identity, bound to the registered sources."""
    audit = audit_bank(BANK, registered_audio_sources())
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
    sources = data.get("sources") or []
    fingerprint = data.get("bank_fingerprint")
    try:
        valid = (
            isinstance(data, dict)
            and data.get("schema") == "danse.score.receipt.v1"
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
    return {"bank_fingerprint": fingerprint, "sources": sources}


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


def package_provenance_matches(package: Path, span: dict) -> bool:
    manifest = package / "manifest.json"
    if not manifest.is_file():
        return not recognized_package_media(package)
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    try:
        return (
            data.get("passage_seed") == hexseed(span["seed"])
            and data.get("passage") == span["passage"]
            and abs(float(data.get("t0", -1)) - span["t0"]) < 1e-9
            and abs(float(data.get("t1", -1)) - span["t1"]) < 1e-9
            and abs(float(data.get("duration", -1)) - span["duration"]) < 1e-3
        )
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
    span: dict,
    only: set[str],
    force: set[str],
    tier: str,
    render_root: Path,
    package: Path,
    origin: Path | None,
    start: float = 0.0,
) -> int:
    """Validate a delivery invocation without creating a directory or rendering."""
    rows: list[tuple[bool, str, str]] = []

    def add(ok: bool, name: str, detail: str) -> None:
        rows.append((ok, name, detail))

    add(program.get("schema") == "danse.program.v2", "program", str(program.get("schema")))
    add(span["duration"] > 0, "capture span", f"{span['duration']:.3f}s from {span['t0']:.3f}s")
    add(shutil.which("node") is not None, "node", shutil.which("node") or "missing")
    add(
        package_provenance_matches(package, span),
        "package passage provenance",
        str(package / "manifest.json"),
    )

    work = pending(program, only, force, package)
    span_names = sorted(work["derived"] | ({"reel"} if work["reel"] else set()))
    for name in span_names:
        error = capture_span_error(name, span, start)
        add(error is None, f"{name} fits selected passage", error or f"within {span['duration']:.3f}s passage")
    need_picture = work["master"] or bool(work["derived"])
    need_score = need_picture or work["reel"]
    need_renderer = work["reel"] or work["stills"]

    picture = render_root / "passage-default.mov"
    picture_info = probe(picture)
    cap = captures(program)["passage"]
    picture_ready = bool(
        picture_info
        and abs(picture_info.get("seconds", 0) * cap.get("fps", 30) - span["duration"] * cap.get("fps", 30)) < 2
        and not is_forced(force, "master")
    )
    need_renderer = need_renderer or (need_picture and not picture_ready)

    score = render_root / "passage-score.wav"
    score_info = probe(score)
    score_ready = bool(
        score_info
        and abs(score_info.get("seconds", 0) - span["duration"]) < 0.1
        and score_provenance(score, span)
        and not is_forced(force, "master")
    )
    need_bank = need_score and not score_ready

    if need_picture or need_score or work["reel"] or work["stills"]:
        for command in ("ffmpeg", "ffprobe"):
            add(shutil.which(command) is not None, command, shutil.which(command) or "missing")

    if need_renderer:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        add(importlib.util.find_spec("playwright") is not None, "Playwright", "Python module")
        add(chrome.is_file(), "Google Chrome", str(chrome))
        local = DANSE / "corpus" / "manifest.local.json"
        local_data = json.loads(local.read_text()) if local.is_file() else {}
        tier_spec = (local_data.get("tiers") or {}).get(tier)
        add(bool(tier_spec), f"corpus tier {tier}", str(local))
        if tier_spec:
            ids = [frame["id"] for frame in json.loads((DANSE / "corpus" / "manifest.json").read_text())["frames"]]
            missing = [
                fid
                for fid in ids
                if not (DANSE / "corpus" / "plates" / tier / f"{fid}.webp").is_file()
                or not (DANSE / "corpus" / "mattes" / tier / f"{fid}.webp").is_file()
            ]
            add(not missing, "film source denominator", f"{len(ids) - len(missing)}/{len(ids)} plate+matte pairs")

    if work["stills"]:
        add(importlib.util.find_spec("PIL") is not None, "Python module Pillow", "package still dependency")

    if need_bank:
        for module in ("numpy", "scipy"):
            add(importlib.util.find_spec(module) is not None, f"Python module {module}", "score dependency")
        declared = registered_audio_sources()
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
        add(origin is not None and origin.is_file(), "unaltered origin photograph", str(origin or "unresolved"))
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
    if not force:
        got = probe_required(dest) if dest.is_file() else None
        if got and abs(got["seconds"] * fps - want) < 2:
            print(f"  passage picture · kept · {got['width']}×{got['height']} @{got['fps']} · {got['seconds']:.1f}s")
            return dest
    print("  passage picture · rendering (this is the long one)")
    done = subprocess.run(
        # fmt: off
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
            "--resume",
            "--quiet",
            "--out",
            str(OUT),
        ],
        # fmt: on
        check=False,
    )
    if done.returncode != 0 or not dest.is_file():
        raise SystemExit("the passage picture would not render")
    return dest


def passage_sound(force: bool, start: float = 0.0) -> tuple[Path, dict]:
    """One score for the passage recording. Every derived capture is cut from it."""
    dest = OUT / "passage-score.wav"
    span = query_capture_span("passage", start=start)
    if not force:
        got = probe_required(dest) if dest.is_file() else None
        provenance = score_provenance(dest, span) if got else None
        if got and provenance and abs(got["seconds"] - span["duration"]) < 0.1:
            print(f"  passage score · kept · {got['seconds']:.1f}s")
            return dest, provenance
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
    return dest, provenance


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
    stem = OUT / "reel-default"
    for junk in OUT.glob("reel-default*"):
        junk.unlink(missing_ok=True)
    done = subprocess.run(
        # fmt: off
        [
            sys.executable,
            str(RENDER),
            "--capture",
            "reel",
            "--start",
            str(start),
            "--tier",
            tier,
            "--codec",
            "h264",
            "--quiet",
            "--out",
            str(OUT),
        ],
        # fmt: on
        check=False,
    )
    picture = stem.with_suffix(".mp4")
    if done.returncode != 0 or not picture.is_file():
        raise SystemExit("the reel would not render")
    tmp_a = OUT / ".reel-a.wav"
    cut_audio(sound, rel_t0, seconds, tmp_a)
    mux(picture, tmp_a, dest, "aac")
    tmp_a.unlink(missing_ok=True)
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
            made.append(dest)
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


def deliver_origin(origin: Path, force: bool) -> Path | None:
    dest = PACKAGE / "stills" / "origin-2017.jpg"
    if dest.is_file() and not force:
        return dest
    if not origin.is_file():
        print(f"  origin-2017.jpg · MISSING SOURCE at {origin}")
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  origin-2017.jpg · byte-identical copy of {origin.name}")
    shutil.copy2(origin, dest)
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
    for item in requirements:
        lines.append(f"#   {item['id']:<30} [{item.get('phase', 'UNOWNED')}] {item['rule']}")
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
    span = query_capture_span("passage", start=args.start)
    origin = registered_origin() if "origin" in only else None
    render_root = capture_root(args.out, span, span["t0"])
    package = args.package or (args.out / "package")
    if args.preflight:
        return preflight(program, span, only, force, args.tier, render_root, package, origin, args.start)
    if not package_provenance_matches(package, span):
        raise SystemExit(f"{package}/manifest.json belongs to a different passage; choose a fresh --package root")

    work = pending(program, only, force, package)
    for name in sorted(work["derived"] | ({"reel"} if work["reel"] else set())):
        error = capture_span_error(name, span, args.start)
        if error:
            raise SystemExit(error)
    media_pending = work["master"] or work["derived"] or work["reel"] or work["stills"]
    if media_pending and shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is required for media delivery; run deliver.py --preflight")

    OUT = render_root
    PACKAGE = package
    OUT.mkdir(parents=True, exist_ok=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)

    print(
        f"{program['title']} · seed {hexseed(program['seed'])} · passage seed {hexseed(span['seed'])} · "
        f"{span['duration']:.1f}s (start at {args.start:.1f}s)\n"
    )

    need_picture = work["master"] or bool(work["derived"])
    need_sound = need_picture or work["reel"]
    score_forced = "master" in force
    picture = passage_picture(program, args.tier, score_forced, start=args.start) if need_picture else None
    sound, sound_provenance = passage_sound(score_forced, start=args.start) if need_sound else (None, None)
    made: list[Path] = []

    if "master" in only:
        made.append(deliver_passage(picture, sound, score_forced) if work["master"] else PACKAGE / "master.mov")
    if "derived" in only:
        for name, spec in DERIVED.items():
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
                if name in work["derived"]
                else PACKAGE / f"{name}{spec['suffix']}"
            )
    if "reel" in only:
        made.append(
            deliver_reel(program, sound, args.tier, score_forced or "reel" in force, start=args.start)
            if work["reel"]
            else PACKAGE / "reel.mp4"
        )
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
        "passage_seed": hexseed(span["seed"]),
        "passage": span["passage"],
        "start": args.start,
        "t0": span["t0"],
        "t1": span["t1"],
        "duration": span["duration"],
        "items": [],
    }
    for path in made:
        if not path.is_file():
            continue
        info = probe(path) or {}
        size = path.stat().st_size
        name = str(path.relative_to(PACKAGE))
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
                "source_sha256": digest(origin),
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

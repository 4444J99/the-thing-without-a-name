#!/usr/bin/env python3
"""Render deterministic Delibes stems/master and emit a digest-bound receipt."""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import platform
import re
import struct
import subprocess
import sys
import tempfile
import wave
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "music"
sys.path.insert(0, str(MUSIC))

from adapt_delibes import MidiFile, parse_midi, write_track  # noqa: E402
from music_score import canonical_sha256, load_score  # noqa: E402

DEFAULT_SCORE = MUSIC / "score.json"
DEFAULT_MIDI = MUSIC / "delibes-screendance-suite.mid"
DEFAULT_ADAPTATION = MUSIC / "adaptation.json"
DEFAULT_TOOLCHAIN = MUSIC / "audio-toolchain.json"
DEFAULT_MIX = MUSIC / "delibes-mix.json"
DEFAULT_USES = ROOT / "sound" / "audio-uses.json"
DEFAULT_OUTPUT = ROOT / ".work" / "music" / "competition"
DEFAULT_RECEIPT = DEFAULT_OUTPUT / "audio-render.json"
DEFAULT_FLUIDSYNTH = Path("/opt/homebrew/bin/fluidsynth")
DEFAULT_FFMPEG = Path("/opt/homebrew/bin/ffmpeg")
DEFAULT_SOUNDFONT = ROOT / ".work" / "music" / "MuseScore_General.sf3"
CHUNK_FRAMES = 16_384
Q16 = 1 << 16
MIN_AUDIBLE_PEAK = 32
MIN_AUDIBLE_RMS = 1.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def load_json(path: Path, schema: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise ValueError(f"{path}: expected {schema}")
    return value


def require_hash(reference: dict[str, Any], label: str) -> Path:
    relative = reference.get("path")
    declared = reference.get("sha256")
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"{label}.path must be a canonical POSIX repository-relative path")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or posix.as_posix() != relative or any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError(f"{label}.path must be a canonical POSIX repository-relative path")
    if not isinstance(declared, str) or len(declared) != 64:
        raise ValueError(f"{label}.sha256 must be a SHA-256 digest")
    path = ROOT.joinpath(*posix.parts)
    if not path.exists():
        raise ValueError(f"{label}: missing {relative}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label}.path must resolve inside the repository") from exc
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label}.path must be a non-symlink regular file")
    actual = sha256(path)
    if actual != declared:
        raise ValueError(f"{label}: declares {declared}, actual {actual}")
    return path


def contract_identity(value: dict[str, Any], label: str) -> str:
    identity = value.get("identity")
    if not isinstance(identity, dict):
        raise ValueError(f"{label} identity must be a mapping")
    declared = identity.get("contract_sha256")
    if not isinstance(declared, str) or len(declared) != 64:
        raise ValueError(f"{label} has no contract_sha256 identity")
    source = {
        **value,
        "identity": {key: row for key, row in identity.items() if key != "contract_sha256"},
    }
    actual = canonical_sha256(source)
    if actual != declared:
        raise ValueError(f"{label} contract_sha256 does not match its content")
    return declared


def validate_inputs(args: argparse.Namespace) -> dict[str, Any]:
    score = load_score(args.score)
    if score.get("release_status") == "fixture-only" or score.get("time", {}).get("passage_mapping") != "native-tempo":
        raise ValueError("competition audio requires a non-fixture native-tempo score")
    choreography = load_json(args.choreography, "danse.choreography.v1")
    contract_identity(choreography, "choreography")
    adaptation = load_json(args.adaptation, "danse.music.adaptation.v1")
    toolchain = load_json(args.toolchain, "danse.audio.toolchain.v1")
    mix = load_json(args.mix, "danse.audio.mix.v1")
    uses = load_json(args.audio_uses, "danse.audio.uses.v1")
    profile_id = args.profile or uses.get("competition_profile")
    profiles = uses.get("profiles")
    profile = profiles.get(profile_id) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict) or profile.get("package_eligible") is not True:
        raise ValueError(f"audio use profile {profile_id!r} is not package-eligible")

    for name, expected_path, expected_digest in (
        ("toolchain.midi", args.midi, sha256(args.midi)),
        ("toolchain.adaptation", args.adaptation, sha256(args.adaptation)),
        ("toolchain.mix", args.mix, sha256(args.mix)),
        ("toolchain.renderer", Path(__file__), sha256(Path(__file__))),
    ):
        reference = toolchain.get(name.split(".")[1])
        if not isinstance(reference, dict):
            raise ValueError(f"{name} is required")
        path = require_hash(reference, name)
        if path.resolve() != expected_path.resolve() or reference["sha256"] != expected_digest:
            raise ValueError(f"{name} does not bind the selected input")

    soundfont = toolchain.get("soundfont")
    if not isinstance(soundfont, dict):
        raise ValueError("toolchain.soundfont is required")
    soundfont_path = require_hash(soundfont, "toolchain.soundfont")
    if soundfont_path.resolve() != args.soundfont.resolve():
        raise ValueError("toolchain.soundfont path differs from requested soundfont")
    notice = soundfont.get("license_notice")
    if not isinstance(notice, dict):
        raise ValueError("toolchain.soundfont.license_notice is required")
    require_hash(notice, "toolchain.soundfont.license_notice")

    fluidsynth = toolchain.get("fluidsynth")
    if not isinstance(fluidsynth, dict) or fluidsynth.get("version") != "2.6.0":
        raise ValueError("toolchain must pin FluidSynth 2.6.0")
    if sha256(args.fluidsynth) != fluidsynth.get("executable_sha256"):
        raise ValueError("FluidSynth executable hash differs from the pinned toolchain")
    version = subprocess.run([str(args.fluidsynth), "--version"], capture_output=True, text=True, check=False)
    if version.returncode != 0 or "2.6.0" not in version.stdout + version.stderr:
        raise ValueError("FluidSynth runtime is not version 2.6.0")

    ffmpeg = toolchain.get("ffmpeg")
    if not isinstance(ffmpeg, dict) or ffmpeg.get("version") != "9.0.1":
        raise ValueError("toolchain must pin ffmpeg 9.0.1")
    if args.ffmpeg.resolve() != Path(ffmpeg.get("executable_path", "")).resolve():
        raise ValueError("ffmpeg path differs from the pinned toolchain")
    if sha256(args.ffmpeg) != ffmpeg.get("executable_sha256"):
        raise ValueError("ffmpeg executable hash differs from the pinned toolchain")
    ffmpeg_version = subprocess.run([str(args.ffmpeg), "-version"], capture_output=True, text=True, check=False)
    if ffmpeg_version.returncode != 0 or not ffmpeg_version.stdout.startswith("ffmpeg version 9.0.1 "):
        raise ValueError("ffmpeg runtime is not version 9.0.1")

    normalization = mix.get("master", {}).get("normalization")
    if not isinstance(normalization, dict) or normalization.get("method") != "ffmpeg-loudnorm-two-pass":
        raise ValueError("competition mix must declare two-pass ffmpeg loudness normalization")
    if ffmpeg.get("settings") != normalization:
        raise ValueError("toolchain ffmpeg settings must equal the mix normalization contract")

    midi_digest = sha256(args.midi)
    if score.get("identity", {}).get("midi_sha256") != midi_digest:
        raise ValueError("score identity does not bind the selected adapted MIDI")
    if adaptation.get("output", {}).get("sha256") != midi_digest:
        raise ValueError("adaptation output does not bind the selected adapted MIDI")
    duration = score.get("time", {}).get("duration_seconds")
    adapted_duration = adaptation.get("output", {}).get("duration_seconds")
    if type(duration) not in (int, float) or abs(float(duration) - float(adapted_duration)) > 1e-6:
        raise ValueError("score duration differs from the native adapted MIDI")

    declared_sources = profile.get("declared_sources")
    if not isinstance(declared_sources, list):
        raise ValueError("audio use profile must declare its sources")
    forbidden = set(profile.get("forbidden_source_kinds") or [])
    for index, source in enumerate(declared_sources):
        if not isinstance(source, dict):
            raise ValueError(f"audio source {index} is malformed")
        if source.get("kind") in forbidden:
            raise ValueError(f"audio source {source.get('id')} is forbidden by the selected profile")
        require_hash(source, f"audio source {source.get('id')}")
        if isinstance(source.get("license_notice"), dict):
            require_hash(source["license_notice"], f"audio source {source.get('id')}.license_notice")

    stems = mix.get("stems")
    required_stems = profile.get("required_stems")
    if not isinstance(stems, list) or [row.get("id") for row in stems] != required_stems:
        raise ValueError("mix stem order must equal the competition audio-use contract")
    return {
        "score": score,
        "choreography": choreography,
        "adaptation": adaptation,
        "toolchain": toolchain,
        "mix": mix,
        "uses": uses,
        "profile_id": profile_id,
        "soundfont": soundfont,
        "fluidsynth": fluidsynth,
        "ffmpeg": ffmpeg,
    }


def solo_midi(parsed: MidiFile, track_index: int) -> bytes:
    if not 0 < track_index < len(parsed.tracks):
        raise ValueError(f"invalid solo MIDI track {track_index}")
    end_tick = max(parsed.ends)
    header = b"MThd" + struct.pack(">IHHH", 6, 1, 2, parsed.division)
    return header + write_track(list(parsed.tracks[0]), end_tick) + write_track(list(parsed.tracks[track_index]), end_tick)


def normalize_wav(source: Path, target: Path, *, sample_rate: int, frames: int) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    peak = 0
    square_sum = 0
    sample_count = 0
    with wave.open(str(source), "rb") as reader:
        if reader.getframerate() != sample_rate or reader.getnchannels() != 2 or reader.getsampwidth() != 2:
            raise ValueError(f"FluidSynth produced an unexpected WAV format for {source}")
        with wave.open(str(target), "wb") as writer:
            writer.setnchannels(2)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            remaining = frames
            while remaining:
                count = min(CHUNK_FRAMES, remaining)
                raw = reader.readframes(count)
                actual = len(raw) // 4
                if actual < count:
                    raw += b"\0" * ((count - actual) * 4)
                samples = array.array("h")
                samples.frombytes(raw)
                if sys.byteorder != "little":
                    samples.byteswap()
                for value in samples:
                    absolute = abs(value)
                    peak = max(peak, absolute)
                    square_sum += value * value
                sample_count += len(samples)
                writer.writeframesraw(raw)
                remaining -= count
    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    return {
        "peak_sample": peak,
        "rms_sample": round(rms, 6),
        "non_silent": peak >= MIN_AUDIBLE_PEAK and rms >= MIN_AUDIBLE_RMS,
    }


def inspect_wav(path: Path, *, sample_rate: int, frames: int) -> dict[str, Any]:
    peak = 0
    square_sum = 0
    sample_count = 0
    with wave.open(str(path), "rb") as reader:
        if reader.getparams()[:4] != (2, 2, sample_rate, frames):
            raise ValueError(f"normalized master does not match the fixed audio format: {path}")
        while sample_count < frames * 2:
            samples = array.array("h")
            samples.frombytes(reader.readframes(CHUNK_FRAMES))
            if sys.byteorder != "little":
                samples.byteswap()
            if not samples:
                break
            for value in samples:
                peak = max(peak, abs(value))
                square_sum += value * value
            sample_count += len(samples)
    if sample_count != frames * 2:
        raise ValueError(f"normalized master ended before {frames} frames")
    rms = math.sqrt(square_sum / sample_count)
    return {
        "path": display_path(path),
        "sha256": sha256(path),
        "frames": frames,
        "sample_rate": sample_rate,
        "channels": 2,
        "duration_seconds": round(frames / sample_rate, 9),
        "peak_sample": peak,
        "rms_sample": round(rms, 6),
        "non_silent": peak >= MIN_AUDIBLE_PEAK and rms >= MIN_AUDIBLE_RMS,
    }


def loudnorm_filter(settings: dict[str, Any], measured: dict[str, Any] | None = None) -> str:
    target = (
        f"loudnorm=I={float(settings['target_lufs']):g}"
        f":TP={float(settings['target_true_peak_dbtp']):g}"
        f":LRA={float(settings['target_lra_lu']):g}"
    )
    if measured is not None:
        linear = "true" if settings.get("linear") is True else "false"
        target += (
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            f":linear={linear}"
        )
    return target + ":print_format=json"


def loudnorm_block(ffmpeg: Path, source: Path, settings: dict[str, Any]) -> dict[str, Any]:
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-nostats",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-af",
        loudnorm_filter(settings),
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
    blocks = re.findall(r"\{\s*\"input_i\".*?\}", result.stderr, flags=re.DOTALL)
    if result.returncode != 0 or not blocks:
        raise ValueError(f"ffmpeg loudnorm analysis failed: {result.stderr[-2000:]}")
    try:
        block = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("ffmpeg loudnorm returned malformed JSON") from exc
    required = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not required <= set(block):
        raise ValueError("ffmpeg loudnorm measurement is incomplete")
    return block


def loudness_measurement(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "integrated_lufs": float(block["input_i"]),
        "true_peak_dbtp": float(block["input_tp"]),
        "lra_lu": float(block["input_lra"]),
        "threshold_lufs": float(block["input_thresh"]),
    }


def loudness_gate(measured: dict[str, Any], settings: dict[str, Any]) -> tuple[bool, bool]:
    loudness_ok = abs(float(measured["integrated_lufs"]) - float(settings["target_lufs"])) <= float(
        settings["tolerance_lu"]
    )
    peak_ok = float(measured["true_peak_dbtp"]) <= float(settings["max_true_peak_dbtp"])
    return loudness_ok, peak_ok


def normalize_master(
    source: Path,
    target: Path,
    *,
    ffmpeg: Path,
    ffmpeg_contract: dict[str, Any],
    settings: dict[str, Any],
    sample_rate: int,
    frames: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    first_block = loudnorm_block(ffmpeg, source, settings)
    application_filter = loudnorm_filter(settings, first_block)
    pending = target.with_name(target.stem + ".pending.wav")
    pending.unlink(missing_ok=True)
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-nostats",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-af",
        application_filter,
        "-ar",
        str(sample_rate),
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        str(pending),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
    if result.returncode != 0 or not pending.is_file():
        raise ValueError(f"ffmpeg two-pass loudnorm application failed: {result.stderr[-2000:]}")
    stats = inspect_wav(pending, sample_rate=sample_rate, frames=frames)
    final_block = loudnorm_block(ffmpeg, pending, settings)
    final_measurement = loudness_measurement(final_block)
    loudness_ok, peak_ok = loudness_gate(final_measurement, settings)
    if not loudness_ok or not peak_ok:
        pending.unlink(missing_ok=True)
        raise ValueError(
            "normalized master misses delivery loudness target: "
            f"{final_measurement['integrated_lufs']:.2f} LUFS, "
            f"{final_measurement['true_peak_dbtp']:.2f} dBTP"
        )
    pending.replace(target)
    stats["path"] = display_path(target)
    stats["sha256"] = sha256(target)
    normalization = {
        "schema": "danse.audio.normalization.v1",
        "method": settings["method"],
        "limiter": "ffmpeg-loudnorm-dynamic-true-peak",
        "ffmpeg": {
            "version": ffmpeg_contract["version"],
            "executable_sha256": ffmpeg_contract["executable_sha256"],
        },
        "targets": {
            "integrated_lufs": float(settings["target_lufs"]),
            "tolerance_lu": float(settings["tolerance_lu"]),
            "target_true_peak_dbtp": float(settings["target_true_peak_dbtp"]),
            "max_true_peak_dbtp": float(settings["max_true_peak_dbtp"]),
            "lra_lu": float(settings["target_lra_lu"]),
        },
        "first_pass": loudness_measurement(first_block),
        "application_filter": application_filter,
        "normalization_type": "dynamic",
        "output": final_measurement,
    }
    return stats, normalization


def render_stems(
    directory: Path,
    *,
    midi: Path,
    mix: dict[str, Any],
    fluidsynth: Path,
    soundfont: Path,
    frames: int,
    sample_rate: int,
    gain: str,
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    parsed = parse_midi(midi)
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    paths: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="danse-fluid-") as temporary:
        scratch = Path(temporary)
        for stem in mix["stems"]:
            stem_id = stem["id"]
            solo = scratch / f"{stem_id}.mid"
            raw = scratch / f"{stem_id}-raw.wav"
            solo.write_bytes(solo_midi(parsed, int(stem["midi_track"])))
            command = [
                str(fluidsynth),
                "-ni",
                "-q",
                "-F",
                str(raw),
                "-T",
                "wav",
                "-O",
                "s16",
                "-r",
                str(sample_rate),
                "-g",
                gain,
                "-R",
                "0",
                "-C",
                "0",
                "-o",
                "synth.cpu-cores=1",
                "-o",
                "synth.lock-memory=0",
                "-o",
                "synth.midi-bank-select=gm",
                str(soundfont),
                str(solo),
            ]
            result = subprocess.run(command, capture_output=True, check=False)
            if result.returncode != 0 or not raw.is_file():
                diagnostic = (result.stdout + result.stderr).decode("utf-8", errors="replace")[-2000:]
                raise ValueError(f"FluidSynth failed for {stem_id}: {diagnostic}")
            target = directory / f"{stem_id}.wav"
            stats = normalize_wav(raw, target, sample_rate=sample_rate, frames=frames)
            rows.append(
                {
                    "id": stem_id,
                    "path": display_path(target),
                    "sha256": sha256(target),
                    "frames": frames,
                    "sample_rate": sample_rate,
                    "channels": 2,
                    "duration_seconds": round(frames / sample_rate, 9),
                    **stats,
                }
            )
            paths[stem_id] = target
    return rows, paths


def rounded_shift(value: int, bits: int) -> int:
    half = 1 << (bits - 1)
    return (value + half) >> bits if value >= 0 else -((-value + half) >> bits)


def mix_stems(
    paths: dict[str, Path],
    target: Path,
    *,
    mix: dict[str, Any],
    frames: int,
    sample_rate: int,
) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    readers: dict[str, wave.Wave_read] = {}
    peak = 0
    square_sum = 0
    sample_count = 0
    polyphonic_frames = 0
    try:
        for stem in mix["stems"]:
            reader = wave.open(str(paths[stem["id"]]), "rb")
            if reader.getparams()[:4] != (2, 2, sample_rate, frames):
                raise ValueError(f"stem {stem['id']} does not match the fixed mix format")
            readers[stem["id"]] = reader
        master_gain = int(mix["master"]["gain_q16"])
        ceiling = rounded_shift(32767 * int(mix["master"]["peak_ceiling_q16"]), 16)
        with wave.open(str(target), "wb") as writer:
            writer.setnchannels(2)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            remaining = frames
            while remaining:
                count = min(CHUNK_FRAMES, remaining)
                left = [0] * count
                right = [0] * count
                active = [0] * count
                for stem in mix["stems"]:
                    samples = array.array("h")
                    samples.frombytes(readers[stem["id"]].readframes(count))
                    if sys.byteorder != "little":
                        samples.byteswap()
                    if len(samples) != count * 2:
                        raise ValueError(f"stem {stem['id']} ended before the declared frame count")
                    gain = int(stem["gain_q16"])
                    pan = int(stem["pan_q16"])
                    left_pan = Q16 if pan <= 0 else Q16 - pan
                    right_pan = Q16 if pan >= 0 else Q16 + pan
                    left_factor = gain * left_pan
                    right_factor = gain * right_pan
                    for frame in range(count):
                        left_sample = samples[frame * 2]
                        right_sample = samples[frame * 2 + 1]
                        left[frame] += left_sample * left_factor
                        right[frame] += right_sample * right_factor
                        if max(abs(left_sample), abs(right_sample)) > 32:
                            active[frame] += 1
                output = array.array("h")
                for frame in range(count):
                    lvalue = max(-ceiling, min(ceiling, rounded_shift(left[frame] * master_gain, 48)))
                    rvalue = max(-ceiling, min(ceiling, rounded_shift(right[frame] * master_gain, 48)))
                    output.extend((lvalue, rvalue))
                    peak = max(peak, abs(lvalue), abs(rvalue))
                    square_sum += lvalue * lvalue + rvalue * rvalue
                    sample_count += 2
                    if active[frame] >= 2:
                        polyphonic_frames += 1
                if sys.byteorder != "little":
                    output.byteswap()
                writer.writeframesraw(output.tobytes())
                remaining -= count
    finally:
        for reader in readers.values():
            reader.close()
    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    return {
        "path": display_path(target),
        "sha256": sha256(target),
        "frames": frames,
        "sample_rate": sample_rate,
        "channels": 2,
        "duration_seconds": round(frames / sample_rate, 9),
        "peak_sample": peak,
        "rms_sample": round(rms, 6),
        "non_silent": peak >= MIN_AUDIBLE_PEAK and rms >= MIN_AUDIBLE_RMS,
        "polyphonic_frames": polyphonic_frames,
    }


def render_once(
    directory: Path,
    *,
    args: argparse.Namespace,
    contracts: dict[str, Any],
    frames: int,
    sample_rate: int,
) -> dict[str, Any]:
    stems, paths = render_stems(
        directory / "stems",
        midi=args.midi,
        mix=contracts["mix"],
        fluidsynth=args.fluidsynth,
        soundfont=args.soundfont,
        frames=frames,
        sample_rate=sample_rate,
        gain=str(contracts["fluidsynth"]["settings"]["gain"]),
    )
    pre_normalized = mix_stems(
        paths,
        directory / "delibes-pre-normalized.wav",
        mix=contracts["mix"],
        frames=frames,
        sample_rate=sample_rate,
    )
    master, normalization = normalize_master(
        directory / "delibes-pre-normalized.wav",
        directory / "delibes-master.wav",
        ffmpeg=args.ffmpeg,
        ffmpeg_contract=contracts["ffmpeg"],
        settings=contracts["mix"]["master"]["normalization"],
        sample_rate=sample_rate,
        frames=frames,
    )
    master["polyphonic_frames"] = pre_normalized["polyphonic_frames"]
    return {
        "stems": stems,
        "pre_normalized_master": pre_normalized,
        "master": master,
        "normalization": normalization,
    }


def pcm_slice_hash(path: Path, start_frame: int, frames: int) -> str:
    with wave.open(str(path), "rb") as reader:
        reader.setpos(start_frame)
        payload = reader.readframes(frames)
    if len(payload) != frames * 4:
        raise ValueError(f"seek probe exceeds {path}")
    return hashlib.sha256(payload).hexdigest()


def receipt(
    *,
    args: argparse.Namespace,
    contracts: dict[str, Any],
    outputs: dict[str, Any],
    repeat: dict[str, Any] | None,
    frames: int,
    sample_rate: int,
) -> dict[str, Any]:
    score = contracts["score"]
    choreography = contracts["choreography"]
    deterministic = repeat is not None and outputs["master"]["sha256"] == repeat["master"]["sha256"]
    if repeat is not None:
        deterministic = (
            deterministic
            and outputs["pre_normalized_master"]["sha256"] == repeat["pre_normalized_master"]["sha256"]
            and outputs["normalization"] == repeat["normalization"]
            and [row["sha256"] for row in outputs["stems"]]
            == [row["sha256"] for row in repeat["stems"]]
        )
    duration = float(score["time"]["duration_seconds"])
    probe_seconds = [0.0, 72.0, 149.152297, 245.0, 346.5]
    probe_frames = min(sample_rate // 4, frames)
    probes = []
    master_path = ROOT / outputs["master"]["path"]
    repeat_path = ROOT / repeat["master"]["path"] if repeat else None
    seek_safe = repeat_path is not None
    for second in probe_seconds:
        start = min(frames - probe_frames, max(0, round(second * sample_rate)))
        first = pcm_slice_hash(master_path, start, probe_frames)
        second_hash = pcm_slice_hash(repeat_path, start, probe_frames) if repeat_path else None
        equal = first == second_hash
        seek_safe = seek_safe and equal
        probes.append({"start_frame": start, "frames": probe_frames, "sha256": first, "repeat_sha256": second_hash, "equal": equal})
    duration_matches = frames == int(
        (Decimal(str(duration)) * sample_rate).to_integral_value(rounding=ROUND_HALF_UP)
    )
    loudness_ok, peak_ok = loudness_gate(
        outputs["normalization"]["output"], contracts["mix"]["master"]["normalization"]
    )
    receipt_outputs = {
        "stems": outputs["stems"],
        "pre_normalized_master": outputs["pre_normalized_master"],
        "master": outputs["master"],
    }
    return {
        "schema": "danse.audio.render.v1",
        "profile": contracts["profile_id"],
        "inputs": {
            "score": {
                "path": display_path(args.score),
                "sha256": sha256(args.score),
                "contract_sha256": contract_identity(score, "score"),
                "duration_seconds": duration,
            },
            "choreography": {
                "path": display_path(args.choreography),
                "sha256": sha256(args.choreography),
                "contract_sha256": contract_identity(choreography, "choreography"),
            },
            "midi": {"path": display_path(args.midi), "sha256": sha256(args.midi)},
            "adaptation": {"path": display_path(args.adaptation), "sha256": sha256(args.adaptation)},
            "toolchain": {"path": display_path(args.toolchain), "sha256": sha256(args.toolchain)},
            "mix": {"path": display_path(args.mix), "sha256": sha256(args.mix)},
            "audio_uses": {"path": display_path(args.audio_uses), "sha256": sha256(args.audio_uses)},
            "soundfont": {"path": display_path(args.soundfont), "sha256": sha256(args.soundfont)},
            "fluidsynth_executable": {"path": str(args.fluidsynth.resolve()), "sha256": sha256(args.fluidsynth), "version": "2.6.0"},
            "ffmpeg_executable": {"path": str(args.ffmpeg.resolve()), "sha256": sha256(args.ffmpeg), "version": "9.0.1"},
        },
        "outputs": receipt_outputs,
        "normalization": outputs["normalization"],
        "verification": {
            "repeat_master_sha256": repeat["master"]["sha256"] if repeat else None,
            "deterministic": deterministic,
            "non_silent": bool(outputs["master"]["non_silent"]),
            "stems_non_silent": all(bool(row["non_silent"]) for row in outputs["stems"]),
            "polyphonic": outputs["master"]["polyphonic_frames"] > 0,
            "normalization_deterministic": repeat is not None and outputs["normalization"] == repeat["normalization"],
            "loudness_in_target": loudness_ok,
            "true_peak_in_target": peak_ok,
            "duration_matches_score": duration_matches,
            "seek_safe": seek_safe,
            "seek_probes": probes,
        },
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, default=DEFAULT_SCORE)
    parser.add_argument("--choreography", type=Path, required=True)
    parser.add_argument("--midi", type=Path, default=DEFAULT_MIDI)
    parser.add_argument("--adaptation", type=Path, default=DEFAULT_ADAPTATION)
    parser.add_argument("--toolchain", type=Path, default=DEFAULT_TOOLCHAIN)
    parser.add_argument("--mix", type=Path, default=DEFAULT_MIX)
    parser.add_argument("--audio-uses", type=Path, default=DEFAULT_USES)
    parser.add_argument("--profile")
    parser.add_argument("--fluidsynth", type=Path, default=DEFAULT_FLUIDSYNTH)
    parser.add_argument("--ffmpeg", type=Path, default=DEFAULT_FFMPEG)
    parser.add_argument("--soundfont", type=Path, default=DEFAULT_SOUNDFONT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--no-repeat", action="store_true", help="development only; receipt remains fail-closed")
    args = parser.parse_args()
    try:
        contracts = validate_inputs(args)
        sample_rate = int(contracts["mix"]["sample_rate"])
        duration = Decimal(str(contracts["score"]["time"]["duration_seconds"]))
        frames = int((duration * sample_rate).to_integral_value(rounding=ROUND_HALF_UP))
        outputs = render_once(args.out, args=args, contracts=contracts, frames=frames, sample_rate=sample_rate)
        repeat = None
        if not args.no_repeat:
            with tempfile.TemporaryDirectory(prefix="danse-audio-repeat-") as temporary:
                repeat = render_once(Path(temporary), args=args, contracts=contracts, frames=frames, sample_rate=sample_rate)
                document = receipt(
                    args=args,
                    contracts=contracts,
                    outputs=outputs,
                    repeat=repeat,
                    frames=frames,
                    sample_rate=sample_rate,
                )
        else:
            document = receipt(
                args=args,
                contracts=contracts,
                outputs=outputs,
                repeat=None,
                frames=frames,
                sample_rate=sample_rate,
            )
        required_verification = [
            document["verification"]["deterministic"],
            document["verification"]["non_silent"],
            document["verification"]["stems_non_silent"],
            document["verification"]["polyphonic"],
            document["verification"]["normalization_deterministic"],
            document["verification"]["loudness_in_target"],
            document["verification"]["true_peak_in_target"],
            document["verification"]["duration_matches_score"],
            document["verification"]["seek_safe"],
        ]
        if not all(required_verification):
            if not args.no_repeat:
                raise ValueError(f"audio verification failed: {document['verification']}")
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(document, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"ok: {display_path(args.receipt)} ({document['outputs']['master']['sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Strict contracts for the Danse reference installation and physical evidence.

The reference twin is deterministic and tracked. Venue, hardware, calibration,
runtime, wall-plug, and restore evidence is external and must be supplied
explicitly; this module never promotes a simulator result into a physical claim.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SPEC = HERE / "digital-twin.json"
GATES = HERE / "gates.json"
ARCHIVE_DISPOSITION = HERE / "archive-disposition.json"

SHA256 = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
UINT32_MAX = 0xFFFFFFFF
REQUIRED_SOURCE_CONTRACTS = {
    "projector-camera",
    "program",
    "score",
    "room-layout",
    "interaction",
    "projection-probe",
}
REQUIRED_GATE_IDS = {
    "venue-approval",
    "hardware-inventory",
    "projector-calibration",
    "speaker-calibration",
    "visible-plane-cue",
    "runtime-approval",
    "wall-plug-recovery",
    "restore-rehearsal",
}
REQUIRED_ARCHIVE_DECISIONS = {
    "pitch-black-20ft-room": "rejected",
    "central-scrim": "deferred",
    "two-projectors": "ported",
    "no-edge-blending": "ported",
    "dead-center-low-light-camera": "superseded",
    "cdn-mediapipe-landmarks": "superseded",
    "visitor-depth-spread": "ported",
    "visitor-audio-panning": "rejected",
}
FORBIDDEN_HOST_MUTATIONS = {"LaunchAgent", "LaunchDaemon", "cron", "systemd-user"}


class ContractError(ValueError):
    """A deterministic or physical installation contract failed closed."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ContractError(f"duplicate JSON key {key!r}")
        value[key] = child
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path}: root must be an object")
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def installation_contract_sha256(spec: dict[str, Any]) -> str:
    canonical = copy.deepcopy(spec)
    identity = canonical.get("identity")
    if not isinstance(identity, dict):
        raise ContractError("digital twin identity must be an object")
    identity["contract_sha256"] = ""
    return canonical_sha256(canonical)


def _exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ContractError(f"{label} keys drifted; missing={missing}, extra={extra}")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _finite(
    value: Any, label: str, low: float = -math.inf, high: float = math.inf
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ContractError(f"{label} must be finite")
    result = float(value)
    if result < low or result > high:
        raise ContractError(f"{label} must be in [{low}, {high}]")
    return result


def _vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ContractError(f"{label} must contain exactly {length} coordinates")
    return [_finite(child, f"{label}[{index}]") for index, child in enumerate(value)]


def _uint32(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > UINT32_MAX
    ):
        raise ContractError(f"{label} must be a 32-bit unsigned integer")
    return value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise ContractError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} is not a valid timestamp") from exc
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value.strip()


def safe_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ContractError(f"{label} must be a portable relative path")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ContractError(f"{label} must stay below its declared root")
    base = root.resolve(strict=True)
    current = base
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ContractError(f"{label} may not traverse a symlink")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise ContractError(f"{label} is unavailable below its declared root") from exc
    if not resolved.is_file():
        raise ContractError(f"{label} must name a regular file")
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _objects(value: Any, label: str, *, minimum: int = 1) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or not all(isinstance(child, dict) for child in value)
    ):
        raise ContractError(f"{label} must contain at least {minimum} objects")
    return value


def _unique(
    records: list[dict[str, Any]], key: str, label: str
) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for index, record in enumerate(records):
        value = record.get(key)
        try:
            present = value in result
        except TypeError as exc:
            raise ContractError(
                f"{label}[{index}].{key} must be a scalar identity"
            ) from exc
        if present or value in (None, ""):
            raise ContractError(f"{label}[{index}].{key} must be present and unique")
        result[value] = record
    return result


def validate_digital_twin(value: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    _exact_keys(
        value,
        {
            "schema",
            "identity",
            "source_contracts",
            "coordinate_system",
            "projector_camera",
            "surfaces",
            "projection_outputs",
            "synchronization",
            "audio",
            "calibration",
            "hardware_roles",
            "runtime",
            "release",
        },
        "digital twin",
    )
    if value["schema"] != "danse.installation.digital-twin.v1":
        raise ContractError("unknown digital twin schema")
    identity = _exact_keys(
        value["identity"], {"id", "status", "contract_sha256"}, "identity"
    )
    _nonempty(identity["id"], "identity.id")
    if identity["status"] != "reference-simulation":
        raise ContractError(
            "digital twin must remain a reference-simulation before venue evidence"
        )
    digest = _sha256(identity["contract_sha256"], "identity.contract_sha256")
    if digest != installation_contract_sha256(value):
        raise ContractError("digital twin contract_sha256 is stale")

    sources = _objects(value["source_contracts"], "source_contracts")
    by_source = _unique(sources, "id", "source_contracts")
    if set(by_source) != REQUIRED_SOURCE_CONTRACTS:
        raise ContractError("digital twin source contract inventory is incomplete")
    for source_id, source in by_source.items():
        allowed = {
            "id",
            "path",
            "sha256",
            "embedded_schema",
            "embedded_contract_sha256",
        }
        if not set(source) <= allowed or not {"id", "path", "sha256"} <= set(source):
            raise ContractError(f"source_contracts.{source_id} has an unknown shape")
        path = safe_file(root, source["path"], f"source_contracts.{source_id}.path")
        if file_sha256(path) != _sha256(
            source["sha256"], f"source_contracts.{source_id}.sha256"
        ):
            raise ContractError(f"source contract {source_id} bytes drifted")
        if "embedded_schema" in source:
            document = load_json(path)
            if document.get("schema") != source["embedded_schema"]:
                raise ContractError(
                    f"source contract {source_id} embedded schema drifted"
                )
        if "embedded_contract_sha256" in source:
            document = load_json(path)
            embedded = (document.get("identity") or {}).get("contract_sha256")
            if embedded != _sha256(
                source["embedded_contract_sha256"],
                f"source_contracts.{source_id}.embedded_contract_sha256",
            ):
                raise ContractError(
                    f"source contract {source_id} embedded identity drifted"
                )

    coordinates = _exact_keys(
        value["coordinate_system"],
        {
            "units",
            "meters_per_unit",
            "origin",
            "axes",
            "reference_volume_m",
            "venue_dimensions_status",
        },
        "coordinate_system",
    )
    if (
        coordinates["units"] != "normalized-room"
        or coordinates["origin"] != "2017-picture-plane-center"
    ):
        raise ContractError("digital twin coordinate system drifted")
    meters = _finite(
        coordinates["meters_per_unit"], "coordinate_system.meters_per_unit", 0.001, 1000
    )
    axes = _exact_keys(coordinates["axes"], {"x", "y", "z"}, "coordinate_system.axes")
    if axes != {"x": "left-to-right", "y": "floor-to-ceiling", "z": "far-to-near"}:
        raise ContractError("digital twin axes drifted")
    volume = _exact_keys(
        coordinates["reference_volume_m"],
        {"width", "height", "depth"},
        "reference_volume_m",
    )
    if coordinates["venue_dimensions_status"] != "requires-venue-measurement":
        raise ContractError("reference volume may not claim venue measurement")

    camera = _exact_keys(
        value["projector_camera"],
        {
            "source_contract",
            "eye",
            "picture_plane_half_extents",
            "fovy_radians",
            "aspect",
            "near",
            "far",
        },
        "projector_camera",
    )
    if camera["source_contract"] != "projector-camera":
        raise ContractError("projector camera must bind engine/room.js")
    eye = _vector(camera["eye"], 3, "projector_camera.eye")
    half_extents = _vector(
        camera["picture_plane_half_extents"],
        2,
        "projector_camera.picture_plane_half_extents",
    )
    if eye != [0.0, 0.0, 2.4] or half_extents != [1.0, 0.75]:
        raise ContractError(
            "projector camera no longer matches the 2017 room coordinates"
        )
    expected_fovy = 2 * math.atan(half_extents[1] / eye[2])
    if not math.isclose(
        _finite(camera["fovy_radians"], "projector_camera.fovy_radians"),
        expected_fovy,
        abs_tol=1e-15,
    ):
        raise ContractError("projector fovy is not derived from the shared room camera")
    if not math.isclose(
        _finite(camera["aspect"], "projector_camera.aspect"),
        half_extents[0] / half_extents[1],
        abs_tol=1e-15,
    ):
        raise ContractError("projector aspect is not derived from the picture plane")
    near = _finite(camera["near"], "projector_camera.near", 0.001)
    far = _finite(camera["far"], "projector_camera.far", near)
    if far <= near:
        raise ContractError("projector far plane must exceed its near plane")
    if not math.isclose(
        _finite(volume["width"], "reference_volume_m.width"),
        2 * half_extents[0] * meters,
    ):
        raise ContractError("reference width disagrees with normalized geometry")
    if not math.isclose(
        _finite(volume["height"], "reference_volume_m.height"),
        2 * half_extents[1] * meters,
    ):
        raise ContractError("reference height disagrees with normalized geometry")
    _finite(volume["depth"], "reference_volume_m.depth", 0.001)

    surfaces = _objects(value["surfaces"], "surfaces", minimum=2)
    by_surface = _unique(surfaces, "id", "surfaces")
    for surface_id, surface in by_surface.items():
        _exact_keys(
            surface,
            {"id", "status", "center", "rotation_radians", "half_extents", "material"},
            f"surface {surface_id}",
        )
        if (
            surface["status"] != "reference-simulation"
            or surface["material"] != "venue-unassigned"
        ):
            raise ContractError(
                f"surface {surface_id} improperly claims a physical assignment"
            )
        _vector(surface["center"], 3, f"surface {surface_id}.center")
        _vector(
            surface["rotation_radians"], 3, f"surface {surface_id}.rotation_radians"
        )
        extents = _vector(
            surface["half_extents"], 2, f"surface {surface_id}.half_extents"
        )
        if min(extents) <= 0:
            raise ContractError(f"surface {surface_id} half extents must be positive")

    outputs = _objects(value["projection_outputs"], "projection_outputs", minimum=2)
    by_output = _unique(outputs, "id", "projection_outputs")
    channels: set[int] = set()
    for output_id, output in by_output.items():
        _exact_keys(
            output,
            {
                "id",
                "channel",
                "surface",
                "viewport",
                "edge_policy",
                "overlap_px",
                "physical_assignment",
            },
            f"projection output {output_id}",
        )
        channel = output["channel"]
        if (
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or channel < 0
            or channel in channels
        ):
            raise ContractError(
                "projection output channels must be unique non-negative integers"
            )
        channels.add(channel)
        if output["surface"] not in by_surface:
            raise ContractError(
                f"projection output {output_id} names an unknown surface"
            )
        if _vector(
            output["viewport"], 4, f"projection output {output_id}.viewport"
        ) != [0.0, 0.0, 1.0, 1.0]:
            raise ContractError(
                "reference outputs must carry the complete shared projector view"
            )
        if (
            output["edge_policy"] != "hard-boundary-no-blend"
            or output["overlap_px"] != 0
        ):
            raise ContractError(
                "reference output edge policy must remain an explicit zero-overlap hard boundary"
            )
        if output["physical_assignment"] != "requires-venue-approval":
            raise ContractError(
                f"projection output {output_id} improperly claims physical assignment"
            )
    if channels != set(range(len(outputs))):
        raise ContractError("projection output channels must be contiguous from zero")

    sync = _exact_keys(
        value["synchronization"],
        {"mode", "fps", "max_output_skew_ms", "measurement_status"},
        "synchronization",
    )
    if (
        sync["mode"] != "shared-river-frame-ticket"
        or sync["measurement_status"] != "requires-venue-proof"
    ):
        raise ContractError(
            "multi-output synchronization may not claim unmeasured hardware behavior"
        )
    fps = sync["fps"]
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0 or fps > 240:
        raise ContractError("synchronization fps must be a bounded integer")
    output_skew = _finite(
        sync["max_output_skew_ms"],
        "synchronization.max_output_skew_ms",
        0,
        1000 / fps + 0.001,
    )

    audio = _exact_keys(
        value["audio"],
        {
            "source_contract",
            "layout_id",
            "speaker_ids",
            "latency_budget_ms",
            "limiter_ceiling_dbfs",
            "physical_assignment",
        },
        "audio",
    )
    if (
        audio["source_contract"] != "room-layout"
        or audio["physical_assignment"] != "requires-venue-approval"
    ):
        raise ContractError(
            "audio must remain bound to the reference room layout and venue-gated"
        )
    layout_registry = load_json(
        safe_file(root, by_source["room-layout"]["path"], "room layout path")
    )
    layout = next(
        (
            item
            for item in layout_registry["layouts"]
            if item.get("id") == audio["layout_id"]
        ),
        None,
    )
    if not isinstance(layout, dict):
        raise ContractError("digital twin names an unknown room layout")
    expected_speakers = [
        speaker["id"]
        for speaker in sorted(layout["speakers"], key=lambda item: item["channel"])
    ]
    if audio["speaker_ids"] != expected_speakers:
        raise ContractError("digital twin speaker order disagrees with the room layout")
    latency = _finite(audio["latency_budget_ms"], "audio.latency_budget_ms", 0)
    limiter = _finite(
        audio["limiter_ceiling_dbfs"], "audio.limiter_ceiling_dbfs", -120, 0
    )
    if latency != float(layout_registry["safety"]["latency_budget_ms"]):
        raise ContractError(
            "digital twin latency budget disagrees with the room layout"
        )
    if limiter != float(layout_registry["safety"]["limiter_ceiling_dbfs"]):
        raise ContractError(
            "digital twin limiter ceiling disagrees with the room layout"
        )

    calibration = _exact_keys(
        value["calibration"],
        {"sequence", "projection_probe_contract", "thresholds", "measurement_status"},
        "calibration",
    )
    if (
        calibration["projection_probe_contract"] != "projection-probe"
        or calibration["measurement_status"] != "requires-venue-proof"
    ):
        raise ContractError(
            "calibration must remain bound to the probe and require venue measurement"
        )
    expected_sequence = [
        "release-integrity",
        "room-safety",
        "surface-geometry",
        "projector-registration",
        "output-synchronization",
        "speaker-routing",
        "audio-visual-synchronization",
        "visible-plane-cue",
        "runtime-recovery",
    ]
    if calibration["sequence"] != expected_sequence:
        raise ContractError("calibration sequence drifted")
    thresholds = _exact_keys(
        calibration["thresholds"],
        {
            "max_registration_error_px",
            "max_output_skew_ms",
            "max_audio_visual_skew_ms",
            "max_speaker_route_errors",
            "limiter_ceiling_dbfs",
        },
        "calibration.thresholds",
    )
    _finite(thresholds["max_registration_error_px"], "max_registration_error_px", 0, 20)
    if (
        _finite(thresholds["max_output_skew_ms"], "max_output_skew_ms", 0)
        != output_skew
    ):
        raise ContractError(
            "calibration and synchronization output-skew thresholds disagree"
        )
    if (
        _finite(thresholds["max_audio_visual_skew_ms"], "max_audio_visual_skew_ms", 0)
        != latency
    ):
        raise ContractError(
            "calibration AV threshold must match the admitted latency budget"
        )
    if (
        thresholds["max_speaker_route_errors"] != 0
        or float(thresholds["limiter_ceiling_dbfs"]) != limiter
    ):
        raise ContractError(
            "speaker calibration thresholds disagree with the room safety contract"
        )

    roles = value["hardware_roles"]
    if (
        not isinstance(roles, list)
        or not roles
        or not all(isinstance(role, str) and role for role in roles)
    ):
        raise ContractError("hardware_roles must be a non-empty string list")
    if len(set(roles)) != len(roles) or roles != sorted(roles):
        raise ContractError("hardware_roles must be unique and sorted")
    derived_roles = {
        "display-host",
        "surface-front",
        "surface-rear",
        "audio-interface",
        "power-distribution",
        "ventilation-path",
        *by_output,
        *(f"speaker-{speaker}" for speaker in audio["speaker_ids"]),
    }
    if set(roles) != derived_roles:
        raise ContractError(
            "hardware role inventory does not cover every output, surface, and speaker"
        )

    runtime = _exact_keys(
        value["runtime"],
        {
            "mode",
            "persistent_host_service",
            "single_approved_launcher",
            "forbidden_host_mutations",
            "health",
            "recovery",
        },
        "runtime",
    )
    if (
        runtime["mode"] != "foreground-supervisor"
        or runtime["persistent_host_service"] is not False
        or runtime["single_approved_launcher"] is not True
    ):
        raise ContractError("runtime must be a single approved foreground supervisor")
    if set(runtime["forbidden_host_mutations"]) != FORBIDDEN_HOST_MUTATIONS:
        raise ContractError("runtime forbidden-host-mutation set drifted")
    health = _exact_keys(
        runtime["health"],
        {
            "probe_interval_seconds",
            "startup_timeout_seconds",
            "probe_timeout_seconds",
            "max_consecutive_failures",
        },
        "runtime.health",
    )
    probe_interval = _finite(
        health["probe_interval_seconds"], "probe_interval_seconds", 0.1, 60
    )
    startup_timeout = _finite(
        health["startup_timeout_seconds"],
        "startup_timeout_seconds",
        probe_interval,
        600,
    )
    _finite(
        health["probe_timeout_seconds"],
        "probe_timeout_seconds",
        0.1,
        min(probe_interval, startup_timeout),
    )
    failures = health["max_consecutive_failures"]
    if (
        isinstance(failures, bool)
        or not isinstance(failures, int)
        or not 1 <= failures <= 10
    ):
        raise ContractError(
            "runtime health failure threshold must be a bounded integer"
        )
    recovery = _exact_keys(
        runtime["recovery"],
        {
            "max_restarts",
            "window_seconds",
            "stable_seconds",
            "backoff_seconds",
            "wall_plug_return_timeout_seconds",
            "wall_plug_proofs_required",
        },
        "runtime.recovery",
    )
    max_restarts = recovery["max_restarts"]
    if (
        isinstance(max_restarts, bool)
        or not isinstance(max_restarts, int)
        or not 0 <= max_restarts <= 10
    ):
        raise ContractError("runtime max_restarts must be a bounded integer")
    window = _finite(recovery["window_seconds"], "window_seconds", 1, 3600)
    _finite(recovery["stable_seconds"], "stable_seconds", 0, window)
    backoffs = recovery["backoff_seconds"]
    if not isinstance(backoffs, list) or len(backoffs) != max_restarts:
        raise ContractError(
            "runtime backoff sequence must cover every admitted restart"
        )
    if any(_finite(delay, "backoff_seconds", 0, window) > window for delay in backoffs):
        raise ContractError("runtime backoff exceeds its recovery window")
    _finite(
        recovery["wall_plug_return_timeout_seconds"],
        "wall_plug_return_timeout_seconds",
        startup_timeout,
        3600,
    )
    if recovery["wall_plug_proofs_required"] != 3:
        raise ContractError(
            "the physical predicate requires exactly three wall-plug proofs"
        )

    release = _exact_keys(
        value["release"],
        {
            "canonical_release_required",
            "developer_checkout_allowed",
            "manifest_name",
            "manifest_schema",
        },
        "release",
    )
    if release != {
        "canonical_release_required": True,
        "developer_checkout_allowed": False,
        "manifest_name": "release-manifest.json",
        "manifest_schema": "danse.installation.release.v1",
    }:
        raise ContractError(
            "installation runtime must consume a canonical release, never a developer checkout"
        )
    return value


def validate_gates(value: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        value,
        {
            "schema",
            "spec_contract_sha256",
            "status",
            "physical_predicates_satisfied",
            "issue_14_can_close",
            "gates",
        },
        "gate ledger",
    )
    if value["schema"] != "danse.installation.gates.v1":
        raise ContractError("unknown installation gate schema")
    if value["spec_contract_sha256"] != spec["identity"]["contract_sha256"]:
        raise ContractError("gate ledger is not bound to the digital twin")
    if (
        value["status"] != "blocked"
        or value["physical_predicates_satisfied"] is not False
        or value["issue_14_can_close"] is not False
    ):
        raise ContractError(
            "installation cannot claim physical completion without external evidence"
        )
    gates = _objects(value["gates"], "gates")
    by_gate = _unique(gates, "id", "gates")
    if set(by_gate) != REQUIRED_GATE_IDS:
        raise ContractError("installation gate inventory is incomplete")
    for gate_id, gate in by_gate.items():
        _exact_keys(gate, {"id", "status", "receipt"}, f"gate {gate_id}")
        if gate["status"] != "blocked" or gate["receipt"] is not None:
            raise ContractError(
                f"gate {gate_id} cannot pass without a durable external receipt"
            )
    return value


def validate_archive_disposition(value: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        value, {"schema", "source", "decisions", "result"}, "archive disposition"
    )
    if value["schema"] != "danse.installation.archive-disposition.v1":
        raise ContractError("unknown installation archive disposition schema")
    source = _exact_keys(
        value["source"],
        {"repository", "branch", "commit", "authority", "merge_wholesale", "files"},
        "archive source",
    )
    expected_source = {
        "repository": "organvm/limen",
        "branch": "archive/danse-predecessor-experiments-20260802",
        "commit": "a232f2d7160e213802580e2d532a0d2d9ac65727",
        "authority": "non-authoritative-evidence-only",
        "merge_wholesale": False,
    }
    if any(source[key] != expected for key, expected in expected_source.items()):
        raise ContractError("installation archive source identity drifted")
    files = _objects(source["files"], "archive source files", minimum=2)
    by_path = _unique(files, "path", "archive source files")
    expected_files = {
        "docs/plans/danse-installation-spec.md": "aedb6bc67861fa71a29e154f8b060d508f84e281d632b98fddc90607a5034497",
        "organs/artist/chambers/danse.yaml": "4d68e423773c7c222399ab5260a9d9b702f4aed539a9958020a996f06f07b6b6",
    }
    if set(by_path) != set(expected_files):
        raise ContractError("installation archive file inventory drifted")
    for path, digest in expected_files.items():
        _exact_keys(by_path[path], {"path", "sha256"}, f"archive file {path}")
        if by_path[path]["sha256"] != digest:
            raise ContractError(f"archive file digest drifted for {path}")
    decisions = _objects(value["decisions"], "archive decisions")
    by_decision = _unique(decisions, "id", "archive decisions")
    if {
        key: row.get("status") for key, row in by_decision.items()
    } != REQUIRED_ARCHIVE_DECISIONS:
        raise ContractError(
            "archived installation claims lack an exact deliberate disposition"
        )
    for decision_id, decision in by_decision.items():
        _exact_keys(
            decision, {"id", "status", "disposition"}, f"archive decision {decision_id}"
        )
        _nonempty(
            decision["disposition"], f"archive decision {decision_id}.disposition"
        )
    result = _exact_keys(
        value["result"],
        {
            "status",
            "canonical_contract",
            "physical_evidence_present",
            "issue_14_complete",
        },
        "archive result",
    )
    if result != {
        "status": "ported",
        "canonical_contract": "installation/digital-twin.json",
        "physical_evidence_present": False,
        "issue_14_complete": False,
    }:
        raise ContractError("archive disposition may not claim physical completion")
    return value


def frame_ticket(
    spec: dict[str, Any], seed: int, stream: int, frame: int
) -> dict[str, Any]:
    validate_digital_twin(spec)
    seed = _uint32(seed, "frame ticket seed")
    stream = _uint32(stream, "frame ticket stream")
    if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
        raise ContractError("frame ticket frame must be a non-negative integer")
    fps = spec["synchronization"]["fps"]
    sources = {source["id"]: source for source in spec["source_contracts"]}
    ticket = {
        "schema": "danse.installation.frame-ticket.v1",
        "spec_contract_sha256": spec["identity"]["contract_sha256"],
        "river": {"seed": seed, "stream": stream},
        "frame": frame,
        "t": round(frame / fps, 9),
        "program_sha256": sources["program"]["sha256"],
        "score_contract_sha256": sources["score"]["embedded_contract_sha256"],
        "outputs": [
            {
                "id": output["id"],
                "channel": output["channel"],
                "surface": output["surface"],
            }
            for output in sorted(
                spec["projection_outputs"], key=lambda item: item["channel"]
            )
        ],
    }
    ticket["ticket_sha256"] = canonical_sha256(ticket)
    return ticket


def calibration_plan(spec: dict[str, Any]) -> dict[str, Any]:
    validate_digital_twin(spec)
    plan = {
        "schema": "danse.installation.calibration-plan.v1",
        "spec_contract_sha256": spec["identity"]["contract_sha256"],
        "sequence": list(spec["calibration"]["sequence"]),
        "projection": [
            {
                "output": output["id"],
                "surface": output["surface"],
                "viewport": list(output["viewport"]),
                "edge_policy": output["edge_policy"],
                "overlap_px": output["overlap_px"],
                "probe_sha256": next(
                    source["sha256"]
                    for source in spec["source_contracts"]
                    if source["id"] == "projection-probe"
                ),
            }
            for output in sorted(
                spec["projection_outputs"], key=lambda item: item["channel"]
            )
        ],
        "audio": [
            {"speaker": speaker, "diagnostic_event": f"0:calibration.impulse:{index}"}
            for index, speaker in enumerate(spec["audio"]["speaker_ids"])
        ],
        "thresholds": copy.deepcopy(spec["calibration"]["thresholds"]),
        "physical_measurements": "required-not-present",
    }
    plan["plan_sha256"] = canonical_sha256(plan)
    return plan


def _receipt_sha(value: Any, label: str) -> str:
    return _sha256(value, label)


def _approved(value: Any, label: str) -> None:
    if value is not True:
        raise ContractError(f"{label} must be explicitly approved")


def _release_inventory(root: Path, manifest_relative: str) -> set[str]:
    """Inventory regular release files without following links or Git metadata."""
    inventory: set[str] = set()

    def walk_error(exc: OSError) -> None:
        raise ContractError("canonical release inventory cannot be read") from exc

    for current_value, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=walk_error
    ):
        current = Path(current_value)
        directories.sort()
        filenames.sort()
        for name in directories:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ContractError(
                    f"canonical release may not contain a symlink: {relative}"
                )
            if name == ".git":
                raise ContractError(
                    "installation release root may not contain Git metadata"
                )
        for name in filenames:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ContractError(
                    f"canonical release may not contain a symlink: {relative}"
                )
            if ".git" in PurePosixPath(relative).parts:
                raise ContractError(
                    "installation release root may not contain Git metadata"
                )
            if not path.is_file():
                raise ContractError(
                    f"canonical release may contain only regular files: {relative}"
                )
            if relative != manifest_relative:
                inventory.add(relative)
    return inventory


def _validate_release(
    release: dict[str, Any], release_root: Path, spec: dict[str, Any]
) -> tuple[Path, dict[str, dict[str, Any]]]:
    _exact_keys(
        release,
        {"root_kind", "manifest_path", "manifest_sha256", "developer_checkout"},
        "evidence.release",
    )
    if (
        release["root_kind"] != "canonical-release"
        or release["developer_checkout"] is not False
    ):
        raise ContractError(
            "installation evidence must name a canonical release, not a developer checkout"
        )
    if release_root.is_symlink():
        raise ContractError("installation release root may not be a symlink")
    root = release_root.resolve(strict=True)
    try:
        root.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ContractError(
            "installation runtime cannot use a developer checkout as its release root"
        )
    if release["manifest_path"] != spec["release"]["manifest_name"]:
        raise ContractError(
            "installation release manifest name disagrees with the digital twin"
        )
    manifest = safe_file(
        root, release["manifest_path"], "evidence.release.manifest_path"
    )
    if file_sha256(manifest) != _receipt_sha(
        release["manifest_sha256"], "evidence.release.manifest_sha256"
    ):
        raise ContractError("canonical release manifest digest does not match evidence")
    document = load_json(manifest)
    _exact_keys(
        document,
        {"schema", "spec_contract_sha256", "files"},
        "canonical release manifest",
    )
    if document["schema"] != spec["release"]["manifest_schema"]:
        raise ContractError("canonical release manifest schema is unsupported")
    if document["spec_contract_sha256"] != spec["identity"]["contract_sha256"]:
        raise ContractError(
            "canonical release manifest belongs to another installation contract"
        )
    records = _objects(document["files"], "canonical release manifest files")
    paths: list[str] = []
    for index, record in enumerate(records):
        _exact_keys(
            record,
            {"path", "bytes", "sha256", "executable"},
            f"canonical release manifest files[{index}]",
        )
        relative = record["path"]
        if (
            not isinstance(relative, str)
            or PurePosixPath(relative).as_posix() != relative
        ):
            raise ContractError(
                "canonical release manifest paths must be canonical relative paths"
            )
        paths.append(relative)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ContractError(
            "canonical release manifest paths must be unique and sorted"
        )
    by_path = {record["path"]: record for record in records}
    for relative, record in by_path.items():
        if relative == release["manifest_path"]:
            raise ContractError("canonical release manifest may not inventory itself")
        path = safe_file(root, relative, f"canonical release file {relative}")
        byte_count = record["bytes"]
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or path.stat().st_size != byte_count
        ):
            raise ContractError(
                f"canonical release file byte count drifted: {relative}"
            )
        if file_sha256(path) != _sha256(
            record["sha256"], f"canonical release file {relative}.sha256"
        ):
            raise ContractError(f"canonical release file digest drifted: {relative}")
        executable = record["executable"]
        actual_executable = bool(path.stat().st_mode & 0o111)
        if not isinstance(executable, bool) or executable != actual_executable:
            raise ContractError(
                f"canonical release file executable mode drifted: {relative}"
            )
    inventory = _release_inventory(root, release["manifest_path"])
    if inventory != set(by_path):
        missing = sorted(set(by_path) - inventory)
        extra = sorted(inventory - set(by_path))
        raise ContractError(
            f"canonical release inventory drifted; missing={missing}, extra={extra}"
        )
    return root, by_path


def _validate_health_url(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError("runtime health_url must be null or a loopback HTTP URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ContractError("runtime health_url is malformed") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.fragment
    ):
        raise ContractError(
            "runtime health_url must be credential-free numeric loopback HTTP with an explicit port"
        )
    return value


def validate_evidence(
    value: dict[str, Any],
    spec: dict[str, Any],
    *,
    phase: str,
    release_root: Path,
) -> dict[str, Any]:
    if phase not in {"runtime", "complete"}:
        raise ContractError(f"unknown physical evidence phase {phase!r}")
    validate_digital_twin(spec)
    _exact_keys(
        value,
        {
            "schema",
            "evidence_id",
            "spec_contract_sha256",
            "venue",
            "geometry",
            "release",
            "hardware",
            "calibration",
            "runtime",
            "wall_plug_proofs",
            "restore_rehearsal",
        },
        "installation evidence",
    )
    if value["schema"] != "danse.installation.evidence.v1":
        raise ContractError("unknown installation evidence schema")
    _nonempty(value["evidence_id"], "evidence_id")
    if value["spec_contract_sha256"] != spec["identity"]["contract_sha256"]:
        raise ContractError("installation evidence belongs to a different digital twin")

    venue = _exact_keys(
        value["venue"],
        {
            "id",
            "approved",
            "approved_by",
            "approved_at",
            "approval_receipt_sha256",
            "dimensions_m",
            "egress_approved",
            "mounting_approved",
            "power_approved",
            "ventilation_approved",
            "safety_receipt_sha256",
        },
        "evidence.venue",
    )
    _nonempty(venue["id"], "evidence.venue.id")
    _approved(venue["approved"], "venue")
    _nonempty(venue["approved_by"], "evidence.venue.approved_by")
    _timestamp(venue["approved_at"], "evidence.venue.approved_at")
    _receipt_sha(venue["approval_receipt_sha256"], "venue approval receipt")
    dimensions = _exact_keys(
        venue["dimensions_m"],
        {"width", "height", "depth"},
        "evidence.venue.dimensions_m",
    )
    reference = spec["coordinate_system"]["reference_volume_m"]
    for axis in ("width", "height", "depth"):
        if _finite(dimensions[axis], f"venue dimension {axis}", 0.1) < float(
            reference[axis]
        ):
            raise ContractError(f"venue {axis} is smaller than the reference twin")
    for key in (
        "egress_approved",
        "mounting_approved",
        "power_approved",
        "ventilation_approved",
    ):
        _approved(venue[key], f"venue {key}")
    _receipt_sha(venue["safety_receipt_sha256"], "venue safety receipt")

    geometry = _exact_keys(
        value["geometry"],
        {"surfaces", "projectors", "receipt_sha256"},
        "evidence.geometry",
    )
    measured_surfaces = _objects(geometry["surfaces"], "evidence.geometry.surfaces")
    by_reference_surface = _unique(
        measured_surfaces, "reference_surface", "evidence.geometry.surfaces"
    )
    reference_surfaces = {surface["id"]: surface for surface in spec["surfaces"]}
    if set(by_reference_surface) != set(reference_surfaces):
        raise ContractError("venue geometry does not map every reference surface")
    meters_per_unit = spec["coordinate_system"]["meters_per_unit"]
    for surface_id, measured in by_reference_surface.items():
        _exact_keys(
            measured,
            {
                "reference_surface",
                "hardware_role",
                "center_m",
                "rotation_radians",
                "size_m",
                "measurement_receipt_sha256",
            },
            f"measured surface {surface_id}",
        )
        if measured["hardware_role"] not in spec["hardware_roles"] or not measured[
            "hardware_role"
        ].startswith("surface-"):
            raise ContractError(
                f"measured surface {surface_id} lacks a declared surface hardware role"
            )
        reference_surface = reference_surfaces[surface_id]
        expected_center = [
            coordinate * meters_per_unit for coordinate in reference_surface["center"]
        ]
        observed_center = _vector(
            measured["center_m"], 3, f"measured surface {surface_id}.center_m"
        )
        if any(
            not math.isclose(actual, expected, abs_tol=0.001)
            for actual, expected in zip(observed_center, expected_center)
        ):
            raise ContractError(
                f"measured surface {surface_id} center disagrees with the approved twin"
            )
        observed_rotation = _vector(
            measured["rotation_radians"],
            3,
            f"measured surface {surface_id}.rotation_radians",
        )
        if any(
            not math.isclose(actual, expected, abs_tol=0.001)
            for actual, expected in zip(
                observed_rotation, reference_surface["rotation_radians"]
            )
        ):
            raise ContractError(
                f"measured surface {surface_id} rotation disagrees with the approved twin"
            )
        expected_size = [
            2 * extent * meters_per_unit for extent in reference_surface["half_extents"]
        ]
        observed_size = _vector(
            measured["size_m"], 2, f"measured surface {surface_id}.size_m"
        )
        if any(
            not math.isclose(actual, expected, abs_tol=0.001)
            for actual, expected in zip(observed_size, expected_size)
        ):
            raise ContractError(
                f"measured surface {surface_id} size disagrees with the approved twin"
            )
        _receipt_sha(
            measured["measurement_receipt_sha256"],
            f"measured surface {surface_id} receipt",
        )

    measured_projectors = _objects(
        geometry["projectors"], "evidence.geometry.projectors"
    )
    by_output = _unique(measured_projectors, "output", "evidence.geometry.projectors")
    reference_outputs = {output["id"]: output for output in spec["projection_outputs"]}
    if set(by_output) != set(reference_outputs):
        raise ContractError("venue geometry does not map every projection output")
    for output_id, measured in by_output.items():
        _exact_keys(
            measured,
            {
                "output",
                "hardware_role",
                "surface",
                "position_m",
                "aim_point_m",
                "throw_distance_m",
                "resolution_px",
                "refresh_hz",
                "lens_receipt_sha256",
            },
            f"measured projector {output_id}",
        )
        output = reference_outputs[output_id]
        if (
            measured["hardware_role"] != output_id
            or measured["hardware_role"] not in spec["hardware_roles"]
        ):
            raise ContractError(
                f"projector {output_id} lacks its declared hardware role"
            )
        if measured["surface"] != output["surface"]:
            raise ContractError(
                f"projector {output_id} is assigned to the wrong reference surface"
            )
        position = _vector(
            measured["position_m"], 3, f"projector {output_id}.position_m"
        )
        aim = _vector(measured["aim_point_m"], 3, f"projector {output_id}.aim_point_m")
        throw = _finite(
            measured["throw_distance_m"],
            f"projector {output_id}.throw_distance_m",
            0.01,
            1000,
        )
        if not math.isclose(throw, math.dist(position, aim), abs_tol=0.001):
            raise ContractError(
                f"projector {output_id} throw distance disagrees with its measured pose"
            )
        resolution = measured["resolution_px"]
        if (
            not isinstance(resolution, list)
            or len(resolution) != 2
            or any(
                isinstance(axis, bool) or not isinstance(axis, int) or axis <= 0
                for axis in resolution
            )
        ):
            raise ContractError(
                f"projector {output_id} resolution must be two positive integers"
            )
        if not math.isclose(
            _finite(
                measured["refresh_hz"], f"projector {output_id}.refresh_hz", 1, 1000
            ),
            spec["synchronization"]["fps"],
            abs_tol=0.001,
        ):
            raise ContractError(
                f"projector {output_id} refresh disagrees with the sync contract"
            )
        _receipt_sha(
            measured["lens_receipt_sha256"], f"projector {output_id} lens receipt"
        )
    _receipt_sha(geometry["receipt_sha256"], "venue geometry receipt")

    root, release_files = _validate_release(value["release"], release_root, spec)

    hardware = _exact_keys(
        value["hardware"],
        {
            "assets",
            "cabling_receipt_sha256",
            "power_receipt_sha256",
            "ventilation_receipt_sha256",
        },
        "evidence.hardware",
    )
    assets = _objects(hardware["assets"], "evidence.hardware.assets")
    by_role = _unique(assets, "role", "evidence.hardware.assets")
    if set(by_role) != set(spec["hardware_roles"]):
        raise ContractError(
            "venue hardware evidence does not cover every required role"
        )
    asset_ids: set[str] = set()
    for role, asset in by_role.items():
        _exact_keys(
            asset,
            {"role", "asset_id", "verified", "receipt_sha256"},
            f"hardware asset {role}",
        )
        asset_id = _nonempty(asset["asset_id"], f"hardware asset {role}.asset_id")
        if asset_id in asset_ids:
            raise ContractError("hardware asset ids must be unique")
        asset_ids.add(asset_id)
        _approved(asset["verified"], f"hardware asset {role}")
        _receipt_sha(asset["receipt_sha256"], f"hardware asset {role} receipt")
    for receipt in (
        "cabling_receipt_sha256",
        "power_receipt_sha256",
        "ventilation_receipt_sha256",
    ):
        _receipt_sha(hardware[receipt], f"hardware {receipt}")

    calibration = _exact_keys(
        value["calibration"],
        {
            "spec_contract_sha256",
            "projector_registration_error_px",
            "output_skew_ms",
            "audio_visual_skew_ms",
            "speaker_route_errors",
            "limiter_ceiling_dbfs",
            "visible_plane_cue",
            "receipt_sha256",
        },
        "evidence.calibration",
    )
    if calibration["spec_contract_sha256"] != spec["identity"]["contract_sha256"]:
        raise ContractError("calibration evidence belongs to a different digital twin")
    thresholds = spec["calibration"]["thresholds"]
    comparisons = (
        ("projector_registration_error_px", "max_registration_error_px"),
        ("output_skew_ms", "max_output_skew_ms"),
        ("audio_visual_skew_ms", "max_audio_visual_skew_ms"),
    )
    for observed, maximum in comparisons:
        if _finite(calibration[observed], f"calibration.{observed}", 0) > float(
            thresholds[maximum]
        ):
            raise ContractError(
                f"calibration {observed} exceeds the admitted threshold"
            )
    route_errors = calibration["speaker_route_errors"]
    if (
        isinstance(route_errors, bool)
        or not isinstance(route_errors, int)
        or route_errors > thresholds["max_speaker_route_errors"]
        or route_errors < 0
    ):
        raise ContractError("speaker routing calibration failed")
    if _finite(
        calibration["limiter_ceiling_dbfs"], "calibration.limiter_ceiling_dbfs", -120, 0
    ) > float(thresholds["limiter_ceiling_dbfs"]):
        raise ContractError("measured limiter ceiling exceeds the admitted maximum")
    plane_cue = _exact_keys(
        calibration["visible_plane_cue"],
        {"passed", "observer", "observed_at", "receipt_sha256"},
        "visible_plane_cue",
    )
    _approved(plane_cue["passed"], "human-observed visible-plane/cue test")
    _nonempty(plane_cue["observer"], "visible_plane_cue.observer")
    _timestamp(plane_cue["observed_at"], "visible_plane_cue.observed_at")
    _receipt_sha(plane_cue["receipt_sha256"], "visible-plane/cue receipt")
    _receipt_sha(calibration["receipt_sha256"], "calibration receipt")

    runtime = _exact_keys(
        value["runtime"],
        {
            "approved",
            "approved_by",
            "approval_receipt_sha256",
            "argv",
            "argv_sha256",
            "health_url",
            "river",
        },
        "evidence.runtime",
    )
    _approved(runtime["approved"], "venue runtime")
    _nonempty(runtime["approved_by"], "runtime.approved_by")
    _receipt_sha(runtime["approval_receipt_sha256"], "runtime approval receipt")
    argv = runtime["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv)
    ):
        raise ContractError("runtime argv must be a non-empty argument vector")
    if canonical_sha256(argv) != _receipt_sha(
        runtime["argv_sha256"], "runtime argv digest"
    ):
        raise ContractError("runtime argv digest is stale")
    launcher = release_files.get(argv[0])
    if launcher is None or launcher["executable"] is not True:
        raise ContractError(
            "runtime executable must be an executable file bound by the canonical release manifest"
        )
    executable = safe_file(root, argv[0], "runtime executable")
    if not os.access(executable, os.X_OK):
        raise ContractError("runtime executable is not executable")
    _validate_health_url(runtime["health_url"])
    river = _exact_keys(
        runtime["river"], {"seed", "stream", "epoch_ms"}, "runtime.river"
    )
    _uint32(river["seed"], "runtime river seed")
    _uint32(river["stream"], "runtime river stream")
    epoch = river["epoch_ms"]
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 0
        or epoch > 9_007_199_254_740_991
    ):
        raise ContractError(
            "runtime river epoch_ms must be a non-negative safe integer"
        )

    proofs = value["wall_plug_proofs"]
    required = spec["runtime"]["recovery"]["wall_plug_proofs_required"]
    if (
        not isinstance(proofs, list)
        or len(proofs) > required
        or not all(isinstance(proof, dict) for proof in proofs)
    ):
        raise ContractError(
            f"wall_plug_proofs must contain at most {required} proof objects"
        )
    restore = _exact_keys(
        value["restore_rehearsal"],
        {
            "setup_passed",
            "strike_passed",
            "restore_passed",
            "canonical_release_restored",
            "observer",
            "observed_at",
            "setup_receipt_sha256",
            "strike_receipt_sha256",
            "restore_receipt_sha256",
        },
        "restore_rehearsal",
    )
    if phase == "complete" and len(proofs) != required:
        raise ContractError(
            f"physical completion requires exactly {required} wall-plug proofs"
        )
    if proofs:
        by_proof = _unique(proofs, "id", "wall_plug_proofs")
        observers: set[tuple[str, str]] = set()
        telemetry: set[str] = set()
        for proof_id, proof in by_proof.items():
            _exact_keys(
                proof,
                {
                    "id",
                    "observer",
                    "observed_at",
                    "power_removed_seconds",
                    "returned_to_display_seconds",
                    "generative_display_returned",
                    "manual_repair_required",
                    "spec_contract_sha256",
                    "runtime_telemetry_sha256",
                    "receipt_sha256",
                },
                f"wall-plug proof {proof_id}",
            )
            observer = _nonempty(
                proof["observer"], f"wall-plug proof {proof_id}.observer"
            )
            observed_at = _timestamp(
                proof["observed_at"], f"wall-plug proof {proof_id}.observed_at"
            )
            if (observer, observed_at) in observers:
                raise ContractError(
                    "wall-plug proofs must be distinct human observations"
                )
            observers.add((observer, observed_at))
            _finite(
                proof["power_removed_seconds"],
                f"wall-plug proof {proof_id}.power_removed_seconds",
                1,
                3600,
            )
            returned = _finite(
                proof["returned_to_display_seconds"],
                f"wall-plug proof {proof_id}.returned_to_display_seconds",
                0,
            )
            if (
                returned
                > spec["runtime"]["recovery"]["wall_plug_return_timeout_seconds"]
            ):
                raise ContractError(
                    f"wall-plug proof {proof_id} exceeded the recovery timeout"
                )
            _approved(
                proof["generative_display_returned"],
                f"wall-plug proof {proof_id} display return",
            )
            if proof["manual_repair_required"] is not False:
                raise ContractError(
                    f"wall-plug proof {proof_id} required manual repair"
                )
            if proof["spec_contract_sha256"] != spec["identity"]["contract_sha256"]:
                raise ContractError(
                    f"wall-plug proof {proof_id} belongs to another configuration"
                )
            telemetry_sha = _receipt_sha(
                proof["runtime_telemetry_sha256"],
                f"wall-plug proof {proof_id} telemetry",
            )
            if telemetry_sha in telemetry:
                raise ContractError(
                    "wall-plug proofs must preserve distinct telemetry receipts"
                )
            telemetry.add(telemetry_sha)
            _receipt_sha(proof["receipt_sha256"], f"wall-plug proof {proof_id} receipt")
    restore_flags = (
        "setup_passed",
        "strike_passed",
        "restore_passed",
        "canonical_release_restored",
    )
    restore_receipts = (
        "setup_receipt_sha256",
        "strike_receipt_sha256",
        "restore_receipt_sha256",
    )
    restore_claimed = any(restore[key] is not False for key in restore_flags) or any(
        restore[key] is not None
        for key in ("observer", "observed_at", *restore_receipts)
    )
    if phase == "complete" or restore_claimed:
        for key in restore_flags:
            _approved(restore[key], f"restore rehearsal {key}")
        _nonempty(restore["observer"], "restore_rehearsal.observer")
        _timestamp(restore["observed_at"], "restore_rehearsal.observed_at")
        for receipt in restore_receipts:
            _receipt_sha(restore[receipt], f"restore rehearsal {receipt}")
    return value


def runtime_plan(
    value: dict[str, Any], spec: dict[str, Any], release_root: Path
) -> dict[str, Any]:
    validate_evidence(value, spec, phase="runtime", release_root=release_root)
    runtime = value["runtime"]
    _, release_files = _validate_release(value["release"], release_root, spec)
    launcher = release_files[runtime["argv"][0]]
    return {
        "schema": "danse.installation.runtime-plan.v1",
        "spec_contract_sha256": spec["identity"]["contract_sha256"],
        "evidence_id": value["evidence_id"],
        "evidence_sha256": canonical_sha256(value),
        "release_manifest_sha256": value["release"]["manifest_sha256"],
        "launcher": copy.deepcopy(launcher),
        "argv": list(runtime["argv"]),
        "health_url": runtime["health_url"],
        "river": copy.deepcopy(runtime["river"]),
        "outputs": [
            output["id"]
            for output in sorted(
                spec["projection_outputs"], key=lambda item: item["channel"]
            )
        ],
        "policy": copy.deepcopy(spec["runtime"]),
    }


def load_reference_contracts(
    root: Path = ROOT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    spec = validate_digital_twin(
        load_json(root / "installation/digital-twin.json"), root
    )
    gates = validate_gates(load_json(root / "installation/gates.json"), spec)
    archive = validate_archive_disposition(
        load_json(root / "installation/archive-disposition.json")
    )
    return spec, gates, archive

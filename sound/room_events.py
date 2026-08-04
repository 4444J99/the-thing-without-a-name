"""Immutable room-event validation, O(1)-seek, and speaker routing.

The JavaScript engine compiles one bus per passage. Python consumes those exact
bytes for offline, stereo, and multichannel plans; no renderer reconstructs an
event from elapsed state or invents a note that began before a seek boundary.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from music_score import canonical_sha256

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAYOUTS = ROOT / "sound" / "room-layout.json"
ROOM_BUS_SCHEMA = "danse.room.events.v1"
ROOM_LAYOUT_SCHEMA = "danse.room.layouts.v1"
ROOM_PLAN_SCHEMA = "danse.room.render-plan.v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UINT32_MAX = 0xFFFFFFFF
EVENT_TYPES = {
    "passage.start",
    "movement.start",
    "plane.assembly",
    "score.cue",
    "plane.recast",
    "score.note",
    "calibration.impulse",
}
EVENT_ORDER = {
    "passage.start": 0,
    "movement.start": 1,
    "plane.assembly": 2,
    "score.cue": 3,
    "plane.recast": 4,
    "score.note": 5,
    "calibration.impulse": 6,
}


class PassageIdentity(TypedDict):
    river_seed: int
    stream: int
    index: int
    seed: int
    t0: float
    seconds: float


class RoomPosition(TypedDict):
    x: float
    y: float
    z: float


class RoomAudio(TypedDict):
    role: str | None
    source_sha256: str | None
    pitch: int | None


class RoomEvent(TypedDict):
    index: int
    id: str
    type: str
    at: float
    end: NotRequired[float]
    source_second: float
    position: RoomPosition
    depth: float
    intensity: float
    passage: PassageIdentity
    audio: RoomAudio
    source: dict[str, Any]
    target_speaker: NotRequired[str]


def _object(value: Any) -> bool:
    return isinstance(value, dict)


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _uint32(value: Any) -> bool:
    return type(value) is int and 0 <= value <= UINT32_MAX


def _rounded(value: float, places: int = 9) -> float:
    scale = 10**places
    return math.floor(value * scale + 0.5) / scale


def _without_declared_digest(contract: dict[str, Any]) -> dict[str, Any]:
    identity = contract.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("room contract identity must be a mapping")
    return {
        **contract,
        "identity": {key: value for key, value in identity.items() if key != "contract_sha256"},
    }


def room_contract_sha256(contract: dict[str, Any]) -> str:
    return canonical_sha256(_without_declared_digest(contract))


def layout_contract_sha256(registry: dict[str, Any]) -> str:
    return canonical_sha256(_without_declared_digest(registry))


def _validate_passage(passage: Any, label: str = "passage") -> dict[str, Any]:
    if not isinstance(passage, dict):
        raise ValueError(f"{label} must be a mapping")
    if not all(_uint32(passage.get(name)) for name in ("river_seed", "stream", "seed")):
        raise ValueError(f"{label} seed and stream fields must be uint32 values")
    if type(passage.get("index")) is not int or passage["index"] < 0:
        raise ValueError(f"{label}.index must be non-negative")
    if (
        not _finite(passage.get("t0"))
        or passage["t0"] < 0
        or not _finite(passage.get("seconds"))
        or passage["seconds"] <= 0
    ):
        raise ValueError(f"{label} must have finite non-negative t0 and positive seconds")
    return passage


def _lower_bound(events: list[dict[str, Any]], at: float) -> int:
    lo = 0
    hi = len(events)
    while lo < hi:
        mid = (lo + hi) >> 1
        if float(events[mid]["at"]) < at:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _lookup_rows(events: list[dict[str, Any]], t0: float, seconds: float) -> dict[str, Any]:
    buckets = []
    for second in range(math.ceil(seconds)):
        start = _lower_bound(events, t0 + second)
        end = _lower_bound(events, min(t0 + seconds, t0 + second + 1))
        buckets.append({"event_start": [start, end]})
    return {
        "quantum_seconds": 1,
        "buckets": buckets,
        "maxima": {
            "event_starts_per_bucket": max(
                (bucket["event_start"][1] - bucket["event_start"][0] for bucket in buckets),
                default=0,
            )
        },
    }


def validate_room_bus(bus: Any) -> dict[str, Any]:
    def bad(message: str) -> None:
        raise ValueError(f"room events: {message}")

    if not isinstance(bus, dict) or bus.get("schema") != ROOM_BUS_SCHEMA:
        bad(f"unknown schema {bus.get('schema') if isinstance(bus, dict) else None}")
    if bus.get("semantics") != "authored-start-events":
        bad("semantics must be authored-start-events")
    if bus.get("release_status") not in {"fixture-only", "artistic-gate-required", "diagnostic-only"}:
        bad("release_status is invalid")
    try:
        passage = _validate_passage((bus.get("identity") or {}).get("passage"), "identity.passage")
    except ValueError as exc:
        bad(str(exc))
    score_digest = (bus.get("identity") or {}).get("score_contract_sha256")
    midi_digest = (bus.get("identity") or {}).get("midi_sha256")
    if bus.get("release_status") == "diagnostic-only":
        if score_digest is not None or midi_digest is not None:
            bad("diagnostic buses cannot claim score or MIDI provenance")
    elif not all(isinstance(digest, str) and SHA256.fullmatch(digest) for digest in (score_digest, midi_digest)):
        bad("score and MIDI identities must be exact SHA-256 digests")
    provenance = bus.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("policy") != "declared-source-bytes-only"
        or not (
            provenance.get("score_work_id") is None
            or isinstance(provenance.get("score_work_id"), str)
            and provenance["score_work_id"]
        )
        or not all(
            digest is None or isinstance(digest, str) and SHA256.fullmatch(digest)
            for digest in (
                provenance.get("repertoire_entry_sha256"),
                provenance.get("layout_contract_sha256"),
            )
        )
    ):
        bad("provenance is invalid")
    time = bus.get("time")
    if (
        not isinstance(time, dict)
        or time.get("basis") != "absolute-river-seconds"
        or not all(_finite(time.get(name)) for name in ("t0", "t1", "seconds"))
        or time["t0"] != passage["t0"]
        or time["seconds"] != passage["seconds"]
        or abs(float(time["t1"]) - (float(time["t0"]) + float(time["seconds"]))) > 1e-6
    ):
        bad("time must match the declared passage partition")
    events = bus.get("events")
    if not isinstance(events, list) or not events:
        bad("events must be non-empty")
    previous = -math.inf
    ids: set[str] = set()
    for index, event in enumerate(events):
        if (
            not isinstance(event, dict)
            or event.get("index") != index
            or event.get("type") not in EVENT_TYPES
            or not isinstance(event.get("id"), str)
            or not event["id"]
            or event["id"] in ids
        ):
            bad(f"event {index} is malformed")
        ids.add(event["id"])
        at = event.get("at")
        if not _finite(at) or at < time["t0"] or not at < time["t1"] or at < previous:
            bad(f"event {index}.at is outside or out of order")
        previous = float(at)
        if not _finite(event.get("source_second")) or event["source_second"] < 0:
            bad(f"event {index}.source_second is invalid")
        position = event.get("position")
        if not isinstance(position, dict) or any(
            not _finite(position.get(axis)) or position[axis] < -1 or position[axis] > 1
            for axis in ("x", "y", "z")
        ):
            bad(f"event {index}.position is invalid")
        if (
            not _finite(event.get("depth"))
            or event["depth"] < 0
            or event["depth"] > 1
            or not _finite(event.get("intensity"))
            or event["intensity"] < 0
            or event["intensity"] > 1
        ):
            bad(f"event {index} bounds are invalid")
        audio = event.get("audio")
        if (
            not isinstance(audio, dict)
            or set(audio) != {"role", "source_sha256", "pitch"}
            or not (audio.get("role") is None or isinstance(audio.get("role"), str) and audio["role"])
            or not (
                audio.get("source_sha256") is None
                or isinstance(audio.get("source_sha256"), str)
                and SHA256.fullmatch(audio["source_sha256"])
            )
            or not (
                audio.get("pitch") is None
                or type(audio.get("pitch")) is int
                and 0 <= audio["pitch"] <= 127
            )
        ):
            bad(f"event {index}.audio is invalid")
        if not isinstance(event.get("source"), dict):
            bad(f"event {index}.source is invalid")
        if event.get("passage") != passage:
            bad(f"event {index}.passage does not match identity")
        if "end" in event and (
            not _finite(event["end"]) or event["end"] < event["at"] or event["end"] > time["t1"] + 1e-6
        ):
            bad(f"event {index}.end is invalid")
        if "target_speaker" in event and (
            not isinstance(event["target_speaker"], str) or not event["target_speaker"]
        ):
            bad(f"event {index}.target_speaker is invalid")
    lookup = bus.get("lookup")
    buckets = lookup.get("buckets") if isinstance(lookup, dict) else None
    if (
        not isinstance(lookup, dict)
        or lookup.get("quantum_seconds") != 1
        or not isinstance(buckets, list)
        or len(buckets) != math.ceil(float(time["seconds"]))
    ):
        bad("lookup must contain one bucket per passage second")
    maximum = 0
    for index, bucket in enumerate(buckets):
        event_range = bucket.get("event_start") if isinstance(bucket, dict) else None
        expected = [
            _lower_bound(events, float(time["t0"]) + index),
            _lower_bound(events, min(float(time["t1"]), float(time["t0"]) + index + 1)),
        ]
        if event_range != expected:
            bad(f"lookup bucket {index}.event_start is stale")
        maximum = max(maximum, event_range[1] - event_range[0])
    if (lookup.get("maxima") or {}).get("event_starts_per_bucket") != maximum:
        bad("lookup maxima is stale")
    declared = (bus.get("identity") or {}).get("contract_sha256")
    if not isinstance(declared, str) or not SHA256.fullmatch(declared) or declared != room_contract_sha256(bus):
        bad("identity.contract_sha256 is stale")
    return bus


def load_room_bus(path: Path) -> dict[str, Any]:
    return validate_room_bus(json.loads(path.read_text()))


def room_events_between(bus: dict[str, Any], start: float, end: float) -> list[dict[str, Any]]:
    if not _finite(start) or not _finite(end) or end < start:
        raise ValueError("invalid room event interval")
    if start == end or end <= bus["time"]["t0"] or start >= bus["time"]["t1"]:
        return []
    clipped_start = max(start, bus["time"]["t0"])
    clipped_end = min(end, bus["time"]["t1"])
    first = max(0, math.floor(clipped_start - bus["time"]["t0"]) - 1)
    last = min(len(bus["lookup"]["buckets"]), math.ceil(clipped_end - bus["time"]["t0"]) + 1)
    indices: set[int] = set()
    for bucket_index in range(first, last):
        low, high = bus["lookup"]["buckets"][bucket_index]["event_start"]
        indices.update(range(low, high))
    return [
        bus["events"][index]
        for index in sorted(indices)
        if start <= bus["events"][index]["at"] < end
    ]


def validate_room_layouts(registry: Any) -> dict[str, Any]:
    def bad(message: str) -> None:
        raise ValueError(f"room layouts: {message}")

    if not isinstance(registry, dict) or registry.get("schema") != ROOM_LAYOUT_SCHEMA:
        bad(f"unknown schema {registry.get('schema') if isinstance(registry, dict) else None}")
    identity = registry.get("identity") or {}
    if (
        not isinstance(identity.get("id"), str)
        or not identity["id"]
        or identity.get("status") != "reference-simulation"
    ):
        bad("registry identity is invalid")
    declared = (registry.get("identity") or {}).get("contract_sha256")
    if not isinstance(declared, str) or not SHA256.fullmatch(declared) or declared != layout_contract_sha256(registry):
        bad("identity.contract_sha256 is stale")
    coordinate = registry.get("coordinate_system")
    if (
        not isinstance(coordinate, dict)
        or coordinate.get("units") != "normalized-room"
        or coordinate.get("axes")
        != {"x": "left-to-right", "y": "floor-to-ceiling", "z": "far-to-near"}
        or not isinstance(coordinate.get("listener"), list)
        or len(coordinate["listener"]) != 3
        or any(not _finite(value) or value < -1 or value > 1 for value in coordinate["listener"])
        or not _finite(coordinate.get("meters_per_unit"))
        or coordinate["meters_per_unit"] <= 0
    ):
        bad("coordinate system is invalid")
    safety = registry.get("safety")
    if (
        not isinstance(safety, dict)
        or not _finite(safety.get("max_event_gain"))
        or not 0 < safety["max_event_gain"] <= 1
        or not _finite(safety.get("limiter_ceiling_dbfs"))
        or safety["limiter_ceiling_dbfs"] > 0
        or not _finite(safety.get("latency_budget_ms"))
        or not 0 <= safety["latency_budget_ms"] <= 100
        or not _finite(safety.get("speed_of_sound_mps"))
        or not 300 <= safety["speed_of_sound_mps"] <= 400
    ):
        bad("safety limits are invalid")
    layouts = registry.get("layouts")
    if not isinstance(layouts, list) or len(layouts) < 2:
        bad("at least stereo and multichannel layouts are required")
    layout_ids: set[str] = set()
    for layout in layouts:
        layout_id = layout.get("id") if isinstance(layout, dict) else None
        if not isinstance(layout_id, str) or not layout_id or layout_id in layout_ids:
            bad("layout ids are missing or duplicated")
        layout_ids.add(layout_id)
        if layout.get("status") not in {"portable-fallback", "reference-simulation"}:
            bad(f"{layout_id}.status is invalid")
        speakers = layout.get("speakers")
        if not isinstance(speakers, list) or len(speakers) < 2:
            bad(f"{layout_id}.speakers is invalid")
        speaker_ids: set[str] = set()
        channels: list[int] = []
        for speaker in speakers:
            speaker_id = speaker.get("id") if isinstance(speaker, dict) else None
            if not isinstance(speaker_id, str) or not speaker_id or speaker_id in speaker_ids:
                bad(f"{layout_id} speaker ids are invalid")
            speaker_ids.add(speaker_id)
            channel = speaker.get("channel")
            position = speaker.get("position")
            channels.append(channel)
            if (
                type(channel) is not int
                or not isinstance(position, list)
                or len(position) != 3
                or any(not _finite(value) or value < -1 or value > 1 for value in position)
            ):
                bad(f"{layout_id}.{speaker_id} is invalid")
        if len(set(channels)) != len(channels) or sorted(channels) != list(range(len(channels))):
            bad(f"{layout_id} channels must be unique and contiguous from zero")
        fold = layout.get("stereo_fold_down")
        matrix = fold.get("matrix") if isinstance(fold, dict) else None
        if (
            not isinstance(fold, dict)
            or fold.get("outputs") != ["left", "right"]
            or not isinstance(matrix, list)
            or len(matrix) != len(speakers)
            or any(
                not isinstance(row, list)
                or len(row) != 2
                or any(not _finite(value) or value < -1 or value > 1 for value in row)
                or math.hypot(*row) == 0
                or math.hypot(*row) > 1 + 1e-12
                for row in matrix
            )
        ):
            bad(f"{layout_id} stereo fold-down is invalid")
    if registry.get("default_layout") not in layout_ids:
        bad("default_layout does not exist")
    return registry


def load_room_layouts(path: Path = DEFAULT_LAYOUTS) -> dict[str, Any]:
    return validate_room_layouts(json.loads(path.read_text()))


def room_layout(registry: dict[str, Any], layout_id: str | None = None) -> dict[str, Any]:
    wanted = layout_id or registry["default_layout"]
    for layout in registry["layouts"]:
        if layout["id"] == wanted:
            return layout
    raise ValueError(f"unknown room layout {wanted}")


def _distance(event: dict[str, Any], speaker: dict[str, Any], registry: dict[str, Any]) -> float:
    listener = registry["coordinate_system"]["listener"]
    meters = registry["coordinate_system"]["meters_per_unit"]
    axes = [event["position"][axis] for axis in ("x", "y", "z")]
    return math.hypot(
        *((value - speaker["position"][index] + listener[index]) * meters for index, value in enumerate(axes))
    )


def _route_event(event: dict[str, Any], registry: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    safety = registry["safety"]
    direct = None
    if event.get("target_speaker"):
        direct = next((speaker for speaker in layout["speakers"] if speaker["id"] == event["target_speaker"]), None)
        if direct is None:
            raise ValueError(f"event {event['id']} names unknown target speaker {event['target_speaker']}")
    distances = [_distance(event, speaker, registry) for speaker in layout["speakers"]]
    nearest = min(distances)
    weights = [
        (1.0 if speaker["id"] == direct["id"] else 0.0) if direct else 1.0 / max(0.25, distances[index])
        for index, speaker in enumerate(layout["speakers"])
    ]
    norm = math.hypot(*weights) or 1.0
    event_gain = min(event["intensity"], safety["max_event_gain"])
    multichannel = []
    for index, speaker in enumerate(layout["speakers"]):
        if weights[index] == 0:
            continue
        delay = 0.0 if direct else (distances[index] - nearest) / safety["speed_of_sound_mps"] * 1000
        if delay > safety["latency_budget_ms"] + 1e-9:
            raise ValueError(f"event {event['id']} exceeds latency budget at {speaker['id']}")
        multichannel.append(
            {
                "speaker": speaker["id"],
                "channel": speaker["channel"],
                "gain": _rounded(event_gain * weights[index] / norm, 12),
                "delay_ms": _rounded(delay, 9),
            }
        )
    stereo = []
    for tap in multichannel:
        speaker_index = next(
            index for index, speaker in enumerate(layout["speakers"]) if speaker["id"] == tap["speaker"]
        )
        row = layout["stereo_fold_down"]["matrix"][speaker_index]
        for channel in range(2):
            gain = tap["gain"] * row[channel]
            if gain != 0:
                stereo.append(
                    {
                        "output": layout["stereo_fold_down"]["outputs"][channel],
                        "channel": channel,
                        "source_speaker": tap["speaker"],
                        "gain": _rounded(gain, 12),
                        "delay_ms": tap["delay_ms"],
                    }
                )
    return {
        "event_index": event["index"],
        "id": event["id"],
        "type": event["type"],
        "at": event["at"],
        **({} if "end" not in event else {"end": event["end"]}),
        "passage": event["passage"],
        "audio": event["audio"],
        "multichannel": multichannel,
        "stereo": stereo,
    }


def plan_room_render(
    bus: dict[str, Any],
    registry: dict[str, Any],
    layout_id: str,
    start: float,
    end: float,
) -> dict[str, Any]:
    layout = room_layout(registry, layout_id)
    return {
        "schema": ROOM_PLAN_SCHEMA,
        "bus_contract_sha256": bus["identity"]["contract_sha256"],
        "layout_contract_sha256": registry["identity"]["contract_sha256"],
        "layout": layout["id"],
        "interval": {"start": start, "end": end},
        "safety": {**registry["safety"]},
        "events": [_route_event(event, registry, layout) for event in room_events_between(bus, start, end)],
    }


def _finish_bus(bus: dict[str, Any]) -> dict[str, Any]:
    bus["events"].sort(key=lambda event: (event["at"], EVENT_ORDER[event["type"]], event["id"]))
    for index, event in enumerate(bus["events"]):
        event["index"] = index
    bus["lookup"] = _lookup_rows(bus["events"], bus["time"]["t0"], bus["time"]["seconds"])
    bus["identity"]["contract_sha256"] = room_contract_sha256(bus)
    return validate_room_bus(bus)


def calibration_bus(
    registry: dict[str, Any],
    layout_id: str,
    *,
    t0: float = 0.0,
    spacing: float = 0.25,
) -> dict[str, Any]:
    layout = room_layout(registry, layout_id)
    if not _finite(t0) or t0 < 0 or not _finite(spacing) or spacing <= 0:
        raise ValueError("invalid calibration timing")
    seconds = max(1.0, len(layout["speakers"]) * spacing)
    passage: PassageIdentity = {
        "river_seed": 0,
        "stream": 0,
        "index": 0,
        "seed": 0,
        "t0": t0,
        "seconds": seconds,
    }
    events = []
    for index, speaker in enumerate(layout["speakers"]):
        x, y, z = speaker["position"]
        events.append(
            {
                "index": -1,
                "id": f"0:calibration.impulse:{index}",
                "type": "calibration.impulse",
                "at": t0 + index * spacing,
                "source_second": _rounded(index * spacing, 9),
                "position": {"x": x, "y": y, "z": z},
                "depth": _rounded((z + 1) / 2, 6),
                "intensity": 0.25,
                "passage": {**passage},
                "audio": {"role": "calibration-impulse", "source_sha256": None, "pitch": None},
                "source": {"kind": "diagnostic-only", "layout": layout["id"], "speaker": speaker["id"]},
                "target_speaker": speaker["id"],
            }
        )
    return _finish_bus(
        {
            "schema": ROOM_BUS_SCHEMA,
            "semantics": "authored-start-events",
            "release_status": "diagnostic-only",
            "identity": {"score_contract_sha256": None, "midi_sha256": None, "passage": passage},
            "time": {
                "basis": "absolute-river-seconds",
                "t0": t0,
                "t1": t0 + seconds,
                "seconds": seconds,
            },
            "provenance": {
                "policy": "declared-source-bytes-only",
                "score_work_id": None,
                "repertoire_entry_sha256": None,
                "layout_contract_sha256": registry["identity"]["contract_sha256"],
            },
            "events": events,
        }
    )

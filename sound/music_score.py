"""Python consumer for the immutable Danse musical-score contract.

This mirrors engine/score.js. Both use the compiled lookup buckets, so random
access is independent of elapsed river time and segment boundaries cannot change
the event plan.
"""

from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCORE = ROOT / "music" / "score.json"


def load_score(path: Path = DEFAULT_SCORE) -> dict[str, Any]:
    return validate(json.loads(path.read_text()))


def validate(score: Any) -> dict[str, Any]:
    if not isinstance(score, dict):
        raise ValueError("music score: root must be a mapping")
    if score.get("schema") != "danse.music.score.v1":
        raise ValueError(f"music score: unknown schema {score.get('schema')}")
    time = score.get("time")
    duration = time.get("duration_seconds") if isinstance(time, dict) else None
    if type(duration) not in (int, float) or not math.isfinite(float(duration)) or duration <= 0:
        raise ValueError("music score: duration must be finite and positive")
    for name in ("tempo", "meter", "beats", "phrases", "dynamics", "movements"):
        if not isinstance(score.get(name), list) or not score[name]:
            raise ValueError(f"music score: {name} must be non-empty")
    for name in ("cues", "notes", "orchestration"):
        if not isinstance(score.get(name), list):
            raise ValueError("music score: cues, notes, and orchestration must be arrays")

    lookup = score.get("lookup")
    buckets = lookup.get("buckets") if isinstance(lookup, dict) else None
    if (
        not isinstance(lookup, dict)
        or lookup.get("quantum_seconds") != 1
        or not isinstance(buckets, list)
        or len(buckets) != math.ceil(duration)
    ):
        raise ValueError("music score: lookup must contain one bucket per source second")

    state_rows = {
        "tempo": score["tempo"],
        "meter": score["meter"],
        "beat": score["beats"],
        "phrase": score["phrases"],
        "dynamic": score["dynamics"],
        "movement": score["movements"],
    }

    def valid_index(value: Any, rows: list[dict[str, Any]]) -> bool:
        return type(value) is int and 0 <= value < len(rows)

    for bucket_index, bucket in enumerate(buckets):
        if not isinstance(bucket, dict):
            raise ValueError(f"music score: lookup bucket {bucket_index} must be a mapping")
        for name, rows in state_rows.items():
            if not valid_index(bucket.get(name), rows):
                raise ValueError(f"music score: lookup bucket {bucket_index}.{name} is out of range")
        active_cues = bucket.get("active_cues")
        if not isinstance(active_cues, list) or any(not valid_index(index, score["cues"]) for index in active_cues):
            raise ValueError(f"music score: lookup bucket {bucket_index}.active_cues is malformed")
        note_start = bucket.get("note_start")
        if (
            not isinstance(note_start, list)
            or len(note_start) != 2
            or any(type(value) is not int for value in note_start)
            or note_start[0] < 0
            or note_start[0] > note_start[1]
            or note_start[1] > len(score["notes"])
        ):
            raise ValueError(f"music score: lookup bucket {bucket_index}.note_start is malformed")
        if type(bucket.get("recast")) is not int or bucket["recast"] < 0:
            raise ValueError(f"music score: lookup bucket {bucket_index}.recast is malformed")

    if score["movements"][0].get("start_second") != 0 or score["movements"][-1].get("end_second") != duration:
        raise ValueError("music score: movement bindings must tile the nominal score")
    cursor = 0.0
    for movement in score["movements"]:
        start_second = movement.get("start_second")
        end_second = movement.get("end_second")
        if (
            type(start_second) not in (int, float)
            or type(end_second) not in (int, float)
            or abs(float(start_second) - cursor) > 1e-6
            or not end_second > start_second
        ):
            raise ValueError(f"music score: movement {movement.get('id')} breaks the score partition")
        cursor = float(end_second)
    return score


def _mapped_time(score: dict[str, Any], absolute_second: float, window: dict[str, float] | None) -> tuple[float, int, float]:
    if not math.isfinite(absolute_second):
        raise ValueError("score time must be finite")
    duration = float(score["time"]["duration_seconds"])
    if window is not None:
        t0, seconds = float(window["t0"]), float(window["seconds"])
        if not math.isfinite(t0) or seconds <= 0:
            raise ValueError("score window must have finite t0 and positive seconds")
        phase = min(1.0, max(0.0, (absolute_second - t0) / seconds))
        return min(math.nextafter(duration, 0.0), phase * duration), 0, seconds / duration
    at = max(0.0, absolute_second)
    cycle = math.floor(at / duration)
    return min(math.nextafter(duration, 0.0), at - cycle * duration), cycle, 1.0


def _advance(rows: list[dict[str, Any]], index: int, source_second: float, key: str = "second") -> int:
    while index + 1 < len(rows) and float(rows[index + 1][key]) <= source_second:
        index += 1
    return index


def score_at(score: dict[str, Any], absolute_second: float, window: dict[str, float] | None = None) -> dict[str, Any]:
    source_second, cycle, scale = _mapped_time(score, absolute_second, window)
    bucket = score["lookup"]["buckets"][math.floor(source_second)]
    tempo = score["tempo"][_advance(score["tempo"], bucket["tempo"], source_second)]
    meter = score["meter"][_advance(score["meter"], bucket["meter"], source_second)]
    beat = score["beats"][_advance(score["beats"], bucket["beat"], source_second)]
    phrase = score["phrases"][_advance(score["phrases"], bucket["phrase"], source_second, "start_second")]
    dynamic = score["dynamics"][_advance(score["dynamics"], bucket["dynamic"], source_second)]
    movement = score["movements"][_advance(score["movements"], bucket["movement"], source_second, "start_second")]
    bucket_cues = [score["cues"][index] for index in bucket["active_cues"]]
    cues = [
        cue
        for cue in bucket_cues
        if float(cue["second"]) <= source_second < float(cue["end_second"])
    ]
    offsets: dict[str, float] = {}
    hold = False
    recast = int(bucket["recast"])
    for cue in bucket_cues:
        if cue["visual"]["recast"] and float(cue["second"]) <= source_second:
            recast = max(recast, int(cue["visual"]["recast_index"]))
    for cue in cues:
        hold = hold or bool(cue["visual"]["hold"])
        for channel, value in cue["visual"]["channel_offsets"].items():
            offsets[channel] = offsets.get(channel, 0.0) + float(value) * float(cue["strength"])
    next_beat = score["beats"][beat["index"] + 1] if beat["index"] + 1 < len(score["beats"]) else None
    beat_span = (float(next_beat["second"]) - float(beat["second"])) if next_beat else 60.0 / float(tempo["bpm"])
    return {
        "identity": score["identity"]["contract_sha256"],
        "absolute_second": absolute_second,
        "source_second": source_second,
        "cycle": cycle,
        "scale": scale,
        "tempo": {**tempo, "effective_bpm": float(tempo["bpm"]) / scale},
        "meter": meter,
        "beat": {**beat, "phase": min(1.0, max(0.0, (source_second - float(beat["second"])) / beat_span))},
        "phrase": phrase,
        "dynamic": dynamic,
        "movement": {
            **movement,
            "u": min(
                1.0,
                max(
                    0.0,
                    (source_second - float(movement["start_second"]))
                    / (float(movement["end_second"]) - float(movement["start_second"])),
                ),
            ),
        },
        "cues": cues,
        "visual": {"hold": hold, "recast": recast, "channel_offsets": offsets},
    }


def _mapped_event(event: dict[str, Any], t0: float, scale: float) -> dict[str, Any]:
    source_start = float(event.get("start_second", event.get("second")))
    source_end = float(event.get("end_second", source_start))
    return {**event, "at": t0 + source_start * scale, "end": t0 + source_end * scale}


def _indexed_events(
    score: dict[str, Any], source_start: float, source_end: float
) -> tuple[set[int], set[int]]:
    """Return lookup-referenced starts, conservatively padded for affine rounding."""
    buckets = score["lookup"]["buckets"]
    first = max(0, math.floor(source_start) - 1)
    last_exclusive = min(len(buckets), math.ceil(source_end) + 1)
    cue_indices: set[int] = set()
    note_indices: set[int] = set()
    for bucket_index in range(first, last_exclusive):
        bucket = buckets[bucket_index]
        cue_indices.update(bucket["active_cues"])
        note_indices.update(range(bucket["note_start"][0], bucket["note_start"][1]))
    return cue_indices, note_indices


def events_between(
    score: dict[str, Any],
    start: float,
    end: float,
    window: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Return authored note/cue starts in the half-open absolute interval."""
    if not math.isfinite(start) or not math.isfinite(end) or end < start:
        raise ValueError("invalid score event interval")
    if end == start:
        return []
    duration = float(score["time"]["duration_seconds"])
    windows: list[tuple[float, float, float]] = []
    if window is not None:
        t0, seconds = float(window["t0"]), float(window["seconds"])
        if not math.isfinite(t0) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("score window must have finite t0 and positive seconds")
        windows.append((t0, seconds, seconds / duration))
    else:
        first = math.floor(max(0.0, start) / duration)
        last = math.ceil(max(0.0, end) / duration) - 1
        if last - first + 1 > 10_000:
            raise ValueError("score event interval exceeds 10,000 cycles")
        windows.extend((cycle * duration, duration, 1.0) for cycle in range(first, last + 1))
    result = []
    for t0, seconds, scale in windows:
        window_end = t0 + seconds
        if window_end <= start or t0 >= end:
            continue
        clipped_start = max(start, t0)
        clipped_end = min(end, window_end)
        if not clipped_end > clipped_start:
            continue
        source_start = (clipped_start - t0) / scale
        source_end = (clipped_end - t0) / scale
        cue_indices, note_indices = _indexed_events(score, source_start, source_end)
        for event_type, rows, indices in (
            ("cue", score["cues"], cue_indices),
            ("note", score["notes"], note_indices),
        ):
            for index in indices:
                event = _mapped_event(rows[index], t0, scale)
                if start <= event["at"] < end:
                    result.append({"type": event_type, **event})
    return sorted(result, key=lambda event: (event["at"], event["type"], event["index"]))

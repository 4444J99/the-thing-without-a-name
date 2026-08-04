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


def validate(score: dict[str, Any]) -> dict[str, Any]:
    if score.get("schema") != "danse.music.score.v1":
        raise ValueError(f"music score: unknown schema {score.get('schema')}")
    duration = (score.get("time") or {}).get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError("music score: duration must be positive")
    buckets = (score.get("lookup") or {}).get("buckets")
    if score["lookup"].get("quantum_seconds") != 1 or not isinstance(buckets, list) or len(buckets) != math.ceil(duration):
        raise ValueError("music score: lookup must contain one bucket per source second")
    for name in ("tempo", "meter", "beats", "phrases", "dynamics", "movements"):
        if not isinstance(score.get(name), list) or not score[name]:
            raise ValueError(f"music score: {name} must be non-empty")
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
    cues = [
        score["cues"][index]
        for index in bucket["active_cues"]
        if float(score["cues"][index]["second"]) <= source_second < float(score["cues"][index]["end_second"])
    ]
    offsets: dict[str, float] = {}
    hold = False
    recast = int(bucket["recast"])
    for cue in cues:
        hold = hold or bool(cue["visual"]["hold"])
        if cue["visual"]["recast"]:
            recast = max(recast, int(cue["visual"]["recast_index"]))
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


def events_between(
    score: dict[str, Any],
    start: float,
    end: float,
    window: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    if not math.isfinite(start) or not math.isfinite(end) or end < start:
        raise ValueError("invalid score event interval")
    duration = float(score["time"]["duration_seconds"])
    windows: list[tuple[float, float, float]] = []
    if window is not None:
        seconds = float(window["seconds"])
        if seconds <= 0:
            raise ValueError("score window seconds must be positive")
        windows.append((float(window["t0"]), seconds, seconds / duration))
    else:
        first = math.floor(max(0.0, start) / duration)
        last = math.floor(max(0.0, max(start, math.nextafter(end, -math.inf))) / duration)
        if last - first > 10_000:
            raise ValueError("score event interval exceeds 10,000 cycles")
        windows.extend((cycle * duration, duration, 1.0) for cycle in range(first, last + 1))
    result = []
    for t0, seconds, scale in windows:
        if t0 + seconds <= start or t0 >= end:
            continue
        for event_type, rows in (("cue", score["cues"]), ("note", score["notes"])):
            for row in rows:
                event = _mapped_event(row, t0, scale)
                if start <= event["at"] < end:
                    result.append({"type": event_type, **event})
    return sorted(result, key=lambda event: (event["at"], event["type"], event["index"]))

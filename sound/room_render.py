#!/usr/bin/env python3
"""Emit deterministic offline stereo or multichannel room render instructions.

This planner never opens a recording and never claims hardware was present. It
consumes the exact passage buses emitted by ``control.mjs`` and preserves each
event's declared source digest for the later byte-owning renderer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from room_events import (  # noqa: E402
    ROOM_PLAN_SCHEMA,
    load_room_layouts,
    plan_room_render,
    room_layout,
    validate_room_bus,
)


def validate_control_room(control: Any, registry: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(control, dict):
        raise ValueError("control track must be a mapping")
    room = control.get("room")
    if not isinstance(room, dict) or room.get("schema") != "danse.room.control.v1":
        raise ValueError("control track has no danse.room.control.v1 payload")
    if room.get("semantics") != "authored-start-events":
        raise ValueError("control room semantics are not authored-start-events")
    if (room.get("layout_identity") or {}).get("contract_sha256") != registry["identity"]["contract_sha256"]:
        raise ValueError("control layout identity does not match the supplied registry")
    buses = room.get("buses")
    if not isinstance(buses, list) or not buses:
        raise ValueError("control room buses must be non-empty")
    validated = [validate_room_bus(bus) for bus in buses]
    if len({bus["identity"]["contract_sha256"] for bus in validated}) != len(validated):
        raise ValueError("control room buses are duplicated")
    if any(
        left["time"]["t1"] > right["time"]["t0"] + 1e-6
        for left, right in zip(validated, validated[1:])
    ):
        raise ValueError("control room buses overlap or are out of order")
    return validated


def plan_control(
    control: dict[str, Any],
    registry: dict[str, Any],
    layout_id: str,
    output: str,
    *,
    require_cleared: bool = False,
) -> dict[str, Any]:
    if output not in {"stereo", "multichannel"}:
        raise ValueError(f"unknown room output {output}")
    layout = room_layout(registry, layout_id)
    buses = validate_control_room(control, registry)
    start = float(control["t0"])
    end = float(control["t1"])
    events = []
    for bus in buses:
        plan = plan_room_render(bus, registry, layout_id, start, end)
        for event in plan["events"]:
            events.append(
                {
                    "bus_contract_sha256": bus["identity"]["contract_sha256"],
                    "event_index": event["event_index"],
                    "id": event["id"],
                    "type": event["type"],
                    "at": event["at"],
                    **({} if "end" not in event else {"end": event["end"]}),
                    "passage": event["passage"],
                    "audio": event["audio"],
                    "taps": event[output],
                }
            )
    events.sort(key=lambda event: (event["at"], event["bus_contract_sha256"], event["event_index"]))
    blocked = [event["id"] for event in events if event["audio"]["role"] and not event["audio"]["source_sha256"]]
    if require_cleared and blocked:
        raise ValueError("room render is blocked by undeclared audio source bytes: " + ", ".join(blocked))
    return {
        "schema": ROOM_PLAN_SCHEMA,
        "kind": "offline-render-instructions",
        "output": output,
        "control": {
            "seed": control["seed"],
            "stream": control["stream"],
            "t0": control["t0"],
            "t1": control["t1"],
        },
        "layout_contract_sha256": registry["identity"]["contract_sha256"],
        "layout": layout["id"],
        "channels": (
            [speaker["id"] for speaker in layout["speakers"]]
            if output == "multichannel"
            else layout["stereo_fold_down"]["outputs"]
        ),
        "safety": registry["safety"],
        "bus_contract_sha256": [bus["identity"]["contract_sha256"] for bus in buses],
        "events": events,
        "clearance": {
            "policy": "declared-source-bytes-only",
            "silent_events": sum(event["audio"]["role"] is None for event in events),
            "declared_events": sum(bool(event["audio"]["source_sha256"]) for event in events),
            "blocked_events": len(blocked),
        },
    }


def read_json(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", help="control JSON path, or - for stdin")
    parser.add_argument("--layouts", type=Path, default=HERE / "room-layout.json")
    parser.add_argument("--layout", default="stereo")
    parser.add_argument("--output", choices=("stereo", "multichannel"), default="stereo")
    parser.add_argument("--require-cleared", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        plan = plan_control(
            read_json(args.control),
            load_room_layouts(args.layouts),
            args.layout,
            args.output,
            require_cleared=args.require_cleared,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    text = json.dumps(plan, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

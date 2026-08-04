#!/usr/bin/env python3
"""Portable cross-language regressions for the deterministic room-event bus."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sound"))

from room_events import (  # noqa: E402
    calibration_bus,
    layout_contract_sha256,
    load_room_layouts,
    plan_room_render,
    room_contract_sha256,
    room_events_between,
    validate_room_bus,
    validate_room_layouts,
)
from room_render import plan_control, validate_control_room  # noqa: E402
from score import room_event_plan  # noqa: E402

PASSAGE = {
    "river_seed": 0x12345678,
    "stream": 7,
    "index": 0,
    "seed": 0x9ABCDEF0,
    "t0": 17.25,
    "seconds": 312.54,
}

COMPILE_SCRIPT = f"""
  import fs from 'node:fs';
  import {{ compileRoomBus, planRoomRender, validateRoomLayouts }} from './engine/room-events.js';
  const score = JSON.parse(fs.readFileSync('music/score.json'));
  const layouts = validateRoomLayouts(JSON.parse(fs.readFileSync('sound/room-layout.json')));
  const passage = {json.dumps(PASSAGE)};
  const bus = compileRoomBus(score, passage);
  const plan = planRoomRender(bus, layouts, 'reference-quad', passage.t0, passage.t0 + passage.seconds);
  console.log(JSON.stringify({{bus, plan}}));
"""


def run(*command: str, input_text: str | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def node_json(script: str) -> object:
    result = run("node", "--input-type=module", "--eval", script)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


class RoomEventContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiled = node_json(COMPILE_SCRIPT)
        cls.bus = validate_room_bus(compiled["bus"])
        cls.node_plan = compiled["plan"]
        cls.layouts = load_room_layouts()

    def test_declared_schemas_and_cross_language_contract_identities_hold(self) -> None:
        layout_schema = json.loads((ROOT / "sound/room-layout.schema.json").read_text())
        event_schema = json.loads((ROOT / "sound/room-events.schema.json").read_text())
        self.assertEqual(layout_schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(layout_schema["properties"]["schema"]["const"], "danse.room.layouts.v1")
        self.assertFalse(layout_schema["additionalProperties"])
        self.assertEqual(event_schema["properties"]["schema"]["const"], "danse.room.events.v1")
        self.assertEqual(event_schema["properties"]["semantics"]["const"], "authored-start-events")
        self.assertFalse(event_schema["additionalProperties"])
        self.assertEqual(
            self.layouts["identity"]["contract_sha256"],
            layout_contract_sha256(self.layouts),
        )
        self.assertEqual(
            self.bus["identity"]["contract_sha256"],
            room_contract_sha256(self.bus),
        )
        self.assertEqual(self.node_plan, plan_room_render(
            self.bus,
            self.layouts,
            "reference-quad",
            PASSAGE["t0"],
            PASSAGE["t0"] + PASSAGE["seconds"],
        ))

    def test_compilation_is_repeatable_typed_bounded_and_provenance_safe(self) -> None:
        repeated = node_json(COMPILE_SCRIPT)
        self.assertEqual(repeated["bus"], self.bus)
        self.assertEqual(len(self.bus["lookup"]["buckets"]), 313)
        self.assertEqual(len(self.bus["events"]), 38)
        self.assertEqual(len({event["id"] for event in self.bus["events"]}), len(self.bus["events"]))
        types = {event["type"] for event in self.bus["events"]}
        self.assertTrue({"passage.start", "movement.start", "plane.assembly", "score.cue", "plane.recast", "score.note"} <= types)
        self.assertTrue(all(-1 <= event["position"][axis] <= 1 for event in self.bus["events"] for axis in ("x", "y", "z")))
        self.assertTrue(all(0 <= event["depth"] <= 1 and 0 <= event["intensity"] <= 1 for event in self.bus["events"]))
        serialized = json.dumps(self.bus)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn(".work", serialized)
        self.assertEqual(self.bus["provenance"]["policy"], "declared-source-bytes-only")

    def test_js_and_python_interval_queries_and_routes_are_value_identical(self) -> None:
        intervals = [
            [PASSAGE["t0"], PASSAGE["t0"] + 1],
            [PASSAGE["t0"] + 95.25, PASSAGE["t0"] + 128.75],
            [PASSAGE["t0"] + PASSAGE["seconds"] - 5, PASSAGE["t0"] + PASSAGE["seconds"]],
            [PASSAGE["t0"] + 30, PASSAGE["t0"] + 30],
        ]
        script = f"""
          import fs from 'node:fs';
          import {{ compileRoomBus, planRoomRender, roomEventsBetween, validateRoomLayouts }} from './engine/room-events.js';
          const score = JSON.parse(fs.readFileSync('music/score.json'));
          const layouts = validateRoomLayouts(JSON.parse(fs.readFileSync('sound/room-layout.json')));
          const passage = {json.dumps(PASSAGE)};
          const bus = compileRoomBus(score, passage);
          const intervals = {json.dumps(intervals)};
          console.log(JSON.stringify(intervals.map(([start, end]) => ({{
            events: roomEventsBetween(bus, start, end),
            plan: planRoomRender(bus, layouts, 'reference-quad', start, end),
          }}))));
        """
        observed = node_json(script)
        expected = [
            {
                "events": room_events_between(self.bus, start, end),
                "plan": plan_room_render(self.bus, self.layouts, "reference-quad", start, end),
            }
            for start, end in intervals
        ]
        self.assertEqual(observed, expected)

    def test_seek_segment_concat_restart_and_start_only_semantics_hold(self) -> None:
        start = PASSAGE["t0"]
        end = start + PASSAGE["seconds"]
        full = room_events_between(self.bus, start, end)
        edges = [start, start + 61.75, start + 128.0, start + 230.2, end]
        concatenated = [
            event
            for left, right in zip(edges, edges[1:])
            for event in room_events_between(self.bus, left, right)
        ]
        self.assertEqual(concatenated, full)
        seek = start + 128.125
        self.assertEqual(room_events_between(self.bus, seek, end), [event for event in full if event["at"] >= seek])

        note = next(event for event in full if event["type"] == "score.note" and event["end"] > event["at"] + 0.01)
        overlap_only = room_events_between(self.bus, note["at"] + 0.001, min(note["end"], note["at"] + 0.01))
        self.assertNotIn(note["id"], {event["id"] for event in overlap_only})

        shifted = {**PASSAGE, "t0": 900.0, "seconds": 425.0}
        script = f"""
          import fs from 'node:fs';
          import {{ compileRoomBus }} from './engine/room-events.js';
          const score = JSON.parse(fs.readFileSync('music/score.json'));
          const first = compileRoomBus(score, {json.dumps(PASSAGE)});
          const repeated = compileRoomBus(score, {json.dumps(PASSAGE)});
          const shifted = compileRoomBus(score, {json.dumps(shifted)});
          console.log(JSON.stringify({{first, repeated, shifted}}));
        """
        buses = node_json(script)
        self.assertEqual(buses["first"], buses["repeated"])
        compact = lambda bus: [
            (event["type"], event["source_second"], event["position"], event["intensity"])
            for event in bus["events"]
        ]
        self.assertEqual(compact(buses["first"]), compact(buses["shifted"]))

    def test_queries_use_bucket_indices_without_scanning_complete_event_arrays(self) -> None:
        class IndexedOnly(list):
            def __iter__(self):
                raise AssertionError("room event query scanned the complete event array")

        guarded = copy.deepcopy(self.bus)
        guarded["events"] = IndexedOnly(guarded["events"])
        target = next(event for event in self.bus["events"] if event["type"] == "score.cue")
        query_start = target["at"]
        query_end = target["at"] + 0.001
        got = room_events_between(guarded, query_start, query_end)
        self.assertTrue(got)

        script = f"""
          import fs from 'node:fs';
          import {{ compileRoomBus, roomEventsBetween }} from './engine/room-events.js';
          const score = JSON.parse(fs.readFileSync('music/score.json'));
          const bus = compileRoomBus(score, {json.dumps(PASSAGE)});
          let reads = 0;
          bus.events = new Proxy(bus.events, {{
            get(target, property, receiver) {{
              if (property === Symbol.iterator) throw new Error('room event query scanned the complete event array');
              if (typeof property === 'string' && /^\\d+$/.test(property)) reads += 1;
              return Reflect.get(target, property, receiver);
            }},
          }});
          const events = roomEventsBetween(bus, {target['at']}, {target['at'] + 0.001});
          console.log(JSON.stringify({{ids: events.map((event) => event.id), reads}}));
        """
        observed = node_json(script)
        self.assertEqual(observed["ids"], [event["id"] for event in got])
        self.assertEqual(observed["reads"], len(got))

    def test_speaker_map_fold_down_gain_latency_and_malformed_maps_fail_closed(self) -> None:
        plan = plan_room_render(
            self.bus,
            self.layouts,
            "reference-quad",
            PASSAGE["t0"],
            PASSAGE["t0"] + PASSAGE["seconds"],
        )
        layout = next(row for row in self.layouts["layouts"] if row["id"] == "reference-quad")
        for event in plan["events"]:
            self.assertTrue(all(0 <= tap["delay_ms"] <= self.layouts["safety"]["latency_budget_ms"] for tap in event["multichannel"]))
            self.assertLessEqual(math_hypot(tap["gain"] for tap in event["multichannel"]), self.layouts["safety"]["max_event_gain"] + 1e-9)
            expected = []
            for tap in event["multichannel"]:
                for channel, coefficient in enumerate(layout["stereo_fold_down"]["matrix"][tap["channel"]]):
                    if coefficient:
                        expected.append((channel, tap["speaker"], round_js(tap["gain"] * coefficient, 12), tap["delay_ms"]))
            actual = [(tap["channel"], tap["source_speaker"], tap["gain"], tap["delay_ms"]) for tap in event["stereo"]]
            self.assertEqual(actual, expected)

        stale = copy.deepcopy(self.layouts)
        stale["safety"]["max_event_gain"] = 0.5
        with self.assertRaisesRegex(ValueError, "contract_sha256 is stale"):
            validate_room_layouts(stale)

        duplicate = copy.deepcopy(self.layouts)
        duplicate["layouts"][1]["speakers"][1]["channel"] = 0
        duplicate["identity"]["contract_sha256"] = layout_contract_sha256(duplicate)
        with self.assertRaisesRegex(ValueError, "channels must be unique"):
            validate_room_layouts(duplicate)

        empty_speaker = copy.deepcopy(self.layouts)
        empty_speaker["layouts"][1]["speakers"][0]["id"] = ""
        empty_speaker["identity"]["contract_sha256"] = layout_contract_sha256(empty_speaker)
        with self.assertRaisesRegex(ValueError, "speaker ids are invalid"):
            validate_room_layouts(empty_speaker)

        bad_axes = copy.deepcopy(self.layouts)
        bad_axes["coordinate_system"]["axes"]["z"] = "near-to-far"
        bad_axes["identity"]["contract_sha256"] = layout_contract_sha256(bad_axes)
        with self.assertRaisesRegex(ValueError, "coordinate system is invalid"):
            validate_room_layouts(bad_axes)

        amplified_fold = copy.deepcopy(self.layouts)
        amplified_fold["layouts"][1]["stereo_fold_down"]["matrix"][0] = [1, 1]
        amplified_fold["identity"]["contract_sha256"] = layout_contract_sha256(amplified_fold)
        with self.assertRaisesRegex(ValueError, "stereo fold-down is invalid"):
            validate_room_layouts(amplified_fold)

        excessive = copy.deepcopy(self.layouts)
        excessive["coordinate_system"]["meters_per_unit"] = 100
        excessive["identity"]["contract_sha256"] = layout_contract_sha256(excessive)
        validate_room_layouts(excessive)
        with self.assertRaisesRegex(ValueError, "exceeds latency budget"):
            plan_room_render(self.bus, excessive, "reference-quad", PASSAGE["t0"], PASSAGE["t0"] + 1)

    def test_calibration_is_diagnostic_direct_and_cross_language_identical(self) -> None:
        python_bus = calibration_bus(self.layouts, "reference-quad")
        script = """
          import fs from 'node:fs';
          import { calibrationBus, planRoomRender, validateRoomLayouts } from './engine/room-events.js';
          const layouts = validateRoomLayouts(JSON.parse(fs.readFileSync('sound/room-layout.json')));
          const bus = calibrationBus(layouts, 'reference-quad');
          console.log(JSON.stringify({bus, plan: planRoomRender(bus, layouts, 'reference-quad', 0, 1)}));
        """
        observed = node_json(script)
        self.assertEqual(observed["bus"], python_bus)
        self.assertEqual(observed["plan"], plan_room_render(python_bus, self.layouts, "reference-quad", 0, 1))
        self.assertEqual(python_bus["release_status"], "diagnostic-only")
        self.assertEqual(len(python_bus["events"]), 4)
        for event in observed["plan"]["events"]:
            self.assertEqual(len(event["multichannel"]), 1)
            target = python_bus["events"][event["event_index"]]["target_speaker"]
            self.assertEqual(event["multichannel"][0]["speaker"], target)
            self.assertEqual(event["multichannel"][0]["delay_ms"], 0)

    def test_webaudio_disabled_and_uncleared_paths_do_not_touch_audio_nodes(self) -> None:
        script = f"""
          import fs from 'node:fs';
          import {{ compileRoomBus, planRoomRender, roomContractSha256, validateRoomLayouts }} from './engine/room-events.js';
          import {{ scheduleRoomWebAudio }} from './sound/web_audio.mjs';
          const score = JSON.parse(fs.readFileSync('music/score.json'));
          const layouts = validateRoomLayouts(JSON.parse(fs.readFileSync('sound/room-layout.json')));
          const bus = compileRoomBus(score, {json.dumps(PASSAGE)});
          const start = bus.time.t0;
          const end = bus.time.t1;
          const forbidden = new Proxy({{}}, {{ get() {{ throw new Error('audio-disabled path touched context'); }} }});
          const disabled = scheduleRoomWebAudio(forbidden, bus, layouts, 'reference-quad', {{}}, start, end, {{enabled:false}});
          const direct = planRoomRender(bus, layouts, 'reference-quad', start, end);

          let nodeCalls = 0;
          const node = () => ({{ connect() {{ nodeCalls += 1; }} }});
          const context = {{
            currentTime: 0,
            destination: {{}},
            createChannelMerger() {{ nodeCalls += 1; return node(); }},
            createBufferSource() {{ nodeCalls += 1; return {{...node(), buffer:null, start() {{}}, stop() {{}}}}; }},
            createDelay() {{ nodeCalls += 1; return {{...node(), delayTime:{{value:0}}}}; }},
            createGain() {{ nodeCalls += 1; return {{...node(), gain:{{value:0}}}}; }},
          }};
          const blocked = scheduleRoomWebAudio(context, bus, layouts, 'reference-quad', {{}}, start, end);
          const callsAfterBlocked = nodeCalls;

          const admittedBus = JSON.parse(JSON.stringify(bus));
          const admittedEvent = admittedBus.events.find((event) => event.type === 'score.note');
          const digest = 'a'.repeat(64);
          admittedEvent.audio.source_sha256 = digest;
          admittedBus.identity.contract_sha256 = roomContractSha256(admittedBus);
          const admitted = scheduleRoomWebAudio(
            context,
            admittedBus,
            layouts,
            'reference-quad',
            {{[admittedEvent.audio.role]: {{buffer: {{duration: 1}}, audio_source_sha256: digest}}}},
            admittedEvent.at,
            admittedEvent.at + 0.0001,
          );
          console.log(JSON.stringify({{
            disabledPlanMatches: JSON.stringify(disabled.plan) === JSON.stringify(direct),
            disabledEvents: disabled.disabled.length,
            blocked: blocked.blocked.length,
            silent: blocked.silent.length,
            callsAfterBlocked,
            scheduled: admitted.scheduled.length,
            nodeCalls,
          }}));
        """
        observed = node_json(script)
        self.assertTrue(observed["disabledPlanMatches"])
        self.assertEqual(observed["disabledEvents"], len(self.bus["events"]))
        self.assertGreater(observed["blocked"], 0)
        self.assertGreater(observed["silent"], 0)
        self.assertEqual(observed["callsAfterBlocked"], 0)
        self.assertEqual(observed["scheduled"], 1)
        self.assertGreater(observed["nodeCalls"], 0)

    def test_control_live_stereo_offline_and_multichannel_share_exact_bus_events(self) -> None:
        result = run("node", "sound/control.mjs", "--rate", "0", "--score", "music/score.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        control = json.loads(result.stdout)
        buses = validate_control_room(control, self.layouts)
        self.assertEqual(control["room"]["semantics"], "authored-start-events")
        self.assertEqual(control["room"]["layout_registry_path"], "sound/room-layout.json")
        self.assertNotIn(str(ROOT), json.dumps(control["room"]))
        stereo = plan_control(control, self.layouts, "reference-quad", "stereo")
        multichannel = plan_control(control, self.layouts, "reference-quad", "multichannel")
        self.assertEqual([event["id"] for event in stereo["events"]], [event["id"] for event in multichannel["events"]])
        self.assertTrue(all(event["taps"] for event in stereo["events"]))
        self.assertTrue(all(event["taps"] for event in multichannel["events"]))
        self.assertEqual(stereo["bus_contract_sha256"], [bus["identity"]["contract_sha256"] for bus in buses])
        self.assertEqual(room_event_plan(control, "reference-quad", "multichannel"), multichannel)
        with self.assertRaisesRegex(ValueError, "undeclared audio source bytes"):
            plan_control(control, self.layouts, "reference-quad", "stereo", require_cleared=True)

    def test_tampered_bus_and_control_identity_fail_before_rendering(self) -> None:
        stale = copy.deepcopy(self.bus)
        stale["events"][0]["intensity"] = 0.1
        with self.assertRaisesRegex(ValueError, "contract_sha256 is stale"):
            validate_room_bus(stale)

        malformed = copy.deepcopy(self.bus)
        malformed["events"][0]["position"]["x"] = 2
        malformed["identity"]["contract_sha256"] = room_contract_sha256(malformed)
        with self.assertRaisesRegex(ValueError, "position is invalid"):
            validate_room_bus(malformed)

        control = {
            "t0": PASSAGE["t0"],
            "t1": PASSAGE["t0"] + PASSAGE["seconds"],
            "seed": PASSAGE["river_seed"],
            "stream": PASSAGE["stream"],
            "room": {
                "schema": "danse.room.control.v1",
                "semantics": "authored-start-events",
                "layout_identity": {"contract_sha256": "0" * 64},
                "buses": [self.bus],
            },
        }
        with self.assertRaisesRegex(ValueError, "layout identity"):
            validate_control_room(control, self.layouts)


def round_js(value: float, places: int) -> float:
    import math

    scale = 10**places
    return math.floor(value * scale + 0.5) / scale


def math_hypot(values) -> float:
    import math

    return math.hypot(*values)


if __name__ == "__main__":
    unittest.main()

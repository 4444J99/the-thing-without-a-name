#!/usr/bin/env python3
"""Portable fixture-level regressions for Danse Music I/II contracts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "music"))
sys.path.insert(0, str(ROOT / "sound"))
sys.path.insert(0, str(ROOT / "render"))
sys.path.insert(0, str(ROOT / "pipeline"))

from compile_score import canonical_sha256, compile_contract, output_bytes  # noqa: E402
from music_score import events_between, load_score, score_at, validate as validate_score  # noqa: E402
from validate_repertoire import load_register, sha256, validate_document  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)


def compact(state: dict) -> dict:
    return {
        "source": round(state["source_second"], 8),
        "scale": round(state["scale"], 8),
        "tempo": round(state["tempo"]["effective_bpm"], 8),
        "beat": state["beat"]["index"],
        "downbeat": state["beat"]["downbeat"],
        "beat_phase": round(state["beat"]["phase"], 8),
        "phrase": state["phrase"]["id"],
        "dynamic": state["dynamic"]["midi_expression"],
        "movement": state["movement"]["id"],
        "movement_u": round(state["movement"]["u"], 8),
        "cues": [cue["id"] for cue in state["cues"]],
        "visual": state["visual"],
    }


def compact_events(events: list[dict]) -> list[dict]:
    return [
        {
            "type": event["type"],
            "index": event["index"],
            "name": event.get("id", event.get("stem")),
            "at": round(event["at"], 8),
            "end": round(event["end"], 8),
        }
        for event in events
    ]


class MusicScoreContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.score = load_score()
        cls.register = load_register()
        cls.program = json.loads((ROOT / "render/program.json").read_text())

    def test_fixture_register_compiler_and_all_tracked_digests_are_current(self) -> None:
        commands = (
            (sys.executable, "music/generate_fixture_midi.py", "--check"),
            (sys.executable, "music/compile_score.py", "--check"),
            (sys.executable, "music/validate_repertoire.py"),
        )
        for command in commands:
            with self.subTest(command=command):
                result = run(*command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.score["release_status"], "fixture-only")
        self.assertEqual(self.score["artistic_gate"]["status"], "pending")
        self.assertEqual(self.register["works"][0]["selection"]["status"], "not-selected")
        self.assertEqual(self.score["identity"]["midi_sha256"], sha256(ROOT / "music/fixtures/generated-study.mid"))
        identity_source = copy.deepcopy(self.score)
        declared_contract = identity_source["identity"].pop("contract_sha256")
        self.assertEqual(declared_contract, canonical_sha256(identity_source))
        schema = json.loads((ROOT / "music/score.schema.json").read_text())
        self.assertEqual(schema["properties"]["schema"]["const"], "danse.music.score.v1")
        self.assertEqual(
            self.register["works"][0]["derived_artifacts"][0]["sha256"],
            sha256(ROOT / "music/score.json"),
        )

    def test_compilation_is_byte_deterministic(self) -> None:
        first = output_bytes(compile_contract(copy.deepcopy(self.register), self.program, "generated-contract-study"))
        second = output_bytes(compile_contract(copy.deepcopy(self.register), self.program, "generated-contract-study"))
        self.assertEqual(first, second)
        self.assertEqual(first, (ROOT / "music/score.json").read_bytes())

    def test_python_validator_reports_missing_and_malformed_lookup_as_value_errors(self) -> None:
        malformed = []
        for value in (None, [], {}, {"quantum_seconds": 1}, {"quantum_seconds": 1, "buckets": None}):
            candidate = copy.deepcopy(self.score)
            if value is None:
                candidate.pop("lookup")
            else:
                candidate["lookup"] = value
            malformed.append(candidate)
        bad_bucket = copy.deepcopy(self.score)
        bad_bucket["lookup"]["buckets"][128]["note_start"] = [3, "4"]
        malformed.append(bad_bucket)

        for candidate in malformed:
            with self.subTest(lookup=candidate.get("lookup")):
                with self.assertRaisesRegex(ValueError, r"^music score: lookup"):
                    validate_score(candidate)

    def test_public_domain_composition_does_not_clear_a_nonfree_recording(self) -> None:
        false_equivalence = copy.deepcopy(self.register)
        false_equivalence["artistic_gate"] |= {"status": "accepted", "evidence": "validator fixture"}
        work = false_equivalence["works"][0]
        work["role"] = "repertoire"
        work["selection"] |= {"status": "selected", "evidence": "validator fixture"}
        work["composition"]["status"] = "public-domain"
        work["edition"]["status"] = "not-applicable"
        work["arrangement_midi"]["status"] = "project-authored"
        work["performance"]["status"] = "project-authored"
        work["recording"]["status"] = "restricted"
        errors = validate_document(false_equivalence, check_derived=False)
        self.assertTrue(
            any("public-domain composition status does not clear" in error for error in errors),
            errors,
        )
        self.assertTrue(any("selected repertoire requires" in error and "recording" in error for error in errors), errors)

    def test_js_and_python_queries_are_value_identical(self) -> None:
        window = {"t0": 17.25, "seconds": 312.54}
        times = [17.25, 49.304, 113.4, 119.85, 120.1, 145.45, 248.0, 329.79]
        expected = [compact(score_at(self.score, at, window)) for at in times]
        script = f"""
          import fs from 'node:fs';
          import {{ scoreAt, validate }} from './engine/score.js';
          const score = validate(JSON.parse(fs.readFileSync('music/score.json')));
          const window = {json.dumps(window)};
          const compact = (state) => ({{
            source: Number(state.source_second.toFixed(8)),
            scale: Number(state.scale.toFixed(8)),
            tempo: Number(state.tempo.effective_bpm.toFixed(8)),
            beat: state.beat.index,
            downbeat: state.beat.downbeat,
            beat_phase: Number(state.beat.phase.toFixed(8)),
            phrase: state.phrase.id,
            dynamic: state.dynamic.midi_expression,
            movement: state.movement.id,
            movement_u: Number(state.movement.u.toFixed(8)),
            cues: state.cues.map((cue) => cue.id),
            visual: state.visual,
          }});
          console.log(JSON.stringify({json.dumps(times)}.map((at) => compact(scoreAt(score, at, window)))));
        """
        result = run("node", "--input-type=module", "--eval", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_bucket_event_queries_are_scaled_half_open_deduplicated_and_value_identical(self) -> None:
        window = {"t0": 51.25, "seconds": 312.54}
        scale = window["seconds"] / self.score["time"]["duration_seconds"]
        at = lambda source: window["t0"] + source * scale
        queries = [
            [at(120.0), at(136.0)],
            [at(128.0), at(132.0)],
            [at(225.0), at(228.0)],
            [at(226.0), at(226.5)],
            [at(128.0), at(128.0)],
        ]
        expected = [compact_events(events_between(self.score, start, end, window)) for start, end in queries]

        self.assertIn(6, self.score["lookup"]["buckets"][225]["active_cues"])
        self.assertIn(6, self.score["lookup"]["buckets"][226]["active_cues"])
        self.assertEqual([(row["type"], row["index"]) for row in expected[0]], [
            ("cue", 2), ("note", 2),
            ("cue", 3), ("note", 3),
            ("cue", 4), ("note", 4),
        ])
        self.assertEqual([(row["type"], row["index"]) for row in expected[1]], [("cue", 3), ("note", 3)])
        self.assertEqual([(row["type"], row["index"]) for row in expected[2]], [("cue", 6), ("note", 6)])
        self.assertEqual(expected[3], [], "an already-active cue is not a new authored start")
        self.assertEqual(expected[4], [])

        script = f"""
          import fs from 'node:fs';
          import {{ eventsBetween }} from './engine/score.js';
          const score = JSON.parse(fs.readFileSync('music/score.json'));
          const window = {json.dumps(window)};
          const compact = (events) => events.map((event) => ({{
            type: event.type,
            index: event.index,
            name: event.id ?? event.stem,
            at: Number(event.at.toFixed(8)),
            end: Number(event.end.toFixed(8)),
          }}));
          const queries = {json.dumps(queries)};
          console.log(JSON.stringify(queries.map(([start, end]) => compact(eventsBetween(score, start, end, window)))));
        """
        result = run("node", "--input-type=module", "--eval", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_event_queries_use_lookup_indices_without_scanning_event_arrays(self) -> None:
        class IndexedOnly(list):
            def __init__(self, rows: list[dict]) -> None:
                super().__init__(rows)
                self.reads = 0

            def __iter__(self):
                raise AssertionError("event query attempted a full-array scan")

            def __getitem__(self, index):
                if isinstance(index, int):
                    self.reads += 1
                return super().__getitem__(index)

        guarded = copy.deepcopy(self.score)
        guarded["cues"] = IndexedOnly(guarded["cues"])
        guarded["notes"] = IndexedOnly(guarded["notes"])
        events = events_between(guarded, 128.0, 128.1)
        self.assertEqual([(event["type"], event["index"]) for event in events], [("cue", 3), ("note", 3)])
        self.assertEqual((guarded["cues"].reads, guarded["notes"].reads), (1, 1))

        script = """
          import fs from 'node:fs';
          import { eventsBetween } from './engine/score.js';
          const score = JSON.parse(fs.readFileSync('music/score.json'));
          const guard = (rows) => {
            let reads = 0;
            const proxy = new Proxy(rows, {
              get(target, property, receiver) {
                if (property === Symbol.iterator) throw new Error('event query attempted a full-array scan');
                if (typeof property === 'string' && /^\\d+$/.test(property)) reads += 1;
                return Reflect.get(target, property, receiver);
              },
            });
            return { proxy, reads: () => reads };
          };
          const cues = guard(score.cues);
          const notes = guard(score.notes);
          score.cues = cues.proxy;
          score.notes = notes.proxy;
          const events = eventsBetween(score, 128, 128.1);
          console.log(JSON.stringify({
            events: events.map((event) => [event.type, event.index]),
            reads: [cues.reads(), notes.reads()],
          }));
        """
        result = run("node", "--input-type=module", "--eval", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"events": [["cue", 3], ["note", 3]], "reads": [1, 1]})

    def test_optional_live_loader_recovers_while_strict_loader_still_fails_closed(self) -> None:
        script = """
          import { load, loadOptional } from './engine/score.js';
          globalThis.fetch = async () => ({ ok: false, status: 404 });
          let reported = null;
          const optional = await loadOptional('missing-score.json', (error) => { reported = error.message; });
          let strict = null;
          try { await load('missing-score.json'); } catch (error) { strict = error.message; }
          console.log(JSON.stringify({ optional, reported, strict }));
        """
        result = run("node", "--input-type=module", "--eval", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "optional": None,
                "reported": "music score 404 at missing-score.json",
                "strict": "music score 404 at missing-score.json",
            },
        )

    def test_score_boundaries_beats_accents_and_visual_transitions_land_exactly(self) -> None:
        movements = [(row["id"], row["start_second"], row["end_second"]) for row in self.score["movements"]]
        self.assertEqual(
            movements,
            [
                ("ONE", 0.0, 40.0),
                ("ASSEMBLY", 40.0, 65.0),
                ("DIVISION", 65.0, 120.0),
                ("PHRASE", 120.0, 225.0),
                ("STILLNESS", 225.0, 285.0),
                ("RESEED", 285.0, 386.0),
                ("SIGNATURE", 386.0, 390.0),
            ],
        )
        beat = score_at(self.score, 4.0)["beat"]
        self.assertEqual((beat["index"], beat["bar"], beat["beat_in_bar"], beat["downbeat"]), (8, 3, 1, True))
        before = score_at(self.score, 127.999)
        accent = score_at(self.score, 128.0)
        after = score_at(self.score, 128.251)
        self.assertEqual(accent["cues"][0]["id"], "phrase-accent-a")
        self.assertEqual(accent["visual"]["recast"], before["visual"]["recast"] + 1)
        self.assertGreater(accent["visual"]["channel_offsets"]["turnover"], 0)
        self.assertFalse(after["cues"])
        self.assertEqual(after["visual"]["recast"], accent["visual"]["recast"])

    def test_seek_segment_concat_restart_and_audio_disabled_paths_align(self) -> None:
        window = {"t0": 51.25, "seconds": 312.54}
        full = events_between(self.score, window["t0"], window["t0"] + window["seconds"], window)
        edges = [window["t0"], 100.0, 177.0, 250.0, window["t0"] + window["seconds"]]
        segmented = [
            event
            for start, end in zip(edges, edges[1:])
            for event in events_between(self.score, start, end, window)
        ]
        self.assertEqual(segmented, full)
        phase = 0.615
        first = score_at(self.score, window["t0"] + phase * window["seconds"], window)
        restarted_window = {"t0": 900.0, "seconds": 425.0}
        restarted = score_at(
            self.score,
            restarted_window["t0"] + phase * restarted_window["seconds"],
            restarted_window,
        )
        self.assertEqual(
            (first["movement"]["id"], first["phrase"]["id"], first["beat"]["index"]),
            (restarted["movement"]["id"], restarted["phrase"]["id"], restarted["beat"]["index"]),
        )

        script = """
          import fs from 'node:fs';
          import { state } from './engine/clock.js';
          import { passageAt } from './engine/program.js';
          import { validate } from './engine/score.js';
          import { scheduleWebAudio } from './sound/web_audio.mjs';
          const score = validate(JSON.parse(fs.readFileSync('music/score.json')));
          const program = JSON.parse(fs.readFileSync('render/program.json'));
          const passage = passageAt(program, 0x12345678, 0, 7);
          const t = passage.t0 + (128 / 390) * passage.seconds;
          const before = state(0x12345678, t, program, 7, score);
          const audio = scheduleWebAudio({currentTime:0}, score, {}, 120, 140, {window:{t0:0,seconds:390}});
          const after = state(0x12345678, t, program, 7, score);
          console.log(JSON.stringify({
            identical: JSON.stringify(before) === JSON.stringify(after),
            planned: audio.plan.length,
            scheduled: audio.scheduled.length,
            missing: audio.missing.length,
            movement: before.movement,
            cue: before.music.cues.map((row) => row.id),
          }));
        """
        result = run("node", "--input-type=module", "--eval", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "identical": True,
                "planned": 4,
                "scheduled": 0,
                "missing": 4,
                "movement": "PHRASE",
                "cue": ["phrase-accent-a"],
            },
        )

    def test_control_and_segment_receipts_emit_score_and_source_identity_without_local_paths(self) -> None:
        result = run("node", "sound/control.mjs", "--rate", "0", "--score", "music/score.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        control = json.loads(result.stdout)
        self.assertEqual(control["music"]["identity"], self.score["identity"])
        self.assertEqual(control["music"]["score_file_sha256"], sha256(ROOT / "music/score.json"))
        self.assertEqual(len(control["music"]["events"]), len(self.score["cues"]) + len(self.score["notes"]))
        self.assertTrue(all(stem["midi_source_sha256"] == self.score["identity"]["midi_sha256"] for stem in control["music"]["stems"]))

        offline = load_module("danse_music_receipt_test", ROOT / "render/render.py")
        args = SimpleNamespace(
            window="passage",
            start=0.0,
            tier="screen",
            seed=0,
            stream=7,
            codec="preview",
            width=320,
            height=180,
            fps=30,
            segment_frames=60,
            score="music/score.json",
        )
        with mock.patch.object(offline, "source_tree_sha256", return_value="fixture-tree"):
            receipt = offline.segment_identity(args, 0, 60)
        identity = receipt["inputs"]["music_score"]
        self.assertEqual(identity["path"], "music/score.json")
        self.assertEqual(identity["contract_sha256"], self.score["identity"]["contract_sha256"])
        self.assertNotIn(str(ROOT), json.dumps(identity))
        self.assertTrue(all(set(stem) == {"id", "midi_source_sha256", "audio_source_sha256"} for stem in identity["stems"]))

    def test_python_renderer_exposes_the_same_event_plan_and_fails_closed_on_uncleared_stems(self) -> None:
        score_renderer = load_module("danse_fixture_score_renderer_test", ROOT / "sound/score.py")
        control_result = run("node", "sound/control.mjs", "--rate", "0", "--score", "music/score.json")
        self.assertEqual(control_result.returncode, 0, control_result.stderr)
        control = json.loads(control_result.stdout)
        plan = score_renderer.music_event_plan(control)
        self.assertEqual(plan, control["music"]["events"])
        self.assertTrue(all(stem["audio_source_sha256"] is None for stem in control["music"]["stems"]))


if __name__ == "__main__":
    unittest.main()

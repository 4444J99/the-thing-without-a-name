#!/usr/bin/env python3
"""Portable regression tests for Danse's delivery-trunk interfaces."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DELIVER = load("danse_deliver_test", ROOT / "render/deliver.py")
SCORE = load("danse_score_test", ROOT / "sound/score.py")
CHECK = load("danse_submission_check_test", ROOT / "submission/check.py")
BROWSER = load("danse_browser_test", ROOT / "render/browser.py")
OFFLINE = load("danse_offline_test", ROOT / "render/render.py")
BANK_CONTRACT = sys.modules["bank_contract"]
CORPUS_CONTRACT = load("danse_corpus_contract_test", ROOT / "pipeline/corpus_contract.py")
RESOLVE = load("danse_resolve_test", ROOT / "sound/resolve.py")
SPAN = {
    "t0": 0.0,
    "t1": 312.54,
    "duration": 312.54,
    "seed": 0xAF6B7BE5,
    "river_seed": 20170620,
    "passage": 0,
    "capture": "passage",
}


class DeliveryContractTest(unittest.TestCase):
    def test_offline_url_preserves_zero_seed_and_every_capture_override(self) -> None:
        args = SimpleNamespace(
            window="passage",
            start=120.25,
            tier="film",
            seed=0,
            stream=7,
            width=3840,
            height=2160,
            fps=24,
        )
        query = parse_qs(urlparse(OFFLINE.film_url("http://render.test", args)).query)
        self.assertEqual(
            query,
            {
                "capture": ["passage"],
                "from": ["120.25"],
                "tier": ["film"],
                "s": ["0"],
                "u": ["7"],
                "width": ["3840"],
                "height": ["2160"],
                "fps": ["24"],
            },
        )

    def test_render_resume_receipt_binds_inputs_source_and_output_bytes(self) -> None:
        args = SimpleNamespace(
            window="passage",
            start=0.0,
            tier="film",
            seed=0,
            stream=7,
            codec="prores",
            width=3840,
            height=2160,
            fps=30,
            segment_frames=900,
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            OFFLINE, "source_tree_sha256", return_value="source-tree"
        ):
            dest = Path(tmp) / "passage-0-seg-000.mov"
            dest.write_bytes(b"encoded segment")
            OFFLINE.write_segment_receipt(dest, args, 0, 30)
            expected = OFFLINE.segment_identity(args, 0, 30)
            probe = subprocess.CompletedProcess([], 0, stdout="30\n", stderr="")
            with mock.patch.object(OFFLINE.subprocess, "run", return_value=probe):
                self.assertTrue(OFFLINE.complete(dest, 30, expected))
                args.start = 1.0
                self.assertFalse(OFFLINE.complete(dest, 30, OFFLINE.segment_identity(args, 0, 30)))
                args.start = 0.0
                dest.write_bytes(b"different segment")
                self.assertFalse(OFFLINE.complete(dest, 30, expected))

    def test_concat_uses_only_explicitly_planned_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stem = root / "passage-default"
            args = SimpleNamespace(codec="prores")
            parts = OFFLINE.segment_paths(stem, args.codec, [0, 1])
            for part in [*parts, root / "passage-default-seg-002.mov"]:
                part.write_bytes(part.name.encode())
                OFFLINE.segment_receipt_path(part).write_text(json.dumps({"name": part.name}))

            def fake_concat(*_args, **_kwargs):
                stem.with_suffix(".mov").write_bytes(b"planned concat")
                return subprocess.CompletedProcess([], 0)

            with mock.patch.object(OFFLINE.subprocess, "run", side_effect=fake_concat):
                OFFLINE.concat(stem, args, parts)
            listing = (root / "passage-default-segments.txt").read_text()
            self.assertIn(parts[0].name, listing)
            self.assertIn(parts[1].name, listing)
            self.assertNotIn("seg-002", listing)
            receipt = json.loads(OFFLINE.concat_receipt_path(stem.with_suffix(".mov")).read_text())
            self.assertEqual([item["name"] for item in receipt["segments"]], [part.name for part in parts])

    def test_query_and_exact_tier_contracts_fail_closed(self) -> None:
        script = """
          import { numericParam } from './engine/query.js';
          import { requireTier } from './engine/tier.js';
          const zero = numericParam(new URLSearchParams('s=0'), 's', 99, {integer:true,min:0});
          let invalid = false;
          try { numericParam(new URLSearchParams('s=nope'), 's', 99, {integer:true,min:0}); } catch { invalid = true; }
          const corpus = {
            ensure: async () => {},
            has: (kind) => kind === 'plates',
          };
          let missingMatte = false;
          try { await requireTier(corpus, 'film', ['IMG_1570']); } catch { missingMatte = true; }
          console.log(JSON.stringify({zero, invalid, missingMatte}));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"zero": 0, "invalid": True, "missingMatte": True})

    def test_closing_signature_names_reproducible_river_position(self) -> None:
        script = """
          import { signature } from './engine/engine.js';
          const program = {signature:{format:'river 0x%RIVER_SEED%/%RIVER_STREAM% from %PASSAGE_T0%s passage %PASSAGE%'}};
          console.log(signature(program, {riverSeed:0, riverStream:7, passageSeed:123, passageT0:12.5, passage:4}));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "river 0x000000/000007 from 12.500s passage 4")

    def test_room_cache_identity_changes_with_same_count_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw, mask, pose = root / "raw.jpg", root / "mask.png", root / "pose.json"
            raw.write_bytes(b"first original")
            mask.write_bytes(b"first matte")
            pose.write_text("{}")
            items = [("IMG_1570", raw, mask, pose)]
            first = CORPUS_CONTRACT.room_cache_key(items)
            source_receipt = CORPUS_CONTRACT.source_set_receipt([raw])
            raw.write_bytes(b"corrected original")
            self.assertNotEqual(first, CORPUS_CONTRACT.room_cache_key(items))
            self.assertNotEqual(source_receipt, CORPUS_CONTRACT.source_set_receipt([raw]))

    def test_pipeline_inputs_fail_closed_before_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "raw").mkdir()
            (work / "vision/mask").mkdir(parents=True)
            (work / "vision/pose").mkdir(parents=True)
            complete_raw = work / "raw/IMG_1570.JPG"
            incomplete_raw = work / "raw/IMG_1571.JPG"
            complete_raw.write_bytes(b"raw one")
            incomplete_raw.write_bytes(b"raw two")
            (work / "vision/mask/IMG_1570.png").write_bytes(b"mask")
            (work / "vision/pose/IMG_1570.json").write_text("{}")
            complete, incomplete = CORPUS_CONTRACT.frame_inventory(work)
            self.assertEqual([row[0] for row in complete], ["IMG_1570"])
            self.assertEqual([row[0].name for row in incomplete], ["IMG_1571.JPG"])

            absent = work / "missing.png"
            self.assertEqual(CORPUS_CONTRACT.missing_measurement_inputs([complete_raw, absent]), [absent])
            self.assertIsNone(CORPUS_CONTRACT.block_shape_error(1024, 768, 16))
            self.assertIn("evenly divide", CORPUS_CONTRACT.block_shape_error(1024, 768, 30))

            readme = (ROOT / "README.md").read_text()
            self.assertIn("../reference/T-2017-full.png", readme)
            self.assertNotIn(".work/reference/T-2017-full.png", readme)

    def test_impractical_passage_offsets_are_rejected_without_walking(self) -> None:
        script = """
          import { readFileSync } from 'node:fs';
          import { passageAt } from './engine/program.js';
          const program = JSON.parse(readFileSync('./render/program.json', 'utf8'));
          let rejected = false;
          try { passageAt(program, 1, 1000000000000); } catch (error) { rejected = error instanceof RangeError; }
          console.log(JSON.stringify({rejected}));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"rejected": True})

    def test_registered_room_requires_content_identity_not_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "registered.MOV"
            candidate.write_bytes(b"confirmed recording")
            expected = RESOLVE.sha256_file(candidate)
            self.assertEqual(RESOLVE.room_content_matches(candidate, expected), (True, expected))
            candidate.write_bytes(b"different recording under the same name")
            matched, actual = RESOLVE.room_content_matches(candidate, expected)
            self.assertFalse(matched)
            self.assertNotEqual(actual, expected)

    def test_peak_normalised_grain_restores_original_level(self) -> None:
        self.assertAlmostEqual(SCORE.original_level_gain(0.5, 0.125), 0.25)

    def test_span_queries_are_metadata_only(self) -> None:
        payload = {
            **SPAN,
            "seed": 20170620,
            "passage": 0,
            "passageSeed": SPAN["seed"],
            "origin": "IMG_1594",
        }
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        DELIVER._capture_span_items.cache_clear()
        with mock.patch.object(DELIVER, "sh", return_value=completed) as run:
            span = DELIVER.query_capture_span("passage", start=120.0)
            span["t0"] = 999
            again = DELIVER.query_capture_span("passage", start=120.0)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--rate") + 1], "0")
        self.assertEqual(span["origin"], "IMG_1594")
        self.assertEqual(again["t0"], 0.0)
        self.assertEqual(run.call_count, 1)
        DELIVER._capture_span_items.cache_clear()

    def test_score_forwards_absolute_start_to_control(self) -> None:
        payload = {"capture": "passage", "t0": 120.0, "t1": 432.54, "duration": 312.54}
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(SCORE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(SCORE.control_track("passage", 123, 30, 120.0), payload)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--from") + 1], "120.0")
        self.assertEqual(command[command.index("--seed") + 1], "123")

    def test_score_rebases_absolute_control_times_into_the_capture(self) -> None:
        self.assertAlmostEqual(SCORE.local_time({"t0": 312.54}, 313.79), 1.25)

    def test_missing_command_is_a_controlled_subprocess_failure(self) -> None:
        with mock.patch.object(DELIVER.subprocess, "run", side_effect=FileNotFoundError("missing")):
            done = DELIVER.sh(["absent-command"])
        self.assertEqual(done.returncode, 127)
        self.assertIn("missing", done.stderr)

    def test_capture_roots_do_not_mix_start_offsets(self) -> None:
        root = Path("/render")
        first = DELIVER.capture_root(root, SPAN, 0.0)
        later = DELIVER.capture_root(root, {**SPAN, "seed": 7}, 120.25)
        self.assertNotEqual(first, later)
        self.assertEqual(first.parent, root)
        self.assertEqual(later.parent, root)

    def test_only_text_never_invokes_picture_or_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "render"
            argv = ["deliver.py", "--only", "text", "--out", str(out)]
            forbidden = mock.Mock(side_effect=AssertionError("render dependency invoked"))
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(DELIVER, "query_capture_span", forbidden),
                mock.patch.object(DELIVER, "passage_picture", forbidden),
                mock.patch.object(DELIVER, "passage_sound", forbidden),
                mock.patch.object(DELIVER, "probe", return_value=None),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            self.assertFalse(forbidden.called)
            self.assertTrue((out / "package/text/synopsis_short.txt").is_file())
            attest = yaml.safe_load((out / "package/attest.yaml").read_text())
            self.assertTrue(attest)
            self.assertTrue(all(value is None for value in attest.values()))

    def test_only_origin_copies_source_bytes_under_stills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "IMG_1594.JPG"
            source.write_bytes(b"camera-original")
            out = root / "render"
            argv = ["deliver.py", "--only", "origin", "--out", str(out)]
            forbidden = mock.Mock(side_effect=AssertionError("render dependency invoked"))
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(DELIVER, "registered_origin", return_value=source),
                mock.patch.object(DELIVER, "query_capture_span", forbidden),
                mock.patch.object(DELIVER, "passage_picture", forbidden),
                mock.patch.object(DELIVER, "passage_sound", forbidden),
                mock.patch.object(DELIVER, "probe", return_value=None),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            copied = out / "package/stills/origin-2017.jpg"
            self.assertEqual(copied.read_bytes(), source.read_bytes())
            self.assertFalse(forbidden.called)
            register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
            report = CHECK.Report()
            CHECK.check_origin_still(register["package"]["origin_still"], out / "package", report)
            self.assertEqual(report.failures, 0)

    def test_origin_source_is_owned_by_the_submission_register(self) -> None:
        with mock.patch.dict(DELIVER.os.environ, {}, clear=True):
            self.assertEqual(DELIVER.registered_origin(), DELIVER.RAW / "IMG_1594.JPG")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            DELIVER.os.environ, {"DANSE_WORK": tmp}, clear=True
        ):
            self.assertEqual(DELIVER.registered_origin(), Path(tmp) / "raw/IMG_1594.JPG")

    def test_attestation_template_survives_unowned_manual_requirement(self) -> None:
        register = {
            "requirements": [
                {"id": "later", "rule": "declare ownership", "check": "manual"},
                {"rule": "has no identifier", "check": "manual"},
                {"id": "without-rule", "check": "manual"},
            ]
        }
        with mock.patch.object(DELIVER.yaml, "safe_load", return_value=register):
            text = DELIVER.attestation_template()
        self.assertIn("[UNOWNED]", text)
        self.assertIn("later: null", text)
        self.assertIn("without-rule: null", text)
        self.assertNotIn("has no identifier", text)

    def test_text_preflight_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "absent"
            argv = ["deliver.py", "--preflight", "--only", "text", "--out", str(out)]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(DELIVER, "query_capture_span", return_value=SPAN),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            self.assertFalse(out.exists())

    def test_preflight_reports_failed_capture_query_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "absent"
            argv = ["deliver.py", "--preflight", "--only", "master", "--out", str(out)]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(DELIVER, "query_capture_span", side_effect=SystemExit("node query failed")),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(DELIVER.main(), 1)
            self.assertIn("node query failed", output.getvalue())
            self.assertIn("NOT READY", output.getvalue())
            self.assertFalse(out.exists())

    def test_preflight_reuses_a_provenanced_cached_score(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            picture = root / "passage-default.mov"
            score = root / "passage-score.wav"

            def fake_probe(path: Path):
                if path == picture:
                    return {"seconds": SPAN["duration"], "fps": 30}
                if path == score:
                    return {"seconds": SPAN["duration"]}
                return None

            with (
                mock.patch.object(DELIVER, "probe", side_effect=fake_probe),
                mock.patch.object(DELIVER, "score_provenance", return_value={"sources": ["a", "b"]}),
                mock.patch.object(DELIVER.shutil, "which", side_effect=lambda command: f"/tools/{command}"),
                redirect_stdout(io.StringIO()) as output,
            ):
                result = DELIVER.preflight(program, SPAN, {"master"}, set(), "film", root, package, None)
            self.assertEqual(result, 0)
            self.assertNotIn("Python module numpy", output.getvalue())
            self.assertNotIn("grain bank", output.getvalue())

    def test_cached_passage_picture_requires_current_concat_receipt(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        span = {**SPAN, "duration": 300.0, "t0": 0.0}
        info = {"seconds": 300.0, "width": 3840, "height": 2160, "fps": 30}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(DELIVER, "OUT", Path(tmp)):
            dest = Path(tmp) / "passage-default.mov"
            dest.write_bytes(b"cached picture")
            completed = subprocess.CompletedProcess([], 0)
            stale = subprocess.CompletedProcess([], 1)
            with (
                mock.patch.object(DELIVER, "query_capture_span", return_value=span),
                mock.patch.object(DELIVER, "probe_required", return_value=info),
                mock.patch.object(DELIVER.subprocess, "run", side_effect=[stale, completed]) as run,
            ):
                self.assertEqual(DELIVER.passage_picture(program, "film", False), dest)
            self.assertIn("--check-concat", run.call_args_list[0].args[0])
            self.assertIn("--resume", run.call_args_list[1].args[0])

    def test_preflight_reuses_manifested_origin_without_raw_source(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            origin_copy = package / "stills/origin-2017.jpg"
            origin_copy.parent.mkdir(parents=True)
            origin_copy.write_bytes(b"preserved origin")
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "passage_seed": DELIVER.hexseed(SPAN["seed"]),
                        "passage": SPAN["passage"],
                        "t0": SPAN["t0"],
                        "t1": SPAN["t1"],
                        "duration": SPAN["duration"],
                        "items": [{"name": "stills/origin-2017.jpg", "sha256": DELIVER.digest(origin_copy)}],
                    }
                )
            )
            missing_raw = root / "unmounted/IMG_1594.JPG"
            with (
                mock.patch.object(DELIVER.shutil, "which", return_value="/tools/node"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    DELIVER.preflight(program, SPAN, {"origin"}, set(), "film", root, package, missing_raw),
                    0,
                )
                self.assertEqual(
                    DELIVER.preflight(program, SPAN, {"origin"}, {"origin"}, "film", root, package, missing_raw),
                    1,
                )

    def test_text_only_preserves_existing_sound_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "render"
            package = out / "package"
            package.mkdir(parents=True)
            old_sound = {"bank_fingerprint": "old-bank", "sources": ["IMG_0226.MOV", "IMG_0227.MOV"]}
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "passage_seed": DELIVER.hexseed(SPAN["seed"]),
                        "passage": SPAN["passage"],
                        "t0": SPAN["t0"],
                        "t1": SPAN["t1"],
                        "duration": SPAN["duration"],
                        "sound": old_sound,
                        "items": [],
                    }
                )
            )
            current_bank = root / "bank.json"
            current_bank.write_text(
                json.dumps(
                    {
                        "fingerprint": "new-bank",
                        "sources": [{"name": "IMG_0226.MOV"}, {"name": "IMG_0227.MOV"}],
                    }
                )
            )
            with (
                mock.patch.object(sys, "argv", ["deliver.py", "--only", "text", "--out", str(out)]),
                mock.patch.object(DELIVER, "BANK", current_bank),
                mock.patch.object(
                    DELIVER,
                    "query_capture_span",
                    side_effect=AssertionError("text-only update queried passage metadata"),
                ),
                mock.patch.object(DELIVER, "probe", return_value=None),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            manifest = json.loads((package / "manifest.json").read_text())
            self.assertEqual(manifest["sound"], old_sound)

    def test_reused_media_preserves_prior_digest_for_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            master = package / "master.mov"
            master.write_bytes(b"modified after packaging")
            prior_digest = "0" * 64
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "passage_seed": DELIVER.hexseed(SPAN["seed"]),
                        "passage": SPAN["passage"],
                        "t0": SPAN["t0"],
                        "t1": SPAN["t1"],
                        "duration": SPAN["duration"],
                        "items": [{"name": "master.mov", "bytes": 23, "sha256": prior_digest}],
                    }
                )
            )
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["deliver.py", "--only", "master", "--out", str(root / "render"), "--package", str(package)],
                ),
                mock.patch.object(DELIVER, "query_capture_span", return_value=SPAN),
                mock.patch.object(
                    DELIVER,
                    "passage_sound",
                    return_value=(root / "passage-score.wav", {"score_sha256": "score"}, False),
                ),
                mock.patch.object(DELIVER.shutil, "which", return_value="/tools/ffprobe"),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            receipt = next(
                item for item in json.loads((package / "manifest.json").read_text())["items"] if item["name"] == "master.mov"
            )
            self.assertEqual(receipt["sha256"], prior_digest)
            self.assertNotEqual(receipt["sha256"], DELIVER.digest(master))

    def test_score_receipt_is_bound_to_cached_audio_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            score = Path(tmp) / "passage-score.wav"
            score.write_bytes(b"score-audio")
            provenance = {
                "bank_fingerprint": "bank-fingerprint",
                "sources": ["IMG_0226.MOV", "IMG_0227.MOV"],
            }
            DELIVER.write_score_receipt(score, SPAN, provenance)
            self.assertEqual(
                DELIVER.score_provenance(score, SPAN),
                {**provenance, "score_sha256": DELIVER.digest(score)},
            )
            score.write_bytes(b"changed-audio")
            self.assertIsNone(DELIVER.score_provenance(score, SPAN))

    def test_missing_manifest_refuses_preexisting_package_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            self.assertTrue(DELIVER.package_provenance_matches(package, SPAN))
            (package / "master.mov").write_bytes(b"unowned media")
            self.assertFalse(DELIVER.package_provenance_matches(package, SPAN))

    def test_passage_independent_manifests_do_not_claim_or_require_a_span(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "manifest.json").write_text(
                json.dumps({"items": [{"name": "text/synopsis_short.txt"}]})
            )
            self.assertTrue(DELIVER.package_provenance_matches(package, SPAN, start=120.0))
            (package / "master.mov").write_bytes(b"unmanifested passage")
            self.assertFalse(DELIVER.package_provenance_matches(package, SPAN, start=120.0))

    def test_fixed_window_package_receipts_bind_the_selected_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "passage_seed": DELIVER.hexseed(SPAN["seed"]),
                        "passage": SPAN["passage"],
                        "start": 120.0,
                        "t0": SPAN["t0"],
                        "t1": SPAN["t1"],
                        "duration": SPAN["duration"],
                        "items": [{"name": "trailer.mp4"}],
                    }
                )
            )
            self.assertTrue(DELIVER.package_provenance_matches(package, SPAN, start=120.0))
            self.assertFalse(DELIVER.package_provenance_matches(package, SPAN, start=121.0))

    def test_forced_score_rebuilds_every_selected_audio_derivative(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            for name in DELIVER.AUDIO_ITEMS:
                (package / name).parent.mkdir(parents=True, exist_ok=True)
                (package / name).touch()
            work = DELIVER.pending(program, {"master", "derived", "reel"}, {"master"}, package)
        self.assertTrue(work["master"])
        self.assertEqual(work["derived"], set(DELIVER.DERIVED))
        self.assertTrue(work["reel"])

    def test_rebuilt_score_invalidates_every_selected_audio_artifact(self) -> None:
        work = {"master": False, "derived": set(), "reel": False, "stills": False}
        DELIVER.expand_rebuilt_score_dependents(work, {"master", "derived", "reel"})
        self.assertTrue(work["master"])
        self.assertEqual(work["derived"], set(DELIVER.DERIVED))
        self.assertTrue(work["reel"])

    def test_reel_renderer_receives_the_resolved_capture_start(self) -> None:
        reel_span = {**SPAN, "capture": "reel", "t0": 140.0, "t1": 170.0, "duration": 30.0}
        passage_span = {**SPAN, "t0": 120.0, "t1": 432.54}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "render"
            package = root / "package"
            out.mkdir()
            package.mkdir()

            def query(name: str, start: float = 0.0) -> dict:
                return reel_span if name == "reel" else passage_span

            def render(command: list[str], **_: object) -> subprocess.CompletedProcess:
                (out / "reel-default.mp4").write_bytes(b"rendered reel")
                return subprocess.CompletedProcess(command, 0)

            def mux_reel(_picture: Path, _audio: Path, dest: Path, *_: object, **__: object) -> None:
                dest.write_bytes(b"muxed reel")

            with (
                mock.patch.object(DELIVER, "OUT", out),
                mock.patch.object(DELIVER, "PACKAGE", package),
                mock.patch.object(DELIVER, "query_capture_span", side_effect=query),
                mock.patch.object(DELIVER.subprocess, "run", side_effect=render) as run,
                mock.patch.object(DELIVER, "cut_audio"),
                mock.patch.object(DELIVER, "mux", side_effect=mux_reel),
            ):
                DELIVER.deliver_reel({}, root / "score.wav", "film", True, start=120.0)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--start") + 1], "140.0")

    def test_capture_overrun_is_rejected_before_render(self) -> None:
        overrun = {**SPAN, "t0": 300.0, "t1": 470.0, "duration": 170.0, "capture": "midnight-moment"}
        with mock.patch.object(DELIVER, "query_capture_span", return_value=overrun):
            error = DELIVER.capture_span_error("midnight-moment", SPAN, 300.0)
        self.assertIn("does not fit passage", error)

    def test_bank_contract_rejects_missing_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bank_root = Path(tmp)
            grains = []
            kinds = ("bed", "sustained", "transient")
            sources = ["IMG_0226.MOV", "IMG_0227.MOV"]
            source_rows = []
            for source in sources:
                source_file = bank_root / source
                source_file.write_bytes(f"private source fixture: {source}".encode())
                source_rows.append({"name": source, "sha256": BANK_CONTRACT.sha256(source_file)})
            for i in range(24):
                grains.append(
                    {
                        "id": f"grain-{i}",
                        "source": sources[i % 2],
                        "kind": kinds[i % len(kinds)],
                        "centroid": i + 1,
                        "brightness": i + 1,
                        "flatness": i + 1,
                        "decay": i + 1,
                        "attack": i + 1,
                        "zcr": i + 1,
                        "rms": 0.125,
                        "wav_sha256": source_rows[i % 2]["sha256"],
                    }
                )
            index = bank_root / "bank.json"
            payload = {
                "schema": "danse.sound.bank.v1",
                "rate": 48_000,
                "sources": source_rows,
                "grains": grains,
            }
            payload["fingerprint"] = BANK_CONTRACT.bank_fingerprint(payload)
            index.write_text(json.dumps(payload))
            expected_source_digests = {row["name"]: row["sha256"] for row in source_rows}
            missing = DELIVER.audit_bank(index, expected_source_digests)
            self.assertEqual(len(missing.payload_errors), len(grains))
            for grain in grains:
                with wave.open(str(bank_root / f"{grain['id']}.wav"), "wb") as payload:
                    payload.setnchannels(1)
                    payload.setsampwidth(2)
                    payload.setframerate(48_000)
                    payload.writeframes(b"\0\0")
                grain["wav_sha256"] = BANK_CONTRACT.sha256(bank_root / f"{grain['id']}.wav")
            payload = {
                "schema": "danse.sound.bank.v1",
                "rate": 48_000,
                "sources": source_rows,
                "grains": grains,
            }
            payload["fingerprint"] = BANK_CONTRACT.bank_fingerprint(payload)
            index.write_text(json.dumps(payload))
            self.assertTrue(DELIVER.audit_bank(index, expected_source_digests).valid)
            stale_register = {**expected_source_digests, sources[0]: source_rows[1]["sha256"]}
            self.assertIn("do not match", DELIVER.audit_bank(index, stale_register).provenance_errors[-1])

            bad_rate = bank_root / f"{grains[0]['id']}.wav"
            with wave.open(str(bad_rate), "wb") as payload:
                payload.setnchannels(1)
                payload.setsampwidth(2)
                payload.setframerate(44_100)
                payload.writeframes(b"\0\0")
            self.assertIn("sample rate 44100", DELIVER.audit_bank(index, expected_source_digests).payload_errors[0])

            with wave.open(str(bad_rate), "wb") as payload:
                payload.setnchannels(1)
                payload.setsampwidth(2)
                payload.setframerate(48_000)
                payload.writeframes(b"\1\0")
            self.assertIn(
                f"changed {grains[0]['id']}.wav",
                DELIVER.audit_bank(index, expected_source_digests).payload_errors,
            )

    def test_malformed_cached_receipts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "passage-score.wav"
            score.write_bytes(b"score")
            DELIVER.score_receipt_path(score).write_text("[]")
            self.assertIsNone(DELIVER.score_provenance(score, SPAN))
            DELIVER.score_receipt_path(score).write_text(
                json.dumps(
                    {
                        "schema": "danse.score.receipt.v1",
                        "sha256": DELIVER.digest(score),
                        "bank_fingerprint": "bank",
                        "sources": [],
                        "t0": "bad",
                    }
                )
            )
            self.assertIsNone(DELIVER.score_provenance(score, SPAN))

            package = root / "package"
            package.mkdir()
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "passage_seed": DELIVER.hexseed(SPAN["seed"]),
                        "passage": SPAN["passage"],
                        "t0": "bad",
                        "items": [{"name": "master.mov"}],
                    }
                )
            )
            self.assertFalse(DELIVER.package_provenance_matches(package, SPAN))

    def test_master_must_match_manifested_passage_and_digest(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            master = package / "master.mov"
            master.write_bytes(b"complete master")
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "duration": 312.54,
                        "items": [{"name": "master.mov", "sha256": "stale"}],
                    }
                )
            )
            info = {
                "width": 3840,
                "height": 2160,
                "fps": 30.0,
                "seconds": 4.0,
                "vcodec": "prores",
                "vprofile": "HQ",
                "acodec": "pcm_s24le",
                "channels": 2,
            }
            report = CHECK.Report()
            with mock.patch.object(CHECK, "probe", return_value=info):
                CHECK.check_master(register["package"]["master"], register, package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["master is one whole manifested passage"], CHECK.FAIL)
            self.assertEqual(statuses["master bytes match delivery manifest"], CHECK.FAIL)

            info["seconds"] = 312.54
            info["fps"] = 0.0
            with mock.patch.object(CHECK, "probe", return_value=info):
                report = CHECK.Report()
                CHECK.check_master(register["package"]["master"], register, package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["master is one whole manifested passage"], CHECK.FAIL)

    def test_screener_directly_matches_manifested_passage_and_digest(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            screener = package / "screener.mov"
            screener.write_bytes(b"complete screener")
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "duration": SPAN["duration"],
                        "items": [{"name": screener.name, "sha256": CHECK.sha256(screener)}],
                    }
                )
            )
            info = {
                "width": 1920,
                "height": 1080,
                "seconds": SPAN["duration"],
                "vcodec": "h264",
                "acodec": "aac",
                "channels": 2,
            }
            with mock.patch.object(CHECK, "probe", return_value=info):
                report = CHECK.Report()
                CHECK.check_screener(register["package"]["screener"], package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["screener is one whole manifested passage"], CHECK.PASS)
            self.assertEqual(statuses["screener bytes match delivery manifest"], CHECK.PASS)

            with mock.patch.object(CHECK, "probe", return_value=None):
                report = CHECK.Report()
                CHECK.check_screener(register["package"]["screener"], package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["screener bytes match delivery manifest"], CHECK.PASS)
            self.assertEqual(statuses["screener"], CHECK.OPEN)

            screener.write_bytes(b"replacement screener")
            info["seconds"] -= 1
            with mock.patch.object(CHECK, "probe", return_value=info):
                report = CHECK.Report()
                CHECK.check_screener(register["package"]["screener"], package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["screener is one whole manifested passage"], CHECK.FAIL)
            self.assertEqual(statuses["screener bytes match delivery manifest"], CHECK.FAIL)

    def test_seed_stills_match_their_manifested_bytes(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            stills = package / "stills"
            stills.mkdir()
            paths = []
            for i in range(6):
                path = stills / f"seed-0x{i:06X}.jpg"
                path.write_bytes(f"still {i}".encode())
                paths.append(path)
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {"name": f"stills/{path.name}", "sha256": CHECK.sha256(path)} for path in paths
                        ]
                    }
                )
            )
            with mock.patch.object(CHECK, "image_size", return_value=(3840, 2160)):
                report = CHECK.Report()
                CHECK.check_stills(register["package"]["stills"], package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["stills bytes match delivery manifest"], CHECK.PASS)

            paths[0].write_bytes(b"replacement still")
            with mock.patch.object(CHECK, "image_size", return_value=(3840, 2160)):
                report = CHECK.Report()
                CHECK.check_stills(register["package"]["stills"], package, report)
            statuses = {name: status for _, name, status, _ in report.rows}
            self.assertEqual(statuses["stills bytes match delivery manifest"], CHECK.FAIL)

    def test_audio_provenance_is_bound_to_each_artifact(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        expected = register["package"]["audio"]["source_recordings"]
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            for name in ("master.mov", "screener.mp4"):
                (package / name).touch()
            current = {"bank_fingerprint": "current", "sources": expected, "score_sha256": "score-current"}
            stale = {"bank_fingerprint": "stale", "sources": expected, "score_sha256": "score-current"}
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "duration": SPAN["duration"],
                        "sound": current,
                        "items": [
                            {
                                "name": "master.mov",
                                "sha256": CHECK.sha256(package / "master.mov"),
                                "sound": current,
                            },
                            {
                                "name": "screener.mp4",
                                "sha256": CHECK.sha256(package / "screener.mp4"),
                                "sound": stale,
                            },
                        ],
                    }
                )
            )
            report = CHECK.Report()
            with (
                mock.patch.object(CHECK, "loudness", return_value={"lufs": -16.0, "true_peak_dbtp": -1.1}),
                mock.patch.object(CHECK, "probe", return_value={"seconds": SPAN["duration"]}),
            ):
                CHECK.check_audio(register["package"]["audio"], package, report)
            row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
            self.assertEqual(row[2], CHECK.FAIL)
            self.assertIn("mixed bank fingerprints", row[3])

            manifest = json.loads((package / "manifest.json").read_text())
            manifest["items"][1]["sound"] = {
                "bank_fingerprint": "current",
                "sources": expected,
                "score_sha256": "score-stale",
            }
            (package / "manifest.json").write_text(json.dumps(manifest))
            with (
                mock.patch.object(CHECK, "loudness", return_value={"lufs": -16.0, "true_peak_dbtp": -1.1}),
                mock.patch.object(CHECK, "probe", return_value={"seconds": SPAN["duration"]}),
            ):
                report = CHECK.Report()
                CHECK.check_audio(register["package"]["audio"], package, report)
            row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
            self.assertEqual(row[2], CHECK.FAIL)
            self.assertIn("mixed score digests", row[3])

    def test_empty_audio_package_reports_its_actual_cause(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            report = CHECK.Report()
            CHECK.check_audio(register["package"]["audio"], Path(tmp), report)
        row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
        self.assertEqual(row[2], CHECK.FAIL)
        self.assertEqual(row[3], "no audio artifact staged")

    def test_audio_receipts_bind_screener_bytes_and_passage_duration(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        expected = register["package"]["audio"]["source_recordings"]
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            master = package / "master.mxf"
            screener = package / "screener.mov"
            master.write_bytes(b"master")
            screener.write_bytes(b"screener")
            sound = {"bank_fingerprint": "bank", "sources": expected, "score_sha256": "score-current"}

            def write_manifest() -> None:
                (package / "manifest.json").write_text(
                    json.dumps(
                        {
                            "duration": SPAN["duration"],
                            "sound": sound,
                            "items": [
                                {"name": master.name, "sha256": CHECK.sha256(master), "sound": sound},
                                {"name": screener.name, "sha256": CHECK.sha256(screener), "sound": sound},
                            ],
                        }
                    )
                )

            write_manifest()
            with (
                mock.patch.object(CHECK, "loudness", return_value={"lufs": -16.0, "true_peak_dbtp": -1.1}),
                mock.patch.object(CHECK, "probe", return_value={"seconds": SPAN["duration"]}),
            ):
                report = CHECK.Report()
                CHECK.check_audio(register["package"]["audio"], package, report)
            row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
            self.assertEqual(row[2], CHECK.PASS)

            screener.write_bytes(b"replaced screener")
            with (
                mock.patch.object(CHECK, "loudness", return_value={"lufs": -16.0, "true_peak_dbtp": -1.1}),
                mock.patch.object(CHECK, "probe", return_value={"seconds": SPAN["duration"]}),
            ):
                report = CHECK.Report()
                CHECK.check_audio(register["package"]["audio"], package, report)
            row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
            self.assertEqual(row[2], CHECK.FAIL)
            self.assertIn("screener.mov (digest)", row[3])

            write_manifest()
            with (
                mock.patch.object(CHECK, "loudness", return_value={"lufs": -16.0, "true_peak_dbtp": -1.1}),
                mock.patch.object(CHECK, "probe", return_value={"seconds": SPAN["duration"] - 10}),
            ):
                report = CHECK.Report()
                CHECK.check_audio(register["package"]["audio"], package, report)
            row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
            self.assertEqual(row[2], CHECK.FAIL)
            self.assertIn("passage duration", row[3])

    def test_attestations_are_cumulative_by_owned_phase(self) -> None:
        reg = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "attest.yaml").write_text("final-cut-only: true\n")
            expected = {"package": 3, "uploaded": 5, "submitted": 6}
            for phase, count in expected.items():
                report = CHECK.Report()
                CHECK.check_attestations(reg, root, phase, report)
                self.assertEqual(len(report.rows), count)
                self.assertEqual(report.failures, count - 1)

    def test_submitted_phase_explains_elapsed_target_without_reopening_it(self) -> None:
        reg = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        now = datetime(2026, 8, 25, 12, tzinfo=ZoneInfo("America/New_York"))
        report = CHECK.Report()
        CHECK.check_deadline(reg, "submitted", report, now=now)
        target = next(row for row in report.rows if row[1] == "target file date")
        self.assertEqual(target[2], CHECK.PASS)
        self.assertIn("submitted-phase receipt", target[3])

    def test_submitted_phase_remains_verifiable_after_the_hard_wall(self) -> None:
        reg = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        now = datetime(2026, 9, 2, 12, tzinfo=ZoneInfo("America/New_York"))
        submitted = CHECK.Report()
        CHECK.check_deadline(reg, "submitted", submitted, now=now)
        hard_wall = next(row for row in submitted.rows if row[1] == "hard wall")
        self.assertEqual(hard_wall[2], CHECK.PASS)
        package = CHECK.Report()
        CHECK.check_deadline(reg, "package", package, now=now)
        self.assertEqual(package.rows[0][2], CHECK.FAIL)

    def test_probe_ignores_attached_picture_streams(self) -> None:
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 640,
                    "height": 480,
                    "r_frame_rate": "0/1",
                    "disposition": {"attached_pic": 1},
                },
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "disposition": {"attached_pic": 0},
                },
            ],
            "format": {"duration": "15.0"},
        }
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        with (
            mock.patch.object(CHECK.shutil, "which", return_value="/usr/bin/ffprobe"),
            mock.patch.object(CHECK.subprocess, "run", return_value=completed),
        ):
            info = CHECK.probe(Path("screener.mp4"))
        self.assertEqual(info["vcodec"], "h264")
        self.assertEqual(info["width"], 1920)

    def test_control_rejects_non_numeric_start(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is not installed")
        done = subprocess.run(
            ["node", str(ROOT / "sound/control.mjs"), "--from", "not-a-number", "--rate", "0"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("--from must be a non-negative number", done.stderr)

    def test_projection_probe_returns_page_self_test_status(self) -> None:
        class Locator:
            def inner_text(self) -> str:
                return "SELF-TEST PASS\nmax Δ 0/255"

        page = mock.Mock()
        page.gl_renderer = "ANGLE Metal Renderer"
        page.evaluate.return_value = True
        page.locator.return_value = Locator()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(BROWSER.run_probe(page, "http://example.test"), 0)
        page.goto.assert_called_once_with("http://example.test/probe.html", wait_until="load")

    def test_explicit_browser_base_never_falls_back_to_local_checkout(self) -> None:
        forbidden = mock.Mock(side_effect=AssertionError("local server fallback invoked"))
        with (
            mock.patch.object(sys, "argv", ["browser.py", "--check", "--base", "https://unreachable.invalid"]),
            mock.patch.object(BROWSER, "reachable", return_value=False),
            mock.patch.object(BROWSER, "serve", forbidden),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            BROWSER.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(forbidden.called)


if __name__ == "__main__":
    unittest.main()

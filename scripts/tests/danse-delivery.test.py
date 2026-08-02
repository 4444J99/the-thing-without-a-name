#!/usr/bin/env python3
"""Portable regression tests for Danse's delivery-trunk interfaces."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock
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
                mock.patch.object(DELIVER, "query_capture_span", return_value=SPAN),
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
                mock.patch.object(DELIVER, "query_capture_span", return_value=SPAN),
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
        self.assertEqual(DELIVER.registered_origin(), DELIVER.RAW / "IMG_1594.JPG")

    def test_attestation_template_survives_unowned_manual_requirement(self) -> None:
        register = {"requirements": [{"id": "later", "rule": "declare ownership", "check": "manual"}]}
        with mock.patch.object(DELIVER.yaml, "safe_load", return_value=register):
            text = DELIVER.attestation_template()
        self.assertIn("[UNOWNED]", text)
        self.assertIn("later: null", text)

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
                mock.patch.object(DELIVER, "query_capture_span", return_value=SPAN),
                mock.patch.object(DELIVER, "probe", return_value=None),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            manifest = json.loads((package / "manifest.json").read_text())
            self.assertEqual(manifest["sound"], old_sound)

    def test_score_receipt_is_bound_to_cached_audio_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            score = Path(tmp) / "passage-score.wav"
            score.write_bytes(b"score-audio")
            provenance = {
                "bank_fingerprint": "bank-fingerprint",
                "sources": ["IMG_0226.MOV", "IMG_0227.MOV"],
            }
            DELIVER.write_score_receipt(score, SPAN, provenance)
            self.assertEqual(DELIVER.score_provenance(score, SPAN), provenance)
            score.write_bytes(b"changed-audio")
            self.assertIsNone(DELIVER.score_provenance(score, SPAN))

    def test_missing_manifest_refuses_preexisting_package_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            self.assertTrue(DELIVER.package_provenance_matches(package, SPAN))
            (package / "master.mov").write_bytes(b"unowned media")
            self.assertFalse(DELIVER.package_provenance_matches(package, SPAN))

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
                    }
                )
            index = bank_root / "bank.json"
            index.write_text(
                json.dumps(
                    {
                        "schema": "danse.sound.bank.v1",
                        "rate": 48_000,
                        "fingerprint": "bank-fingerprint",
                        "sources": [{"name": source} for source in sources],
                        "grains": grains,
                    }
                )
            )
            missing = DELIVER.audit_bank(index, sources)
            self.assertEqual(len(missing.payload_errors), len(grains))
            for grain in grains:
                with wave.open(str(bank_root / f"{grain['id']}.wav"), "wb") as payload:
                    payload.setnchannels(1)
                    payload.setsampwidth(2)
                    payload.setframerate(48_000)
                    payload.writeframes(b"\0\0")
            self.assertTrue(DELIVER.audit_bank(index, sources).valid)

            bad_rate = bank_root / f"{grains[0]['id']}.wav"
            with wave.open(str(bad_rate), "wb") as payload:
                payload.setnchannels(1)
                payload.setsampwidth(2)
                payload.setframerate(44_100)
                payload.writeframes(b"\0\0")
            self.assertIn("sample rate 44100", DELIVER.audit_bank(index, sources).payload_errors[0])

    def test_malformed_cached_receipts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "passage-score.wav"
            score.write_bytes(b"score")
            score.with_suffix(".json").write_text('{"schema":"danse.score.receipt.v1","t0":"bad"}')
            self.assertIsNone(DELIVER.score_provenance(score, SPAN))

            package = root / "package"
            package.mkdir()
            (package / "manifest.json").write_text('{"passage_seed":"0xAF6B7BE5","t0":"bad"}')
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

    def test_audio_provenance_is_bound_to_each_artifact(self) -> None:
        register = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
        expected = register["package"]["audio"]["source_recordings"]
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            for name in ("master.mov", "screener.mp4"):
                (package / name).touch()
            current = {"bank_fingerprint": "current", "sources": expected}
            stale = {"bank_fingerprint": "stale", "sources": expected}
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "sound": current,
                        "items": [
                            {"name": "master.mov", "sound": current},
                            {"name": "screener.mp4", "sound": stale},
                        ],
                    }
                )
            )
            report = CHECK.Report()
            with mock.patch.object(CHECK, "loudness", return_value={"lufs": -16.0, "true_peak_dbtp": -1.1}):
                CHECK.check_audio(register["package"]["audio"], package, report)
            row = next(row for row in report.rows if row[1] == "per-artifact score provenance")
            self.assertEqual(row[2], CHECK.FAIL)
            self.assertIn("mixed bank fingerprints", row[3])

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


if __name__ == "__main__":
    unittest.main()

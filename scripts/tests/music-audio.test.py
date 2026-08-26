#!/usr/bin/env python3
"""Focused custody and deterministic-audio tests for the Delibes suite."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "music"))
sys.path.insert(0, str(ROOT / "sound"))

from adapt_delibes import (  # noqa: E402
    MidiEvent,
    build,
    channel,
    meta,
    parse_midi,
    sha256,
    validate_inputs as validate_adaptation_inputs,
    write_track,
)
from render_music import (  # noqa: E402
    loudness_gate,
    mix_stems,
    pcm_slice_hash,
    render_stems,
    require_hash,
    validate_inputs as validate_render_inputs,
)
from validate_repertoire import load_register, validate_document, validate_schema_instance  # noqa: E402


FLUIDSYNTH = Path("/opt/homebrew/bin/fluidsynth")
FFMPEG = Path("/opt/homebrew/bin/ffmpeg")
SOUNDFONT = ROOT / ".work" / "music" / "MuseScore_General.sf3"
EXPECTED_MIDI = "a42b36415e6b41f63778e19b6b171b34c65eeca3c862c22eb0f80ee67980f199"


class DelibesCustodyTest(unittest.TestCase):
    def test_source_adaptation_and_toolchain_hashes_are_exact(self) -> None:
        adaptation = json.loads((ROOT / "music/adaptation.json").read_text())
        toolchain = json.loads((ROOT / "music/audio-toolchain.json").read_text())
        uses = json.loads((ROOT / "sound/audio-uses.json").read_text())
        self.assertEqual(
            [row["sha256"] for row in adaptation["sources"]],
            [
                "76e183b57c7f035a319bf5a7c5691d61c8a5f3af61f9d1b860047f7b28e6dc70",
                "86e1eaad1e99fcf3f275af9c59cde94580d9f9bfb10f7d366053a398295001c7",
            ],
        )
        for row in adaptation["sources"]:
            self.assertEqual(sha256(ROOT / row["path"]), row["sha256"])
        self.assertEqual(sha256(ROOT / adaptation["output"]["path"]), EXPECTED_MIDI)
        self.assertEqual(toolchain["midi"]["sha256"], EXPECTED_MIDI)
        self.assertEqual(sha256(ROOT / toolchain["adaptation"]["path"]), toolchain["adaptation"]["sha256"])
        self.assertEqual(sha256(ROOT / toolchain["mix"]["path"]), toolchain["mix"]["sha256"])
        self.assertEqual(sha256(ROOT / toolchain["renderer"]["path"]), toolchain["renderer"]["sha256"])
        if SOUNDFONT.is_file():
            self.assertEqual(sha256(SOUNDFONT), toolchain["soundfont"]["sha256"])
        if FLUIDSYNTH.is_file():
            self.assertEqual(sha256(FLUIDSYNTH), toolchain["fluidsynth"]["executable_sha256"])
        if FFMPEG.is_file():
            self.assertEqual(sha256(FFMPEG), toolchain["ffmpeg"]["executable_sha256"])
        self.assertEqual(toolchain["ffmpeg"]["version"], "9.0.1")
        self.assertEqual(toolchain["ffmpeg"]["settings"], json.loads((ROOT / "music/delibes-mix.json").read_text())["master"]["normalization"])
        notice = toolchain["soundfont"]["license_notice"]
        self.assertEqual(sha256(ROOT / notice["path"]), notice["sha256"])
        profile = uses["profiles"][uses["competition_profile"]]
        self.assertTrue(profile["package_eligible"])
        self.assertNotIn("private-grain-bank", {row["kind"] for row in profile["declared_sources"]})
        self.assertFalse(uses["profiles"]["hybrid-apartment"]["package_eligible"])

    def test_adapted_midi_is_native_tempo_chamber_score(self) -> None:
        exports = [ROOT / ".work/music/Valse-Lente-Delibes.mid", ROOT / ".work/music/Valse-Coppelia.mid"]
        if all(path.is_file() for path in exports):
            sylvia, coppelia = validate_adaptation_inputs(exports)
            first, details = build(sylvia, coppelia)
            second, repeated = build(sylvia, coppelia)
            self.assertEqual(first, second)
            self.assertEqual(details, repeated)
            self.assertEqual(hashlib.sha256(first).hexdigest(), EXPECTED_MIDI)
            self.assertEqual(first, (ROOT / "music/delibes-screendance-suite.mid").read_bytes())

        parsed = parse_midi(ROOT / "music/delibes-screendance-suite.mid")
        self.assertEqual(parsed.division, 480)
        self.assertEqual(len(parsed.tracks), 8)
        self.assertEqual(max(parsed.ends), 426240)
        programs = {
            (track, event.status & 0x0F, event.data[0])
            for track, events in enumerate(parsed.tracks)
            for event in events
            if event.kind == "channel" and event.status >> 4 == 0xC
        }
        self.assertEqual(
            programs,
            {(1, 0, 40), (2, 1, 40), (3, 2, 41), (4, 3, 42), (5, 4, 43), (7, 5, 47)},
        )
        triangle = [
            event.tick
            for event in parsed.tracks[6]
            if event.kind == "channel" and event.status == 0x99 and event.data == bytes((81, 70))
        ]
        self.assertEqual(triangle, [11520, 24480, 37440, 50400, 89280])

    def test_selected_register_is_schema_valid_and_hydration_is_fail_closed(self) -> None:
        register = load_register()
        self.assertEqual(validate_schema_instance(register), [])
        work = register["works"][0]
        self.assertEqual(register["artistic_gate"]["status"], "accepted")
        self.assertEqual((work["id"], work["role"], work["selection"]["status"]), (
            "delibes-screendance-suite",
            "repertoire",
            "selected",
        ))
        self.assertEqual(work["recording"]["status"], "pending-render")
        self.assertEqual(work["samples"]["items"][0]["source"]["custody"], "hydrated-local")

        missing = copy.deepcopy(register)
        missing["works"][0]["samples"]["items"][0]["source"]["path"] = ".work/music/absent.sf3"
        errors = validate_document(missing, check_derived=False, require_hydrated=True)
        self.assertTrue(any("required hydrated bytes are absent" in error for error in errors), errors)

        fixture = copy.deepcopy(register)
        fixture["works"][0]["role"] = "fixture"
        fixture["works"][0]["selection"]["status"] = "selected"
        errors = validate_document(fixture, check_derived=False)
        self.assertTrue(any("contract fixture cannot be selected" in error for error in errors), errors)

    def test_render_rejects_fixture_before_touching_audio_sources(self) -> None:
        args = SimpleNamespace(score=ROOT / "music/score.json")
        fixture = {
            "schema": "danse.music.score.v1",
            "release_status": "fixture-only",
            "time": {"passage_mapping": "restart-and-affine-stretch"},
        }
        with mock.patch("render_music.load_score", return_value=fixture):
            with self.assertRaisesRegex(ValueError, "non-fixture native-tempo"):
                validate_render_inputs(args)

    def test_render_contract_paths_reject_traversal_backslashes_and_symlinks(self) -> None:
        for path in ("music/../README.md", "music\\README.md", "./music/README.md"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "canonical POSIX repository-relative"):
                    require_hash({"path": path, "sha256": "0" * 64}, "fixture")
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as temporary:
            directory = Path(temporary)
            target = directory / "target.bin"
            target.write_bytes(b"custody")
            link = directory / "link.bin"
            link.symlink_to(target)
            relative = link.relative_to(ROOT).as_posix()
            with self.assertRaisesRegex(ValueError, "non-symlink regular file"):
                require_hash({"path": relative, "sha256": hashlib.sha256(b"custody").hexdigest()}, "fixture")

    def test_loudness_gate_and_schema_reject_out_of_target_receipts(self) -> None:
        settings = json.loads((ROOT / "music/delibes-mix.json").read_text())["master"]["normalization"]
        self.assertEqual(loudness_gate({"integrated_lufs": -15.92, "true_peak_dbtp": -1.0}, settings), (True, True))
        self.assertEqual(loudness_gate({"integrated_lufs": -31.8, "true_peak_dbtp": -10.58}, settings), (False, True))
        self.assertEqual(loudness_gate({"integrated_lufs": -15.92, "true_peak_dbtp": -0.99}, settings), (True, False))

        receipt_path = ROOT / ".work/music/competition/audio-render.json"
        if not receipt_path.is_file():
            return
        receipt = json.loads(receipt_path.read_text())
        schema = json.loads((ROOT / "music/audio-render.schema.json").read_text())
        validator = Draft202012Validator(schema)
        bad = copy.deepcopy(receipt)
        bad["normalization"]["output"]["integrated_lufs"] = -31.8
        bad["verification"]["loudness_in_target"] = True
        self.assertTrue(list(validator.iter_errors(bad)))

    def test_final_audio_receipt_if_present_binds_every_input_and_output(self) -> None:
        path = ROOT / ".work/music/competition/audio-render.json"
        if not path.is_file():
            self.skipTest("final ignored audio receipt not present")
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["schema"], "danse.audio.render.v1")
        self.assertEqual(receipt["profile"], "competition-classical")
        for name, row in receipt["inputs"].items():
            source = Path(row["path"])
            source = source if source.is_absolute() else ROOT / source
            with self.subTest(input=name):
                self.assertTrue(source.is_file())
                self.assertEqual(sha256(source), row["sha256"])
        schema = json.loads((ROOT / "music/audio-render.schema.json").read_text())
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(receipt)), [])
        outputs = [
            receipt["outputs"]["pre_normalized_master"],
            receipt["outputs"]["master"],
            *receipt["outputs"]["stems"],
        ]
        for row in outputs:
            source = ROOT / row["path"]
            with self.subTest(output=row.get("id", "master")):
                self.assertEqual(sha256(source), row["sha256"])
                self.assertTrue(row["non_silent"])
                self.assertGreaterEqual(row["peak_sample"], 32)
                self.assertGreaterEqual(row["rms_sample"], 1)
                self.assertEqual(row["frames"], 16843024)
        verification = receipt["verification"]
        for key in (
            "deterministic",
            "non_silent",
            "stems_non_silent",
            "polyphonic",
            "normalization_deterministic",
            "loudness_in_target",
            "true_peak_in_target",
            "duration_matches_score",
            "seek_safe",
        ):
            self.assertIs(verification[key], True, key)
        self.assertEqual(verification["repeat_master_sha256"], receipt["outputs"]["master"]["sha256"])
        loudness_ok, peak_ok = loudness_gate(receipt["normalization"]["output"], receipt["normalization"]["targets"] | {
            "target_lufs": receipt["normalization"]["targets"]["integrated_lufs"],
            "max_true_peak_dbtp": receipt["normalization"]["targets"]["max_true_peak_dbtp"],
        })
        self.assertTrue(loudness_ok)
        self.assertTrue(peak_ok)


@unittest.skipUnless(FLUIDSYNTH.is_file() and SOUNDFONT.is_file(), "pinned FluidSynth/SF3 not hydrated")
class FluidSynthDeterminismTest(unittest.TestCase):
    def test_two_short_renders_are_identical_non_silent_polyphonic_and_seek_safe(self) -> None:
        division = 480
        end = 960
        conductor = [
            meta(0, 0x03, "test conductor", 0, 0),
            meta(0, 0x51, (500000).to_bytes(3, "big"), 10, 0),
        ]
        violin = [
            meta(0, 0x03, "violin", 0, 0),
            channel(0, 0xC0, bytes((40,)), 30, 0),
            channel(0, 0x90, bytes((60, 96)), 50, 0),
            channel(0, 0x90, bytes((64, 88)), 50, 1),
            channel(720, 0x80, bytes((60, 0)), 50, 2),
            channel(720, 0x80, bytes((64, 0)), 50, 3),
        ]
        cello = [
            meta(0, 0x03, "cello", 0, 0),
            channel(0, 0xC1, bytes((42,)), 30, 0),
            channel(0, 0x91, bytes((48, 92)), 50, 0),
            channel(720, 0x81, bytes((48, 0)), 50, 1),
        ]
        midi_bytes = b"MThd" + struct.pack(">IHHH", 6, 1, 3, division)
        midi_bytes += write_track(conductor, end) + write_track(violin, end) + write_track(cello, end)
        mix = {
            "stems": [
                {"id": "violin", "midi_track": 1, "gain_q16": 58982, "pan_q16": -8192},
                {"id": "cello", "midi_track": 2, "gain_q16": 55706, "pan_q16": 8192},
            ],
            "master": {"gain_q16": 49152, "peak_ceiling_q16": 58409},
        }
        frames = 48000
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            midi = root / "test.mid"
            midi.write_bytes(midi_bytes)
            first_rows, first_paths = render_stems(
                root / "first/stems",
                midi=midi,
                mix=mix,
                fluidsynth=FLUIDSYNTH,
                soundfont=SOUNDFONT,
                frames=frames,
                sample_rate=48000,
                gain="0.5",
            )
            second_rows, second_paths = render_stems(
                root / "second/stems",
                midi=midi,
                mix=mix,
                fluidsynth=FLUIDSYNTH,
                soundfont=SOUNDFONT,
                frames=frames,
                sample_rate=48000,
                gain="0.5",
            )
            self.assertEqual([row["sha256"] for row in first_rows], [row["sha256"] for row in second_rows])
            self.assertTrue(all(row["non_silent"] for row in first_rows))
            first = mix_stems(first_paths, root / "first/master.wav", mix=mix, frames=frames, sample_rate=48000)
            second = mix_stems(second_paths, root / "second/master.wav", mix=mix, frames=frames, sample_rate=48000)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue(first["non_silent"])
            self.assertGreater(first["polyphonic_frames"], 0)
            self.assertEqual(first["frames"], frames)
            self.assertEqual(
                pcm_slice_hash(root / "first/master.wav", 4000, 12000),
                pcm_slice_hash(root / "second/master.wav", 4000, 12000),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

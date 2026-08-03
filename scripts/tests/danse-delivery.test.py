#!/usr/bin/env python3
"""Portable regression tests for Danse's delivery-trunk interfaces."""

from __future__ import annotations

import hashlib
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
_prior_corpus_contract = sys.modules.get("corpus_contract")
sys.modules["corpus_contract"] = CORPUS_CONTRACT
try:
    CORPUS_PIPELINE = load("danse_corpus_pipeline_test", ROOT / "pipeline/4_corpus.py")
finally:
    if _prior_corpus_contract is None:
        del sys.modules["corpus_contract"]
    else:
        sys.modules["corpus_contract"] = _prior_corpus_contract
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


def corpus_fixture(root: Path) -> tuple[Path, Path]:
    work = root / "work"
    out = root / "out"
    raw = work / "raw/IMG_1570.png"
    mask = work / "vision/mask/IMG_1570.png"
    pose = work / "vision/pose/IMG_1570.json"
    raw.parent.mkdir(parents=True)
    mask.parent.mkdir(parents=True)
    pose.parent.mkdir(parents=True)
    CORPUS_PIPELINE.Image.init()
    CORPUS_PIPELINE.Image.new("RGB", (4, 3), "white").save(raw, "PNG")
    CORPUS_PIPELINE.Image.new("L", (4, 3), 255).save(mask, "PNG")
    pose.write_text("{}")
    return work, out


def corpus_public_manifest(work: Path) -> dict:
    items, incomplete = CORPUS_CONTRACT.frame_inventory(work)
    assert not incomplete and len(items) == 1
    fid, raw, mask, pose = items[0]
    with CORPUS_PIPELINE.Image.open(raw) as image:
        native = list(image.size)
    return {
        "schema": "danse.corpus.v1",
        "camera": native,
        "tiers": {name: {"sentinel": name} for name in CORPUS_PIPELINE.SHIPPED},
        "score": None,
        "frames": [
            {
                "id": fid,
                "source": raw.name,
                "native": native,
                "registered": True,
                "figure": CORPUS_PIPELINE.figure_geometry(mask),
                "joints": CORPUS_PIPELINE.joints_of(pose),
                "score_area": 0.0,
            }
        ],
        "sentinel": "public bytes must survive",
    }


def run_corpus_pipeline(work: Path, out: Path, tiers: str, *extra: str) -> int:
    argv = ["4_corpus.py", "--work", str(work), "--out", str(out), "--skip-room", "--tiers", tiers, *extra]
    with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return CORPUS_PIPELINE.main()


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

    def test_offline_render_rejects_an_unauthorized_tier_before_capture(self) -> None:
        render = mock.Mock(side_effect=AssertionError("unauthorized tier reached the renderer"))
        authorization = mock.Mock(return_value=(False, "stale receipt"))
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(sys, "argv", ["render.py", "--segment", "0"]),
                mock.patch.object(OFFLINE, "authorize_render_tier", authorization),
                mock.patch.object(OFFLINE, "render_segment", render),
                mock.patch.dict(OFFLINE.os.environ, {"DANSE_WORK": tmp}, clear=True),
                redirect_stderr(io.StringIO()) as error,
            ):
                self.assertEqual(OFFLINE.main(), 1)
            authorization.assert_called_once_with(OFFLINE.APP / "corpus", Path(tmp), "screen")
        self.assertFalse(render.called)
        self.assertIn("stale receipt", error.getvalue())

    def test_render_and_delivery_source_identities_bind_the_tier_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "corpus/tier-receipts/screen.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_bytes(b"first receipt")

            with mock.patch.object(OFFLINE, "APP", root):
                first = OFFLINE.source_tree_sha256(SimpleNamespace(tier="screen"))
                receipt.write_bytes(b"second receipt")
                second = OFFLINE.source_tree_sha256(SimpleNamespace(tier="screen"))
            self.assertNotEqual(first, second)

            render_dir = root / "render"
            render_dir.mkdir()
            program = render_dir / "program.json"
            bank = root / "sound/bank/bank.json"
            bank.parent.mkdir(parents=True)
            program.write_text("{}")
            bank.write_text("{}")
            DELIVER.delivery_source_sha256.cache_clear()
            with (
                mock.patch.object(DELIVER, "DANSE", root),
                mock.patch.object(DELIVER, "HERE", render_dir),
                mock.patch.object(DELIVER, "PROGRAM", program),
                mock.patch.object(DELIVER, "BANK", bank),
            ):
                third = DELIVER.delivery_source_sha256("screen")
                receipt.write_bytes(b"third receipt")
                DELIVER.delivery_source_sha256.cache_clear()
                fourth = DELIVER.delivery_source_sha256("screen")
            DELIVER.delivery_source_sha256.cache_clear()
            self.assertNotEqual(third, fourth)

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
          import { fromData } from './engine/corpus.js';
          const zero = numericParam(new URLSearchParams('s=0'), 's', 99, {integer:true,min:0});
          let invalid = false;
          try { numericParam(new URLSearchParams('s=nope'), 's', 99, {integer:true,min:0}); } catch { invalid = true; }
          const corpus = {
            ensure: async () => {},
            has: (kind) => kind === 'plates',
          };
          let missingMatte = false;
          try { await requireTier(corpus, 'film', ['IMG_1570']); } catch { missingMatte = true; }
          const requested = [];
          globalThis.Image = class { set src(value) { requested.push(value); } };
          const progressive = fromData('/corpus/', {
            tiers: {browse:{width:512,eager:true}, screen:{width:1024,eager:false}},
            frames: [],
          });
          const fallback = {};
          progressive.textures.set('plates/browse/IMG_1570', fallback);
          const got = progressive.get(null, 'plates', 'IMG_1570', 'screen');
          progressive.get(null, 'plates', 'IMG_1570', 'screen');
          console.log(JSON.stringify({
            zero, invalid, missingMatte,
            progressiveFallback: got === fallback,
            progressiveRequests: requested,
          }));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            json.loads(done.stdout),
            {
                "zero": 0,
                "invalid": True,
                "missingMatte": True,
                "progressiveFallback": True,
                "progressiveRequests": ["/corpus/plates/screen/IMG_1570.webp"],
            },
        )

    def test_progressive_tier_failure_is_cached_until_invalidation(self) -> None:
        script = """
          import { fromData } from './engine/corpus.js';
          const requested = [];
          let shouldFail = true;
          globalThis.Image = class {
            set src(value) {
              requested.push(value);
              const fail = shouldFail;
              queueMicrotask(() => fail ? this.onerror() : this.onload());
            }
          };
          const settle = async () => {
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
          };
          const corpus = fromData('/corpus/', {
            tiers: {browse:{width:512,eager:true}, screen:{width:1024,eager:false}},
            frames: [],
          });
          const key = 'plates/screen/IMG_1570';
          const fallbackKey = 'plates/browse/IMG_1570';
          const fallback = {};
          corpus.textures.set(fallbackKey, fallback);

          const firstFallback = corpus.get(null, 'plates', 'IMG_1570', 'screen') === fallback;
          await settle();
          const repeatedFallback = Array.from(
            {length: 20},
            () => corpus.get(null, 'plates', 'IMG_1570', 'screen') === fallback,
          ).every(Boolean);
          const requestsAfterFailure = requested.length;

          shouldFail = false;
          await corpus.ensure('plates', 'screen', ['IMG_1570']);
          const recovered = corpus.has('plates', 'screen', 'IMG_1570') && !corpus.failed.has(key);
          corpus.images.delete(key);

          shouldFail = true;
          const recoveredFallback = corpus.get(null, 'plates', 'IMG_1570', 'screen') === fallback;
          await settle();
          corpus.get(null, 'plates', 'IMG_1570', 'screen');
          const requestsAfterRecoveryFailure = requested.length;

          corpus.invalidate();
          corpus.textures.set(fallbackKey, fallback);
          const invalidatedFallback = corpus.get(null, 'plates', 'IMG_1570', 'screen') === fallback;
          const requestsAfterInvalidation = requested.length;

          console.log(JSON.stringify({
            firstFallback,
            repeatedFallback,
            requestsAfterFailure,
            recovered,
            recoveredFallback,
            requestsAfterRecoveryFailure,
            invalidatedFallback,
            requestsAfterInvalidation,
          }));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            json.loads(done.stdout),
            {
                "firstFallback": True,
                "repeatedFallback": True,
                "requestsAfterFailure": 1,
                "recovered": True,
                "recoveredFallback": True,
                "requestsAfterRecoveryFailure": 3,
                "invalidatedFallback": True,
                "requestsAfterInvalidation": 4,
            },
        )

    def test_invalidation_starts_a_fresh_request_while_the_old_one_is_pending(self) -> None:
        script = """
          import { fromData } from './engine/corpus.js';
          const requested = [];
          globalThis.Image = class {
            set src(value) { this.url = value; requested.push(this); }
          };
          const settle = async () => {
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
          };
          const corpus = fromData('/corpus/', {
            tiers: {browse:{width:512,eager:true}, screen:{width:1024,eager:false}},
            frames: [],
          });
          const key = 'plates/screen/IMG_1570';
          const fallbackKey = 'plates/browse/IMG_1570';
          const fallback = {};
          corpus.textures.set(fallbackKey, fallback);
          corpus.get(null, 'plates', 'IMG_1570', 'screen');
          const firstEpoch = corpus.failed;

          corpus.invalidate();
          corpus.textures.set(fallbackKey, fallback);
          corpus.get(null, 'plates', 'IMG_1570', 'screen');
          const secondEpoch = corpus.failed;
          const freshRequestStarted = requested.length === 2 && firstEpoch !== secondEpoch;
          const newRequestOwnsPending = corpus.pending.get(key) === secondEpoch;

          requested[0].onerror();
          await settle();
          const oldCompletionPreservesPending = corpus.pending.get(key) === secondEpoch;
          const oldFailureDidNotPoison = !secondEpoch.has(key);
          corpus.get(null, 'plates', 'IMG_1570', 'screen');
          const repeatedGetDeduplicated = requested.length === 2;

          requested[1].onerror();
          await settle();
          console.log(JSON.stringify({
            freshRequestStarted,
            newRequestOwnsPending,
            oldCompletionPreservesPending,
            oldFailureDidNotPoison,
            repeatedGetDeduplicated,
            currentFailureRecorded: corpus.failed.has(key),
            pendingCleared: !corpus.pending.has(key),
          }));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            json.loads(done.stdout),
            {
                "freshRequestStarted": True,
                "newRequestOwnsPending": True,
                "oldCompletionPreservesPending": True,
                "oldFailureDidNotPoison": True,
                "repeatedGetDeduplicated": True,
                "currentFailureRecorded": True,
                "pendingCleared": True,
            },
        )

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
            source_identity = CORPUS_CONTRACT.corpus_source_identity(items)
            tier_identity = CORPUS_CONTRACT.tier_source_identity(source_identity, {"width": 512}, 85)
            raw.write_bytes(b"corrected original")
            self.assertNotEqual(first, CORPUS_CONTRACT.room_cache_key(items))
            self.assertNotEqual(source_receipt, CORPUS_CONTRACT.source_set_receipt([raw]))
            self.assertNotEqual(source_identity, CORPUS_CONTRACT.corpus_source_identity(items))
            self.assertNotEqual(tier_identity, CORPUS_CONTRACT.tier_source_identity(source_identity, {"width": 1024}, 85))

    def test_tier_output_identity_rejects_missing_and_extra_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plate = root / "plates/browse/IMG_1570.webp"
            matte = root / "mattes/browse/IMG_1570.webp"
            plate.parent.mkdir(parents=True)
            matte.parent.mkdir(parents=True)
            plate.write_bytes(b"plate bytes")
            matte.write_bytes(b"matte bytes")

            identity = CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"])
            self.assertIsNotNone(identity)
            linked_root = root / "linked-root"
            linked_root.symlink_to(root, target_is_directory=True)
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(linked_root, "browse", ["IMG_1570"]))
            matte.unlink()
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"]))
            matte.write_bytes(b"matte bytes")
            surplus = matte.parent / "unexpected.webp"
            surplus.write_bytes(b"surplus")
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"]))
            surplus.unlink()

            outside = root / "outside.webp"
            outside.write_bytes(b"bytes outside the corpus tier")
            matte.unlink()
            matte.symlink_to(outside)
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"]))
            matte.unlink()
            matte.write_bytes(b"matte bytes")

            outside_tier = root / "outside-tier"
            outside_tier.mkdir()
            (outside_tier / plate.name).write_bytes(b"plate bytes")
            plate.unlink()
            plate.parent.rmdir()
            plate.parent.symlink_to(outside_tier, target_is_directory=True)
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"]))

            plate.parent.unlink()
            plate.parent.mkdir()
            plate.write_bytes(b"plate bytes")
            outside_plates = root / "outside-plates"
            (outside_plates / "browse").mkdir(parents=True)
            (outside_plates / "browse" / plate.name).write_bytes(b"plate bytes")
            shutil.rmtree(root / "plates")
            (root / "plates").symlink_to(outside_plates, target_is_directory=True)
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"]))

            (root / "plates").unlink()
            plate.parent.mkdir(parents=True)
            plate.write_bytes(b"plate bytes")
            outside_mattes = root / "outside-mattes"
            (outside_mattes / "browse").mkdir(parents=True)
            (outside_mattes / "browse" / matte.name).write_bytes(b"matte bytes")
            shutil.rmtree(root / "mattes")
            (root / "mattes").symlink_to(outside_mattes, target_is_directory=True)
            self.assertIsNone(CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"]))

    def test_tier_receipt_validator_rejects_mutation_versions_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plate = root / "plates/browse/IMG_1570.webp"
            matte = root / "mattes/browse/IMG_1570.webp"
            receipt = root / "tier-receipts/browse.json"
            plate.parent.mkdir(parents=True)
            matte.parent.mkdir(parents=True)
            receipt.parent.mkdir(parents=True)
            plate.write_bytes(b"plate bytes")
            matte.write_bytes(b"matte bytes")
            output = CORPUS_CONTRACT.tier_output_identity(root, "browse", ["IMG_1570"])
            payload = {
                "schema": "danse.corpus.tier-receipt.v2",
                "tier": "browse",
                "source_sha256": "1" * 64,
                "output_sha256": output,
            }
            receipt.write_text(json.dumps(payload))
            self.assertTrue(CORPUS_CONTRACT.tier_receipt_is_current(root, "browse", ["IMG_1570"]))

            plate.write_bytes(b"mutated")
            self.assertFalse(CORPUS_CONTRACT.tier_receipt_is_current(root, "browse", ["IMG_1570"]))
            plate.write_bytes(b"plate bytes")
            payload["schema"] = "danse.corpus.tier-receipt.v1"
            receipt.write_text(json.dumps(payload))
            self.assertFalse(CORPUS_CONTRACT.tier_receipt_is_current(root, "browse", ["IMG_1570"]))

            payload["schema"] = "danse.corpus.tier-receipt.v2"
            target = root / "receipt-target.json"
            target.write_text(json.dumps(payload))
            receipt.unlink()
            receipt.symlink_to(target)
            self.assertFalse(CORPUS_CONTRACT.tier_receipt_is_current(root, "browse", ["IMG_1570"]))

            receipt.unlink()
            receipt.write_text(json.dumps(payload))
            external_receipts = root / "external-tier-receipts"
            receipt.parent.rename(external_receipts)
            receipt.parent.symlink_to(external_receipts, target_is_directory=True)
            self.assertFalse(CORPUS_CONTRACT.tier_receipt_is_current(root, "browse", ["IMG_1570"]))

    def test_tracked_shipped_tier_receipts_match_every_committed_byte(self) -> None:
        manifest = json.loads((ROOT / "corpus/manifest.json").read_text())
        ids = [frame["id"] for frame in manifest["frames"]]
        for tier in ("browse", "screen"):
            with self.subTest(tier=tier):
                self.assertTrue(CORPUS_CONTRACT.tier_receipt_is_current(ROOT / "corpus", tier, ids))

    def test_local_render_authorization_binds_hydrated_sources_and_output_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, corpus = corpus_fixture(root)
            public = corpus_public_manifest(work)
            corpus.mkdir(parents=True)
            (corpus / "manifest.json").write_text(json.dumps(public))
            plate = corpus / "plates/film/IMG_1570.webp"
            matte = corpus / "mattes/film/IMG_1570.webp"
            plate.parent.mkdir(parents=True)
            matte.parent.mkdir(parents=True)
            plate.write_bytes(b"film plate")
            matte.write_bytes(b"film matte")
            nbytes = plate.stat().st_size + matte.stat().st_size
            (corpus / "manifest.local.json").write_text(
                json.dumps(
                    {
                        "schema": "danse.corpus.local.v1",
                        "tiers": {"film": CORPUS_CONTRACT.tier_manifest_entry("film", nbytes)},
                    }
                )
            )
            items, incomplete = CORPUS_CONTRACT.frame_inventory(work)
            self.assertFalse(incomplete)
            source = CORPUS_CONTRACT.tier_source_identity(
                CORPUS_CONTRACT.corpus_source_identity(items),
                CORPUS_CONTRACT.TIER_SPECS["film"],
                CORPUS_CONTRACT.MATTE_QUALITY,
            )
            receipt = corpus / "tier-receipts/film.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "danse.corpus.tier-receipt.v2",
                        "tier": "film",
                        "source_sha256": source,
                        "output_sha256": CORPUS_CONTRACT.tier_output_identity(
                            corpus, "film", ["IMG_1570"]
                        ),
                    }
                )
            )
            self.assertEqual(
                CORPUS_CONTRACT.authorize_render_tier(corpus, work, "film"),
                (True, "1 exact plate+matte pairs"),
            )

            plate.write_bytes(b"FILM PLATE")
            allowed, detail = CORPUS_CONTRACT.authorize_render_tier(corpus, work, "film")
            self.assertFalse(allowed)
            self.assertIn("receipt", detail)

            plate.write_bytes(b"film plate")
            raw = work / "raw/IMG_1570.png"
            raw.unlink()
            raw.symlink_to(work / "raw/missing.png")
            allowed, detail = CORPUS_CONTRACT.authorize_render_tier(corpus, work, "film")
            self.assertFalse(allowed)
            self.assertIn("source bytes are unreadable", detail)

    def test_tier_retention_rejects_mutated_bytes_and_source_only_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, out = corpus_fixture(root)
            plate = out / "plates/browse/IMG_1570.webp"
            matte = out / "mattes/browse/IMG_1570.webp"
            receipt = out / "tier-receipts/browse.json"
            plate.parent.mkdir(parents=True)
            matte.parent.mkdir(parents=True)
            receipt.parent.mkdir(parents=True)
            plate.write_bytes(b"encoded plate")
            matte.write_bytes(b"encoded matte")
            items, incomplete = CORPUS_CONTRACT.frame_inventory(work)
            self.assertFalse(incomplete)
            source = CORPUS_CONTRACT.tier_source_identity(
                CORPUS_CONTRACT.corpus_source_identity(items),
                CORPUS_PIPELINE.TIERS["browse"],
                CORPUS_PIPELINE.MATTE_QUALITY,
            )
            output = CORPUS_CONTRACT.tier_output_identity(out, "browse", ["IMG_1570"])
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "danse.corpus.tier-receipt.v2",
                        "tier": "browse",
                        "source_sha256": source,
                        "output_sha256": output,
                    }
                )
            )

            self.assertEqual(run_corpus_pipeline(work, out, ""), 0)
            self.assertEqual(set(json.loads((out / "manifest.json").read_text())["tiers"]), {"browse"})

            plate.write_bytes(b"mutated plate")
            self.assertEqual(run_corpus_pipeline(work, out, ""), 0)
            self.assertEqual(json.loads((out / "manifest.json").read_text())["tiers"], {})

            plate.write_bytes(b"encoded plate")
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "danse.corpus.tier-receipt.v1",
                        "tier": "browse",
                        "source_sha256": source,
                    }
                )
            )
            self.assertEqual(run_corpus_pipeline(work, out, ""), 0)
            self.assertEqual(json.loads((out / "manifest.json").read_text())["tiers"], {})

    def test_partial_shipped_rebuild_retains_only_receipted_current_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, out = corpus_fixture(root)
            screen_plate = out / "plates/screen/IMG_1570.webp"
            screen_matte = out / "mattes/screen/IMG_1570.webp"
            screen_receipt = out / "tier-receipts/screen.json"
            screen_plate.parent.mkdir(parents=True)
            screen_matte.parent.mkdir(parents=True)
            screen_receipt.parent.mkdir(parents=True)
            screen_plate.write_bytes(b"screen plate")
            screen_matte.write_bytes(b"screen matte")
            items, incomplete = CORPUS_CONTRACT.frame_inventory(work)
            self.assertFalse(incomplete)
            screen_source = CORPUS_CONTRACT.tier_source_identity(
                CORPUS_CONTRACT.corpus_source_identity(items),
                CORPUS_PIPELINE.TIERS["screen"],
                CORPUS_PIPELINE.MATTE_QUALITY,
            )
            screen_receipt.write_text(
                json.dumps(
                    {
                        "schema": "danse.corpus.tier-receipt.v2",
                        "tier": "screen",
                        "source_sha256": screen_source,
                        "output_sha256": CORPUS_CONTRACT.tier_output_identity(
                            out, "screen", ["IMG_1570"]
                        ),
                    }
                )
            )

            def fake_encode(_src, dest, *_args, **_kwargs):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(dest.relative_to(out).as_posix().encode())
                return dest.stat().st_size

            with mock.patch.object(CORPUS_PIPELINE, "encode", side_effect=fake_encode):
                self.assertEqual(run_corpus_pipeline(work, out, "browse"), 0)
                self.assertEqual(set(json.loads((out / "manifest.json").read_text())["tiers"]), {"browse", "screen"})

                screen_plate.write_bytes(b"mutated screen plate")
                self.assertEqual(run_corpus_pipeline(work, out, "browse"), 0)
                self.assertEqual(set(json.loads((out / "manifest.json").read_text())["tiers"]), {"browse"})

    def test_limited_smoke_build_isolated_from_canonical_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, canonical = corpus_fixture(root)
            second_raw = work / "raw/IMG_1571.png"
            second_mask = work / "vision/mask/IMG_1571.png"
            second_pose = work / "vision/pose/IMG_1571.json"
            CORPUS_PIPELINE.Image.new("RGB", (4, 3), "black").save(second_raw, "PNG")
            CORPUS_PIPELINE.Image.new("L", (4, 3), 0).save(second_mask, "PNG")
            second_pose.write_text("{}")
            canonical.mkdir(parents=True)
            sentinel = canonical / "manifest.json"
            sentinel.write_bytes(b"canonical corpus bytes")

            def fake_encode(_src, dest, *_args, **_kwargs):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(dest.as_posix().encode())
                return dest.stat().st_size

            argv = ["4_corpus.py", "--work", str(work), "--limit", "1", "--skip-room"]
            with (
                mock.patch.object(CORPUS_PIPELINE, "OUT", canonical),
                mock.patch.object(CORPUS_PIPELINE, "encode", side_effect=fake_encode),
                mock.patch.object(sys, "argv", argv),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(CORPUS_PIPELINE.main(), 0)
            self.assertEqual(sentinel.read_bytes(), b"canonical corpus bytes")
            smoke = work / "corpus-smoke-1"
            self.assertEqual(set(json.loads((smoke / "manifest.json").read_text())["tiers"]), {"browse", "screen"})

            extra = smoke / "plates/browse/old-extra.webp"
            extra.write_bytes(b"stale smoke output")
            with (
                mock.patch.object(CORPUS_PIPELINE, "OUT", canonical),
                mock.patch.object(CORPUS_PIPELINE, "encode", side_effect=fake_encode),
                mock.patch.object(sys, "argv", argv),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(CORPUS_PIPELINE.main(), 0)
            self.assertFalse(extra.exists())
            self.assertEqual(sentinel.read_bytes(), b"canonical corpus bytes")

            external = root / "external-plates"
            (external / "browse").mkdir(parents=True)
            outside_sentinel = external / "browse/DO_NOT_DELETE"
            outside_sentinel.write_bytes(b"outside smoke custody")
            shutil.rmtree(smoke / "plates")
            (smoke / "plates").symlink_to(external, target_is_directory=True)
            with (
                mock.patch.object(CORPUS_PIPELINE, "OUT", canonical),
                mock.patch.object(CORPUS_PIPELINE, "encode", side_effect=fake_encode),
                mock.patch.object(sys, "argv", argv),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(CORPUS_PIPELINE.main(), 0)
            self.assertEqual(outside_sentinel.read_bytes(), b"outside smoke custody")

            explicit = [*argv, "--out", str(canonical)]
            encoder = mock.Mock(side_effect=AssertionError("canonical smoke target encoded"))
            with (
                mock.patch.object(CORPUS_PIPELINE, "OUT", canonical),
                mock.patch.object(CORPUS_PIPELINE, "encode", encoder),
                mock.patch.object(sys, "argv", explicit),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(CORPUS_PIPELINE.main(), 1)
            self.assertFalse(encoder.called)
            self.assertEqual(sentinel.read_bytes(), b"canonical corpus bytes")

    def test_interrupted_tier_rebuild_cannot_retain_its_old_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, out = corpus_fixture(root)
            public = out / "manifest.json"
            public.parent.mkdir(parents=True)
            public_bytes = (json.dumps(corpus_public_manifest(work), indent=1) + "\n").encode()
            public.write_bytes(public_bytes)
            receipt = out / "tier-receipts/film.json"
            local = out / "manifest.local.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text('{"schema":"danse.corpus.tier-receipt.v2"}')
            local.write_text('{"schema":"danse.corpus.local.v1"}')

            with mock.patch.object(CORPUS_PIPELINE, "encode", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    run_corpus_pipeline(work, out, "film")
            self.assertEqual(public.read_bytes(), public_bytes)
            self.assertFalse(receipt.exists())
            self.assertFalse(local.exists())

    def test_local_tier_build_preserves_the_compatible_public_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, out = corpus_fixture(root)
            public = out / "manifest.json"
            public.parent.mkdir(parents=True)
            public_bytes = (json.dumps(corpus_public_manifest(work), indent=1) + "\n").encode()
            public.write_bytes(public_bytes)

            def fake_encode(_src, dest, *_args, **_kwargs):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(dest.relative_to(out).as_posix().encode())
                return dest.stat().st_size

            with mock.patch.object(CORPUS_PIPELINE, "encode", side_effect=fake_encode):
                self.assertEqual(run_corpus_pipeline(work, out, "film"), 0)

            self.assertEqual(public.read_bytes(), public_bytes)
            local = json.loads((out / "manifest.local.json").read_text())
            self.assertEqual(set(local["tiers"]), {"film"})
            receipt = json.loads((out / "tier-receipts/film.json").read_text())
            self.assertEqual(receipt["schema"], "danse.corpus.tier-receipt.v2")
            self.assertEqual(len(receipt["output_sha256"]), 64)
            self.assertIn("tier-receipts/film.json", (ROOT / "corpus/.gitignore").read_text().splitlines())

    def test_local_tier_mismatch_preserves_prior_authorization_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work, out = corpus_fixture(root)
            public = out / "manifest.json"
            incompatible = corpus_public_manifest(work)
            incompatible["frames"][0]["source"] = "different-source.png"
            public.parent.mkdir(parents=True)
            public_bytes = (json.dumps(incompatible, indent=1) + "\n").encode()
            public.write_bytes(public_bytes)
            receipt = out / "tier-receipts/film.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_bytes(b"prior film receipt")
            local = out / "manifest.local.json"
            local.write_bytes(b"prior local manifest")
            encoder = mock.Mock(side_effect=AssertionError("incompatible build encoded output"))

            with mock.patch.object(CORPUS_PIPELINE, "encode", encoder):
                self.assertEqual(run_corpus_pipeline(work, out, "film"), 1)

            self.assertFalse(encoder.called)
            self.assertEqual(public.read_bytes(), public_bytes)
            self.assertEqual(receipt.read_bytes(), b"prior film receipt")
            self.assertEqual(local.read_bytes(), b"prior local manifest")

    def test_unregistered_recording_cannot_become_room_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "unregistered.mov"
            candidate.write_bytes(b"private recording bytes")
            self.assertEqual(RESOLVE.room_content_matches(candidate, None), (False, None))
            expected = RESOLVE.sha256_file(candidate)
            self.assertEqual(RESOLVE.room_content_matches(candidate, expected), (True, expected))
            self.assertEqual(RESOLVE.room_content_matches(candidate, "0" * 64), (False, expected))

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

            marker = work / "vision/.incomplete"
            marker.write_text("danse.vision.incomplete\n")
            marked_complete, marked_incomplete = CORPUS_CONTRACT.frame_inventory(work)
            self.assertEqual(marked_complete, [])
            self.assertEqual([row[0].name for row in marked_incomplete], ["IMG_1570.JPG", "IMG_1571.JPG"])
            self.assertTrue(all(marker in missing for _, missing in marked_incomplete))
            marker.unlink()

            absent = work / "missing.png"
            self.assertEqual(CORPUS_CONTRACT.missing_measurement_inputs([complete_raw, absent]), [absent])
            self.assertIsNone(CORPUS_CONTRACT.block_shape_error(1024, 768, 16))
            self.assertIn("evenly divide", CORPUS_CONTRACT.block_shape_error(1024, 768, 30))

            readme = (ROOT / "README.md").read_text()
            self.assertIn("../reference/T-2017-full.png", readme)
            self.assertNotIn(".work/reference/T-2017-full.png", readme)

    @unittest.skipUnless(
        sys.platform == "darwin" and (ROOT / "pipeline/1_vision/danse-vision").is_file(),
        "requires the locally built macOS Vision extractor",
    )
    def test_failed_vision_rerun_cannot_retain_any_prior_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            out = root / "vision"
            raw.mkdir()
            for frame_id in ("IMG_1570", "IMG_1571"):
                (raw / f"{frame_id}.jpg").write_bytes(b"not an image")
                pose = out / "pose" / f"{frame_id}.json"
                mask = out / "mask" / f"{frame_id}.png"
                pose.parent.mkdir(parents=True, exist_ok=True)
                mask.parent.mkdir(parents=True, exist_ok=True)
                pose.write_text('{"stale":true}')
                mask.write_bytes(b"stale mask")
            unrelated_pose = out / "pose/NOT_A_DANSE_ARTIFACT.txt"
            unrelated_mask = out / "mask/NOT_A_DANSE_ARTIFACT.txt"
            unrelated_pose.write_text("preserve me")
            unrelated_mask.write_text("preserve me")
            (out / "vision.json").write_text('{"stale":true}')

            done = subprocess.run(
                [str(ROOT / "pipeline/1_vision/danse-vision"), str(raw), str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertFalse((out / "vision.json").exists())
            self.assertTrue((out / ".incomplete").is_file())
            self.assertEqual(unrelated_pose.read_text(), "preserve me")
            self.assertEqual(unrelated_mask.read_text(), "preserve me")
            self.assertFalse(any((out / "pose" / f"{frame_id}.json").exists() for frame_id in ("IMG_1570", "IMG_1571")))
            self.assertFalse(any((out / "mask" / f"{frame_id}.png").exists() for frame_id in ("IMG_1570", "IMG_1571")))

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
            self.assertEqual(SCORE.control_track("passage", 123, 30, 120.0, stream=7), payload)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--from") + 1], "120.0")
        self.assertEqual(command[command.index("--seed") + 1], "123")
        self.assertEqual(command[command.index("--stream") + 1], "7")

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
            nonmedia_probe = mock.Mock(side_effect=AssertionError("text passed to ffprobe"))
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(DELIVER, "query_capture_span", forbidden),
                mock.patch.object(DELIVER, "passage_picture", forbidden),
                mock.patch.object(DELIVER, "passage_sound", forbidden),
                mock.patch.object(DELIVER, "probe", nonmedia_probe),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(DELIVER.main(), 0)
            self.assertFalse(forbidden.called)
            self.assertFalse(nonmedia_probe.called)
            self.assertTrue((out / "package/text/synopsis_short.txt").is_file())
            attest = yaml.safe_load((out / "package/attest.yaml").read_text())
            self.assertTrue(attest)
            self.assertTrue(all(value is None for value in attest.values()))
            items = json.loads((out / "package/manifest.json").read_text())["items"]
            self.assertTrue(items)
            self.assertTrue(all(set(item) == {"name", "bytes", "sha256"} for item in items))

    def test_only_origin_copies_source_bytes_under_stills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "IMG_1594.JPG"
            source.write_bytes(b"camera-original")
            source_digest = DELIVER.digest(source)
            out = root / "render"
            argv = ["deliver.py", "--only", "origin", "--out", str(out)]
            forbidden = mock.Mock(side_effect=AssertionError("render dependency invoked"))
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(DELIVER, "registered_origin", return_value=source),
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=source_digest),
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
            origin_spec = dict(register["package"]["origin_still"], source_sha256=source_digest)
            report = CHECK.Report()
            CHECK.check_origin_still(origin_spec, out / "package", report)
            self.assertEqual(report.failures, 0)
            wrong = CHECK.Report()
            CHECK.check_origin_still({**origin_spec, "source_sha256": "0" * 64}, out / "package", wrong)
            self.assertEqual(wrong.failures, 1)

            source.write_bytes(b"different camera bytes")
            with (
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=source_digest),
                self.assertRaises(SystemExit),
            ):
                DELIVER.deliver_origin(source, True)

    def test_origin_source_is_owned_by_the_submission_register(self) -> None:
        self.assertEqual(
            DELIVER.registered_origin_source_sha256(),
            "72b4f8f1c553c40bd4ec2de9956d547493ed17aaa5eabe172260c2156c8fde42",
        )
        with mock.patch.dict(DELIVER.os.environ, {}, clear=True):
            self.assertEqual(DELIVER.hydrated_work_root(), DELIVER.RAW.parent)
            self.assertEqual(DELIVER.registered_origin(), DELIVER.RAW / "IMG_1594.JPG")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            DELIVER.os.environ, {"DANSE_WORK": tmp}, clear=True
        ):
            self.assertEqual(DELIVER.hydrated_work_root(), Path(tmp))
            self.assertEqual(DELIVER.registered_origin(), Path(tmp) / "raw/IMG_1594.JPG")

    def test_reused_origin_repairs_missing_or_stale_manifest_receipt(self) -> None:
        for prior_receipt in ("missing", "stale"):
            with self.subTest(prior_receipt=prior_receipt), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                out = root / "render"
                package = out / "package"
                origin_copy = package / "stills/origin-2017.jpg"
                origin_copy.parent.mkdir(parents=True)
                origin_copy.write_bytes(b"preserved camera original")
                expected = DELIVER.digest(origin_copy)
                if prior_receipt == "stale":
                    (package / "manifest.json").write_text(
                        json.dumps(
                            {
                                "items": [
                                    {
                                        "name": "stills/origin-2017.jpg",
                                        "source": "wrong.jpg",
                                        "copy_mode": "reencoded",
                                        "sha256": "0" * 64,
                                        "source_sha256": "0" * 64,
                                    }
                                ]
                            }
                        )
                    )
                missing_raw = root / "unmounted/IMG_1594.JPG"
                forbidden = mock.Mock(side_effect=AssertionError("passage dependency invoked"))
                with (
                    mock.patch.object(sys, "argv", ["deliver.py", "--only", "origin", "--out", str(out)]),
                    mock.patch.object(DELIVER, "registered_origin", return_value=missing_raw),
                    mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                    mock.patch.object(DELIVER, "query_capture_span", forbidden),
                    mock.patch.object(DELIVER, "passage_picture", forbidden),
                    mock.patch.object(DELIVER, "passage_sound", forbidden),
                    mock.patch.object(DELIVER, "probe", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(DELIVER.main(), 0)
                self.assertFalse(forbidden.called)
                self.assertEqual(origin_copy.read_bytes(), b"preserved camera original")
                item = next(
                    entry
                    for entry in json.loads((package / "manifest.json").read_text())["items"]
                    if entry["name"] == "stills/origin-2017.jpg"
                )
                self.assertEqual(
                    item,
                    {
                        "name": "stills/origin-2017.jpg",
                        "bytes": len(b"preserved camera original"),
                        "sha256": expected,
                        "source": "IMG_1594.JPG",
                        "source_sha256": expected,
                        "copy_mode": "byte-identical",
                    },
                )

    def test_forged_origin_receipt_cannot_approve_tampered_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            origin_copy = package / "stills/origin-2017.jpg"
            origin_copy.parent.mkdir(parents=True)
            origin_copy.write_bytes(b"tampered bytes")
            tampered = CHECK.sha256(origin_copy)
            (package / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "name": "stills/origin-2017.jpg",
                                "source": "IMG_1594.JPG",
                                "copy_mode": "byte-identical",
                                "sha256": tampered,
                                "source_sha256": tampered,
                            }
                        ]
                    }
                )
            )
            spec = {
                "filename": "origin-2017.jpg",
                "source_filename": "IMG_1594.JPG",
                "source_sha256": hashlib.sha256(b"camera original").hexdigest(),
                "copy_mode": "byte-identical",
            }
            report = CHECK.Report()
            CHECK.check_origin_still(spec, package, report)
            status = next(
                row[2]
                for row in report.rows
                if row[1] == "origin is byte-identical to its registered source"
            )
            self.assertEqual(status, CHECK.FAIL)

    def test_origin_registration_rejects_missing_or_malformed_sha256(self) -> None:
        cases = {
            "missing": {},
            "null": {"source_sha256": None},
            "short": {"source_sha256": "0" * 63},
            "non-hex": {"source_sha256": "g" * 64},
            "non-string": {"source_sha256": 7},
        }
        for label, digest_field in cases.items():
            register = {
                "package": {
                    "origin_still": {
                        "source_filename": "IMG_1594.JPG",
                        "copy_mode": "byte-identical",
                        **digest_field,
                    }
                }
            }
            with mock.patch.object(DELIVER.yaml, "safe_load", return_value=register):
                for reader in (DELIVER.registered_origin, DELIVER.registered_origin_source_sha256):
                    with self.subTest(case=label, reader=reader.__name__), self.assertRaises(SystemExit):
                        reader()

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

            external_work = root / "external-work"
            authorization = mock.Mock(return_value=(True, "fixture tier"))
            with (
                mock.patch.object(DELIVER, "probe", side_effect=fake_probe),
                mock.patch.object(DELIVER, "score_provenance", return_value={"sources": ["a", "b"]}),
                mock.patch.object(DELIVER, "authorize_render_tier", authorization),
                mock.patch.object(DELIVER.shutil, "which", side_effect=lambda command: f"/tools/{command}"),
                mock.patch.object(
                    DELIVER.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ),
                mock.patch.dict(DELIVER.os.environ, {"DANSE_WORK": str(external_work)}, clear=True),
                redirect_stdout(io.StringIO()) as output,
            ):
                result = DELIVER.preflight(program, SPAN, {"master"}, set(), "film", root, package, None)
            self.assertEqual(result, 0)
            authorization.assert_called_once_with(DELIVER.DANSE / "corpus", external_work, "film")
            self.assertNotIn("Python module numpy", output.getvalue())
            self.assertNotIn("grain bank", output.getvalue())

    def test_preflight_rejects_a_picture_with_stale_concat_receipts(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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
                mock.patch.object(DELIVER.importlib.util, "find_spec", return_value=None),
                mock.patch.object(
                    DELIVER.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 1),
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                result = DELIVER.preflight(program, SPAN, {"master"}, set(), "film", root, root / "package", None)
            self.assertEqual(result, 1)
            self.assertIn("Playwright", output.getvalue())

    def test_hash_navigation_discards_superseded_program_loads(self) -> None:
        source = (ROOT / "index.html").read_text()
        self.assertIn("const generation = ++navigationGeneration", source)
        self.assertGreaterEqual(source.count("generation !== navigationGeneration"), 2)

    def test_sound_depth_uses_the_renderers_view_space(self) -> None:
        script = """
          import { camera, viewDepth } from './engine/room.js';
          const view = camera(0.8, 0.7, 0.35).view;
          const point = [0.4, -0.2, 0.9];
          console.log(JSON.stringify({world: point[2], viewed: viewDepth(view, point)}));
        """
        done = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        depths = json.loads(done.stdout)
        self.assertNotAlmostEqual(depths["world"], depths["viewed"])
        self.assertIn("viewDepth(view.view, p.position)", (ROOT / "sound/control.mjs").read_text())

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

    def test_preflight_reuses_registered_origin_bytes_without_raw_source(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            origin_copy = package / "stills/origin-2017.jpg"
            origin_copy.parent.mkdir(parents=True)
            origin_copy.write_bytes(b"preserved origin")
            missing_raw = root / "unmounted/IMG_1594.JPG"
            with (
                mock.patch.object(DELIVER.shutil, "which", return_value="/tools/node"),
                mock.patch.object(
                    DELIVER, "registered_origin_source_sha256", return_value=DELIVER.digest(origin_copy)
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    DELIVER.preflight(
                        program,
                        SPAN,
                        {"origin"},
                        set(),
                        "film",
                        root,
                        package,
                        missing_raw,
                        passage_requested=False,
                    ),
                    0,
                )
                self.assertEqual(
                    DELIVER.preflight(
                        program,
                        SPAN,
                        {"origin"},
                        {"origin"},
                        "film",
                        root,
                        package,
                        missing_raw,
                        passage_requested=False,
                    ),
                    1,
                )

    def test_preflight_reports_unreadable_origin_without_aborting(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            source = root / "raw/IMG_1594.JPG"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"registered origin bytes")
            with (
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value="f" * 64),
                mock.patch.object(DELIVER, "digest", side_effect=PermissionError("access denied")),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(
                    DELIVER.preflight(
                        program,
                        SPAN,
                        {"origin"},
                        {"origin"},
                        "film",
                        root,
                        package,
                        source,
                        passage_requested=False,
                    ),
                    1,
                )
            self.assertIn("registered origin photograph identity", output.getvalue())
            self.assertIn("source bytes are unreadable (access denied)", output.getvalue())
            self.assertIn("NOT READY", output.getvalue())

    def test_symlinked_origin_cannot_be_adopted_or_approved(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw/IMG_1594.JPG"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"registered origin bytes")
            expected = DELIVER.digest(source)
            package = root / "package"
            origin_copy = package / "stills/origin-2017.jpg"
            origin_copy.parent.mkdir(parents=True)
            origin_copy.symlink_to(source)

            for forced in (False, True):
                with (
                    self.subTest(forced=forced),
                    mock.patch.object(DELIVER, "PACKAGE", package),
                    mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                    self.assertRaises(SystemExit),
                ):
                    DELIVER.deliver_origin(source, forced)

                with (
                    mock.patch.object(DELIVER.shutil, "which", return_value="/tools/node"),
                    mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(
                        DELIVER.preflight(
                            program,
                            SPAN,
                            {"origin"},
                            {"origin"} if forced else set(),
                            "film",
                            root,
                            package,
                            source,
                            passage_requested=False,
                        ),
                        1,
                    )

            spec = {
                "filename": "origin-2017.jpg",
                "source_filename": source.name,
                "source_sha256": expected,
                "copy_mode": "byte-identical",
            }
            report = CHECK.Report()
            CHECK.check_origin_still(spec, package, report)
            self.assertEqual(report.failures, 1)

            origin_copy.unlink()
            origin_copy.symlink_to(root / "missing-origin.jpg")
            with (
                mock.patch.object(DELIVER.shutil, "which", return_value="/tools/node"),
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    DELIVER.preflight(
                        program,
                        SPAN,
                        {"origin"},
                        {"origin"},
                        "film",
                        root,
                        package,
                        source,
                        passage_requested=False,
                    ),
                    1,
                )
            dangling_report = CHECK.Report()
            CHECK.check_origin_still(spec, package, dangling_report)
            self.assertEqual(dangling_report.failures, 1)

            origin_copy.unlink()
            origin_copy.parent.rmdir()
            external_stills = root / "external-stills"
            external_stills.mkdir()
            origin_copy.parent.symlink_to(external_stills, target_is_directory=True)
            (external_stills / origin_copy.name).write_bytes(source.read_bytes())
            with (
                mock.patch.object(DELIVER, "PACKAGE", package),
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                self.assertRaises(SystemExit),
            ):
                DELIVER.deliver_origin(source, False)
            with (
                mock.patch.object(DELIVER.shutil, "which", return_value="/tools/node"),
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    DELIVER.preflight(
                        program,
                        SPAN,
                        {"origin"},
                        set(),
                        "film",
                        root,
                        package,
                        source,
                        passage_requested=False,
                    ),
                    1,
                )
            linked_parent_report = CHECK.Report()
            CHECK.check_origin_still(spec, package, linked_parent_report)
            self.assertEqual(linked_parent_report.failures, 1)

    def test_symlinked_package_root_cannot_receive_origin(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw/IMG_1594.JPG"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"registered origin bytes")
            expected = DELIVER.digest(source)
            external_package = root / "external-package"
            external_package.mkdir()
            package = root / "package"
            package.symlink_to(external_package, target_is_directory=True)

            with (
                mock.patch.object(DELIVER, "PACKAGE", package),
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                self.assertRaises(SystemExit),
            ):
                DELIVER.deliver_origin(source, True)
            self.assertFalse((external_package / "stills/origin-2017.jpg").exists())

            external_origin = external_package / "stills/origin-2017.jpg"
            external_origin.parent.mkdir()
            external_origin.write_bytes(source.read_bytes())
            with (
                mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                mock.patch.object(DELIVER, "digest", side_effect=AssertionError("invalid package was read")),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(
                    DELIVER.preflight(
                        program,
                        SPAN,
                        {"origin"},
                        set(),
                        "film",
                        root,
                        package,
                        source,
                        passage_requested=False,
                    ),
                    1,
                )
            self.assertIn("staged origin is a regular file", output.getvalue())
            self.assertIn("NOT READY", output.getvalue())

            spec = {
                "filename": "origin-2017.jpg",
                "source_filename": source.name,
                "source_sha256": expected,
                "copy_mode": "byte-identical",
            }
            report = CHECK.Report()
            CHECK.check_origin_still(spec, package, report)
            self.assertEqual(report.failures, 1)

            external_main = root / "external-main-package"
            external_main.mkdir()
            package_main = root / "main-package"
            package_main.symlink_to(external_main, target_is_directory=True)
            argv = ["deliver.py", "--only", "text", "--out", str(root / "render"), "--package", str(package_main)]
            with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                DELIVER.main()
            self.assertEqual(list(external_main.iterdir()), [])

    def test_non_directory_package_slots_fail_closed(self) -> None:
        program = json.loads((ROOT / "render/program.json").read_text())
        for blocked in ("package", "stills"):
            with self.subTest(blocked=blocked), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "raw/IMG_1594.JPG"
                source.parent.mkdir(parents=True)
                source.write_bytes(b"registered origin bytes")
                expected = DELIVER.digest(source)
                package = root / "package"
                if blocked == "package":
                    package.write_bytes(b"not a package directory")
                else:
                    package.mkdir()
                    (package / "stills").write_bytes(b"not a stills directory")

                with (
                    mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                    redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(
                        DELIVER.preflight(
                            program,
                            SPAN,
                            {"origin"},
                            {"origin"},
                            "film",
                            root,
                            package,
                            source,
                            passage_requested=False,
                        ),
                        1,
                    )
                self.assertIn("NOT READY", output.getvalue())

                with (
                    mock.patch.object(DELIVER, "PACKAGE", package),
                    mock.patch.object(DELIVER, "registered_origin_source_sha256", return_value=expected),
                    self.assertRaises(SystemExit),
                ):
                    DELIVER.deliver_origin(source, True)

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
                        "source_tree_sha256": DELIVER.delivery_source_sha256("film"),
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

    def test_package_receipts_bind_the_producing_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            manifest = {
                "passage_seed": DELIVER.hexseed(SPAN["seed"]),
                "passage": SPAN["passage"],
                "t0": SPAN["t0"],
                "t1": SPAN["t1"],
                "duration": SPAN["duration"],
                "source_tree_sha256": "tree-a",
                "items": [{"name": "master.mov"}],
            }
            (package / "manifest.json").write_text(json.dumps(manifest))
            self.assertTrue(DELIVER.package_provenance_matches(package, SPAN, source_tree_sha256="tree-a"))
            self.assertFalse(DELIVER.package_provenance_matches(package, SPAN, source_tree_sha256="tree-b"))

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

    def test_reel_renderer_accepts_one_segment_and_receives_the_resolved_capture_start(self) -> None:
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
                (out / "reel-default-seg-000.mp4").write_bytes(b"rendered reel")
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

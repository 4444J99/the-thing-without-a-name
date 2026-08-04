#!/usr/bin/env python3
"""Adversarial and reproducibility checks for the Danse release framework."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_contract as CONTRACT  # noqa: E402

TEST_COMMIT = "a" * 40
FIXTURE_FILES = (
    "release/manifest.json",
    "release/manifest.schema.json",
    "opportunities/omega-20260804.json",
    "opportunities/omega-20260804.receipt.json",
    "opportunities/source-evidence-20260804.json",
    "opportunities/opportunity.schema.json",
    "scripts/check-opportunities.py",
    "submission/screendance-2027.yaml",
    "corpus/manifest.json",
    "scripts/check-danse.py",
    "scripts/private_custody.py",
    "rights/evidence/mediapipe-attribution.json",
    "installation/contract.py",
    "installation/digital-twin.json",
    "installation/gates.json",
    "engine/room.js",
    "render/program.json",
    "music/score.json",
    "sound/room-layout.json",
    "interaction/adapter.js",
    "reference/projection-probe.png",
)


def load_release_builder():
    path = ROOT / "scripts/build-release.py"
    spec = importlib.util.spec_from_file_location("danse_release_builder_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load_release_builder()


def load_pages_builder():
    path = ROOT / "scripts/build-pages.py"
    spec = importlib.util.spec_from_file_location("danse_release_pages_boundary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAGES = load_pages_builder()


class Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.links: list[dict[str, str | None]] = []
        self.metas: list[dict[str, str | None]] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.by_id[str(values["id"])] = (tag, values)
        if tag == "a":
            self.links.append(values)
        if tag == "meta":
            self.metas.append(values)
        if tag == "script":
            self.scripts += 1


def fixture_root(base: Path) -> Path:
    root = base / "repo"
    for relative in FIXTURE_FILES:
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return root


def read_manifest(root: Path = ROOT) -> dict:
    return json.loads((root / "release/manifest.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, manifest: dict) -> None:
    (root / "release/manifest.json").write_bytes(CONTRACT.canonical_json(manifest))


def _release_copy(value):
    """Remove draft-only prose from a synthetic fully evidenced test fixture."""
    if isinstance(value, str):
        replacements = (
            (r"\bdraft\b", "final"),
            (r"\bpending\b", "cleared"),
            (r"\bprovisional\b", "earlier"),
            (r"not for publication", "approved for publication"),
            (r"\bawaits?\b", "uses"),
            (r"\brequire(?:s|d)?\b", "carries"),
        )
        for pattern, replacement in replacements:
            value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
        return value
    if isinstance(value, list):
        return [_release_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _release_copy(item) for key, item in value.items()}
    return value


def complete_manifest(root: Path) -> dict:
    manifest = _release_copy(read_manifest(root))
    manifest["version"] = "1.0.0"
    manifest["status"] = "released"

    evidence_path = root / "release/evidence/public-receipt.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        '{"schema":"danse.release-evidence.v1","result":"satisfied"}\n',
        encoding="utf-8",
    )
    evidence = {
        "path": "release/evidence/public-receipt.json",
        "sha256": CONTRACT.sha256(evidence_path),
        "summary": "Synthetic public-safe evidence fixture.",
    }

    for claim in manifest["claims"]:
        claim["status"] = "verified"
        claim["evidence"] = copy.deepcopy(evidence)
    for index, credit in enumerate(manifest["credits"], start=1):
        credit["status"] = "cleared"
        credit["name"] = credit["name"] or f"Cleared contributor {index}"
        credit["evidence"] = copy.deepcopy(evidence)
    for medium in manifest["media"]:
        source_path = root / f"release/media/{medium['id']}.bin"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(f"synthetic media {medium['id']}\n".encode())
        medium["status"] = "ready"
        medium["source"] = {
            "path": source_path.relative_to(root).as_posix(),
            "sha256": CONTRACT.sha256(source_path),
            "bytes": source_path.stat().st_size,
            "destination": f"media/assets/{medium['id']}.bin",
        }
        medium["clearance"] = {
            "status": "cleared",
            "owner": "Synthetic fixture",
            "evidence": copy.deepcopy(evidence),
        }
        medium["alt_text"] = f"Synthetic accessible description for {medium['label']}."
    for gate in manifest["gates"]:
        gate["state"] = "satisfied"
        gate["evidence"] = copy.deepcopy(evidence)
    for section in ("spatial_requirements", "technical_rider"):
        for requirement in manifest["installation"][section]:
            requirement["status"] = "verified"

    manifest["press"]["contact"] = {
        "status": "approved",
        "label": "Project contact",
        "url": "https://organvm.github.io/the-thing-without-a-name/project/contact/",
    }
    manifest["accessibility"]["captions"] = {
        "status": "approved",
        "language": "en",
        "label": "English captions",
        "reason": None,
        "cues": [
            {
                "start": "00:00:00.000",
                "end": "00:00:02.000",
                "text": "Ambient room tone; photographic fragments emerge.",
            }
        ],
    }
    manifest["accessibility"]["transcript"] = {
        "status": "approved",
        "text": "No spoken dialogue. Ambient sound and image events are described in the caption track.",
        "reason": None,
    }
    write_manifest(root, manifest)
    return manifest


class ProductionManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = CONTRACT.validate_release(ROOT, phase="draft")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temporary.name) / "release"
        cls.receipt = BUILD.build(ROOT, cls.output, "draft", TEST_COMMIT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_snapshot_binding_uses_final_merged_freeze_and_source_evidence(self) -> None:
        binding = self.manifest["opportunity_snapshot"]
        self.assertEqual(binding["sha256"], CONTRACT.EXPECTED_OPPORTUNITY_SHA256)
        self.assertEqual(binding["snapshot_id"], "omega-20260804")
        self.assertEqual(binding["frozen_at"], CONTRACT.EXPECTED_OPPORTUNITY_FROZEN_AT)
        self.assertEqual(
            binding["source_evidence_sha256"],
            CONTRACT.EXPECTED_SOURCE_EVIDENCE_SHA256,
        )
        snapshot = json.loads((ROOT / binding["path"]).read_text())
        screendance = next(item for item in snapshot["opportunities"] if item["id"] == "screendance-miami-2027")
        self.assertEqual(screendance["consumer_contract"]["schema"], "danse.submission.v2")
        self.assertEqual(
            screendance["consumer_contract"]["canonical_sha256"],
            "d35ba2dd373271158df2138f150ec0a9cb4e4a075407b3f2da29929ed7334872",
        )

    def test_installation_binding_consumes_reference_contract_without_clearing_gates(self) -> None:
        binding = self.manifest["installation"]["reference_contract"]
        ledger = json.loads((ROOT / binding["gate_ledger"]["path"]).read_text())
        self.assertEqual(binding["status"], "reference-only")
        self.assertEqual(
            binding["spec_contract_sha256"],
            "f20d7e1d3dc8d4d1173badd5445e26bc21b2fcd8d7948d6a88ab2b9b9cef9dd3",
        )
        self.assertFalse(binding["physical_predicates_satisfied"])
        self.assertFalse(binding["issue_14_can_close"])
        self.assertEqual(binding["blocked_gates"], [gate["id"] for gate in ledger["gates"]])
        self.assertTrue(all(gate["status"] == "blocked" and gate["receipt"] is None for gate in ledger["gates"]))
        release_gate = next(gate for gate in self.manifest["gates"] if gate["id"] == "installation-evidence")
        self.assertEqual(release_gate["state"], "pending")
        self.assertIsNone(release_gate["evidence"])
        self.assertEqual(self.receipt["release"]["installation_reference"], binding)

    def test_custody_contract_is_bound_without_claiming_a_restore_or_cleanup_authority(self) -> None:
        claim = next(
            claim
            for claim in self.manifest["claims"]
            if claim["id"] == "private-custody-contract"
        )
        self.assertEqual(claim["status"], "verified")
        self.assertEqual(claim["evidence"]["path"], "scripts/private_custody.py")
        self.assertEqual(
            claim["evidence"]["sha256"],
            CONTRACT.sha256(ROOT / "scripts/private_custody.py"),
        )
        gate = next(
            gate for gate in self.manifest["gates"] if gate["id"] == "release-custody"
        )
        self.assertEqual(gate["state"], "pending")
        self.assertIsNone(gate["evidence"])

    def test_tracked_manifest_is_honest_draft_but_public_and_release_fail_closed(self) -> None:
        public = CONTRACT.phase_blockers(self.manifest, "public")
        release = CONTRACT.phase_blockers(self.manifest, "release")
        self.assertGreaterEqual(len(public), 30)
        self.assertGreater(len(release), len(public))
        for phase in ("public", "release"):
            with self.assertRaisesRegex(CONTRACT.ReleaseError, f"{phase} phase blocked"):
                CONTRACT.validate_release(ROOT, phase=phase)
            target = Path(self.temporary.name) / f"blocked-{phase}"
            with self.assertRaisesRegex(CONTRACT.ReleaseError, f"{phase} phase blocked"):
                BUILD.build(ROOT, target, phase, TEST_COMMIT)
            self.assertFalse(target.exists(), "a blocked phase must fail before writing any byte")

    def test_draft_outputs_are_complete_local_artifacts_not_public_claims(self) -> None:
        paths = {record["path"] for record in self.receipt["files"]}
        self.assertEqual(paths, set(BUILD.GENERATED_PATHS))
        self.assertFalse(any(path.startswith("media/assets/") for path in paths))
        self.assertEqual(set(self.receipt["toolchain"]), {"python", "pypdf", "reportlab"})
        self.assertTrue(all(self.receipt["toolchain"].values()))
        self.assertEqual(self.receipt["release"]["manifest"]["path"], "release/manifest.json")
        self.assertEqual(
            self.receipt["release"]["opportunity_snapshot"]["path"],
            "opportunities/omega-20260804.json",
        )
        self.assertEqual(
            self.receipt["release"]["source_evidence"]["sha256"],
            CONTRACT.EXPECTED_SOURCE_EVIDENCE_SHA256,
        )
        project = (self.output / "project/index.html").read_text(encoding="utf-8")
        self.assertIn('name="robots" content="noindex,nofollow"', project)
        self.assertIn("Draft - not for publication", project)
        self.assertIn("@media (prefers-reduced-motion:reduce)", project)
        self.assertIn("viewport-fit=cover", project)
        self.assertNotIn("<script", project)
        self.assertNotIn("sound is not scored to the image", project.lower())
        reference = self.manifest["installation"]["reference_contract"]
        self.assertIn(reference["spec_id"], project)
        self.assertIn(reference["spec_contract_sha256"], project)
        self.assertIn("8 gates remain blocked", project)

    def test_project_markup_is_semantic_and_keeps_the_artwork_at_root(self) -> None:
        markup = Markup()
        markup.feed((self.output / "project/index.html").read_text(encoding="utf-8"))
        self.assertIn("content", markup.by_id)
        self.assertIn("access", markup.by_id)
        self.assertIn("evidence", markup.by_id)
        self.assertEqual(markup.scripts, 0)
        hrefs = {link.get("href") for link in markup.links}
        self.assertIn("../", hrefs)
        self.assertIn("#access", hrefs)
        self.assertIn("#evidence", hrefs)
        robots = [meta for meta in markup.metas if meta.get("name") == "robots"]
        self.assertEqual(robots[0]["content"], "noindex,nofollow")

    def test_pdf_is_deterministic_structured_and_visibly_draft(self) -> None:
        path = self.output / BUILD.PDF_NAME
        reader = PdfReader(str(path))
        self.assertGreaterEqual(len(reader.pages), 5)
        self.assertEqual(reader.metadata.title, "THE THING WITHOUT A NAME")
        self.assertFalse(reader.is_encrypted)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("DRAFT - NOT FOR PUBLICATION", text)
        self.assertIn("System flow", text)
        self.assertIn("Reference installation contract", text)
        self.assertIn(self.manifest["installation"]["reference_contract"]["spec_id"], text)
        self.assertIn("Required evidence before publication", text)

    def test_accessibility_press_credit_and_media_outputs_come_from_manifest(self) -> None:
        access = (self.output / "accessibility/accessibility.md").read_text()
        captions = (self.output / "accessibility/captions.en.vtt").read_text()
        transcript = (self.output / "accessibility/transcript.txt").read_text()
        press = (self.output / "press/press-kit.md").read_text()
        credits = (self.output / "press/credits.txt").read_text()
        inventory = json.loads((self.output / "media/release-media.json").read_text())
        calendar = json.loads((self.output / "press/posting-calendar.json").read_text())
        self.assertIn(self.manifest["accessibility"]["alt_text"], access)
        self.assertTrue(captions.startswith("WEBVTT\n"))
        self.assertIn(self.manifest["accessibility"]["transcript"]["text"], transcript)
        self.assertIn(self.manifest["press"]["synopsis_short"], press)
        self.assertIn(self.manifest["credits"][0]["role"], credits)
        self.assertEqual(len(inventory["media"]), len(self.manifest["media"]))
        self.assertTrue(all(item["released"] is None for item in inventory["media"]))
        self.assertFalse(calendar["publishes_automatically"])

    def test_pages_allowlist_still_excludes_project_and_release_surfaces(self) -> None:
        pages = set(PAGES.source_files(ROOT))
        self.assertFalse(any(path.startswith("project/") for path in pages))
        self.assertFalse(any(path.startswith("release/") for path in pages))
        self.assertNotIn("scripts/build-release.py", pages)


class DeterminismAndCompletedPhaseTest(unittest.TestCase):
    def test_two_draft_builds_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = BUILD.build(ROOT, base / "one", "draft", TEST_COMMIT)
            second = BUILD.build(ROOT, base / "two", "draft", TEST_COMMIT)
            self.assertEqual(first, second)
            for record in first["files"]:
                relative = record["path"]
                self.assertEqual((base / "one" / relative).read_bytes(), (base / "two" / relative).read_bytes())
            self.assertEqual(
                (base / "one" / BUILD.ARTIFACT_MANIFEST).read_bytes(),
                (base / "two" / BUILD.ARTIFACT_MANIFEST).read_bytes(),
            )

    def test_fully_evidenced_fixture_builds_public_and_release_without_draft_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = fixture_root(base)
            manifest = complete_manifest(root)
            CONTRACT.validate_release(root, phase="public")
            CONTRACT.validate_release(root, phase="release")
            output = base / "artifact"
            receipt = BUILD.build(root, output, "release", TEST_COMMIT)
            project = (output / "project/index.html").read_text(encoding="utf-8")
            self.assertNotIn("noindex,nofollow", project)
            self.assertNotIn("Draft - not for publication", project)
            self.assertEqual(receipt["phase"], "release")
            assets = [record for record in receipt["files"] if record["path"].startswith("media/assets/")]
            self.assertEqual(len(assets), len(manifest["media"]))
            captions = (output / "accessibility/captions.en.vtt").read_text()
            self.assertIn("00:00:00.000 --> 00:00:02.000", captions)

    def test_public_phase_does_not_require_release_only_lifecycle_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = fixture_root(base)
            original = read_manifest(root)
            manifest = complete_manifest(root)
            manifest["status"] = "public-approved"
            original_media = {item["id"]: item for item in original["media"]}
            for index, medium in enumerate(manifest["media"]):
                if medium["required_for"] == ["release"]:
                    manifest["media"][index] = copy.deepcopy(original_media[medium["id"]])
            original_gates = {item["id"]: item for item in original["gates"]}
            for index, gate in enumerate(manifest["gates"]):
                if gate["required_for"] == ["release"]:
                    manifest["gates"][index] = copy.deepcopy(original_gates[gate["id"]])
            write_manifest(root, manifest)

            CONTRACT.validate_release(root, phase="public")
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "release phase blocked"):
                CONTRACT.validate_release(root, phase="release")
            receipt = BUILD.build(root, base / "public-artifact", "public", TEST_COMMIT)
            self.assertEqual(receipt["phase"], "public")


class AdversarialManifestTest(unittest.TestCase):
    def mutate(self, callback) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = fixture_root(Path(temporary.name))
        manifest = read_manifest(root)
        callback(manifest)
        write_manifest(root, manifest)
        return temporary, root

    def test_unknown_manifest_key_fails_schema(self) -> None:
        temporary, root = self.mutate(lambda manifest: manifest.update({"surprise": True}))
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "schema failure"):
                CONTRACT.validate_release(root)

    def test_superseded_opportunity_digest_fails(self) -> None:
        def change(manifest):
            manifest["opportunity_snapshot"]["sha256"] = "0" * 64

        temporary, root = self.mutate(change)
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "reviewed frozen opportunity digest"):
                CONTRACT.validate_release(root)

    def test_verified_claim_digest_drift_fails(self) -> None:
        def change(manifest):
            manifest["claims"][0]["evidence"]["sha256"] = "f" * 64

        temporary, root = self.mutate(change)
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "digest mismatch"):
                CONTRACT.validate_release(root)

    def test_installation_contract_digest_drift_fails(self) -> None:
        def change(manifest):
            manifest["installation"]["reference_contract"]["digital_twin"]["sha256"] = "f" * 64

        temporary, root = self.mutate(change)
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "installation digital twin digest mismatch"):
                CONTRACT.validate_release(root)

    def test_fake_satisfied_gate_without_evidence_fails(self) -> None:
        def change(manifest):
            manifest["gates"][0]["state"] = "satisfied"

        temporary, root = self.mutate(change)
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "satisfied gate .* has no evidence"):
                CONTRACT.validate_release(root)

    def test_duplicate_ids_fail(self) -> None:
        def change(manifest):
            manifest["media"][1]["id"] = manifest["media"][0]["id"]

        temporary, root = self.mutate(change)
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "media ids must be unique"):
                CONTRACT.validate_release(root)

    def test_media_path_escape_fails(self) -> None:
        def change(manifest):
            manifest["media"][0]["source"] = {
                "path": "../private/still.png",
                "sha256": "0" * 64,
                "destination": "media/assets/still.png",
            }

        temporary, root = self.mutate(change)
        with temporary:
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "schema failure"):
                CONTRACT.validate_release(root)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_evidence_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = fixture_root(base)
            outside = base / "outside.json"
            shutil.copyfile(root / "corpus/manifest.json", outside)
            (root / "corpus/manifest.json").unlink()
            (root / "corpus/manifest.json").symlink_to(outside)
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "traverses a symlink"):
                CONTRACT.validate_release(root)

    def test_approved_empty_caption_track_fails_public(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            manifest = complete_manifest(root)
            manifest["accessibility"]["captions"]["cues"] = []
            write_manifest(root, manifest)
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "approved caption track contains no cues"):
                CONTRACT.validate_release(root, phase="public")

    def test_caption_cue_must_have_forward_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            manifest = complete_manifest(root)
            manifest["accessibility"]["captions"]["cues"][0]["end"] = "00:00:00.000"
            write_manifest(root, manifest)
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "must end after it starts"):
                CONTRACT.validate_release(root, phase="public")

    def test_media_destinations_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            manifest = complete_manifest(root)
            manifest["media"][1]["source"]["destination"] = manifest["media"][0]["source"]["destination"]
            write_manifest(root, manifest)
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "media destination is not unique"):
                CONTRACT.validate_release(root, phase="public")

    def test_media_byte_count_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            manifest = complete_manifest(root)
            manifest["media"][0]["source"]["bytes"] += 1
            write_manifest(root, manifest)
            with self.assertRaisesRegex(CONTRACT.ReleaseError, "byte count mismatch"):
                CONTRACT.validate_release(root, phase="public")


class AdversarialArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.output = self.base / "artifact"
        BUILD.build(ROOT, self.output, "draft", TEST_COMMIT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_tampered_pdf_digest_fails(self) -> None:
        with (self.output / BUILD.PDF_NAME).open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(CONTRACT.ReleaseError, "digest mismatch"):
            BUILD.verify_artifact(self.output, TEST_COMMIT)

    def test_unrecorded_file_fails(self) -> None:
        (self.output / "private.txt").write_text("not allowlisted\n")
        with self.assertRaisesRegex(CONTRACT.ReleaseError, "inventory mismatch"):
            BUILD.verify_artifact(self.output, TEST_COMMIT)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_project_file_fails(self) -> None:
        outside = self.base / "outside.html"
        outside.write_text("outside\n")
        project = self.output / "project/index.html"
        project.unlink()
        project.symlink_to(outside)
        with self.assertRaisesRegex(CONTRACT.ReleaseError, "missing or non-regular"):
            BUILD.verify_artifact(self.output, TEST_COMMIT)

    def test_wrong_source_commit_fails(self) -> None:
        with self.assertRaisesRegex(CONTRACT.ReleaseError, "does not match expected"):
            BUILD.verify_artifact(self.output, "b" * 40)

    def test_noncanonical_manifest_binding_fails(self) -> None:
        receipt_path = self.output / BUILD.ARTIFACT_MANIFEST
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["release"]["manifest"]["path"] = "release/other-manifest.json"
        receipt_path.write_bytes(CONTRACT.canonical_json(receipt))
        with self.assertRaisesRegex(CONTRACT.ReleaseError, "non-canonical release manifest"):
            BUILD.verify_artifact(self.output, TEST_COMMIT)

    def test_duplicate_receipt_key_fails(self) -> None:
        receipt_path = self.output / BUILD.ARTIFACT_MANIFEST
        receipt_path.write_text(
            '{"schema":"danse.release-build.v1","schema":"danse.release-build.v1"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CONTRACT.ReleaseError, "duplicate key 'schema'"):
            BUILD.verify_artifact(self.output, TEST_COMMIT)


if __name__ == "__main__":
    unittest.main()

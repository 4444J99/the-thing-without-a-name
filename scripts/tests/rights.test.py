#!/usr/bin/env python3
"""Portable regressions for the Danse rights and attribution contract."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import rights_contract as RIGHTS  # noqa: E402


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_evidence() -> dict:
    return copy.deepcopy(RIGHTS.load_register()["bindings"]["corpus"]["source"])


def clear_requirements(document: dict) -> None:
    evidence = source_evidence()
    for asset in document["assets"]:
        if asset["disposition"] == "blocked":
            asset["disposition"] = "owned"
            asset["rights_holder"] = asset["rights_holder"] or "Redacted rights holder"
            asset["blocker"] = None
        if asset["public_credit"]["state"] == "pending":
            asset["public_credit"] |= {
                "state": "approved",
                "label": asset["public_credit"]["label"] or f"Approved {asset['id']} credit",
            }
        for use in asset["uses"]:
            if use["status"] == "blocked":
                use["status"] = "cleared"
                use["evidence"] = copy.deepcopy(evidence)
                if use["territory"] == "pending":
                    use["territory"] = "worldwide"
                if use["term"] == "pending":
                    use["term"] = "project-duration"
                if use["promotion"] == "pending":
                    use["promotion"] = "allowed"
                if use["archive"] == "pending":
                    use["archive"] = "allowed"
    for gate in document["human_gates"]:
        gate["state"] = "satisfied"
        gate["evidence"] = copy.deepcopy(evidence)
    document["status"] = "cleared"


def make_package(base: Path, document: dict) -> Path:
    package = base / "package"
    (package / "stills").mkdir(parents=True)
    (package / "text").mkdir()
    master = b"rights-test-master"
    origin = b"rights-test-origin"
    (package / "master.mov").write_bytes(master)
    (package / "stills/origin-2017.jpg").write_bytes(origin)
    for binding in document["package_text"]:
        source = ROOT / binding["source"]["path"]
        (package / binding["destination"]).write_bytes(source.read_bytes())

    submission = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
    audio_sources = submission["package"]["audio"]["source_recordings"]
    origin_source = submission["package"]["origin_still"]["source_sha256"]
    text_items = []
    for binding in document["package_text"]:
        payload = (package / binding["destination"]).read_bytes()
        text_items.append(
            {
                "name": binding["destination"],
                "bytes": len(payload),
                "sha256": digest_bytes(payload),
            }
        )
    manifest = {
        "schema": "danse.delivery.manifest.v1",
        "title": "Rights contract test",
        "seed": "0x1234ABCD",
        "source_tree_sha256": "a" * 64,
        "items": [
            {
                "name": "master.mov",
                "bytes": len(master),
                "sha256": digest_bytes(master),
                "sound": {
                    "sources": audio_sources,
                    "score_sha256": "b" * 64,
                    "bank_fingerprint": "test-bank",
                },
            },
            {
                "name": "stills/origin-2017.jpg",
                "bytes": len(origin),
                "sha256": digest_bytes(origin),
                "source": "IMG_1594.JPG",
                "source_sha256": origin_source,
                "copy_mode": "byte-identical",
            },
            *text_items,
        ],
    }
    (package / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    attest = {}
    for gate in document["human_gates"]:
        record = gate["attestation"]
        if record is not None:
            attest[record["key"]] = record["values"][0]
    (package / "attest.yaml").write_text(yaml.safe_dump(attest, sort_keys=True))
    return package


def make_release(base: Path, document: dict, register_path: Path = RIGHTS.REGISTER) -> Path:
    evidence = source_evidence()
    media = []
    for rule in document["release_rules"]:
        media.append(
            {
                "id": rule["media_id"],
                "required_for": rule["required_for"],
                "status": "ready",
                "source": {**copy.deepcopy(evidence), "destination": f"media/assets/{rule['media_id']}.bin"},
                "clearance": {"status": "cleared", "owner": "Rights test", "evidence": copy.deepcopy(evidence)},
            }
        )
    credits = [
        {
            "id": rule["credit_id"],
            "name": f"Approved {rule['credit_id']}",
            "status": "cleared",
            "evidence": copy.deepcopy(evidence),
        }
        for rule in document["credit_rules"]
    ]
    manifest = {
        "schema": "danse.release.v1",
        "release_id": "rights-test",
        "status": "released",
        "media": media,
        "credits": credits,
        "gates": [
            {
                "id": "rights-register",
                "required_for": ["public", "release"],
                "state": "satisfied",
                "evidence": {
                    "path": "rights/register.json",
                    "sha256": RIGHTS.sha256(register_path),
                    "summary": "Exact redacted rights register",
                },
            }
        ],
    }
    path = base / "release-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


class RightsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = RIGHTS.load_register()

    def test_draft_cli_validates_exact_sources_schema_and_inventory(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/check-rights.py", "--phase", "draft", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "ready")
        self.assertEqual(receipt["inventory"]["assets"], len(RIGHTS.EXPECTED_CATEGORIES))
        self.assertEqual(
            {asset["category"] for asset in self.document["assets"]},
            RIGHTS.EXPECTED_CATEGORIES,
        )
        self.assertEqual(receipt["register"]["sha256"], RIGHTS.sha256(RIGHTS.REGISTER))
        self.assertEqual(receipt["register"]["schema_sha256"], RIGHTS.sha256(RIGHTS.SCHEMA))

    def test_every_shipping_phase_fails_closed_without_human_or_exact_artifact_evidence(self) -> None:
        for phase in ("public", "package", "uploaded", "submitted", "release"):
            with self.subTest(phase=phase):
                _, receipt = RIGHTS.validate_all(phase=phase)
                self.assertEqual(receipt["status"], "blocked")
                self.assertTrue(receipt["blockers"])
                self.assertFalse(any("/Users/" in blocker for blocker in receipt["blockers"]))
        _, public = RIGHTS.validate_all(phase="public")
        self.assertTrue(any("dancer-release-and-credit" in blocker for blocker in public["blockers"]))
        self.assertTrue(any("--release-manifest" in blocker for blocker in public["blockers"]))
        _, package = RIGHTS.validate_all(phase="package")
        self.assertTrue(any("--package" in blocker for blocker in package["blockers"]))

    def test_private_paths_contacts_and_sensitive_fields_are_rejected(self) -> None:
        for mutation, expected in (
            (("note", "private release at /Users/example/release.pdf"), "machine-local path"),
            (("note", "contact dancer@example.test"), "email address"),
            (("note", "call 305-555-0123"), "phone number"),
        ):
            with self.subTest(expected=expected):
                candidate = copy.deepcopy(self.document)
                candidate["assets"][0]["uses"][0][mutation[0]] = mutation[1]
                errors = RIGHTS.validate_document(candidate)
                self.assertTrue(any(expected in error for error in errors), errors)
        candidate = copy.deepcopy(self.document)
        candidate["assets"][0]["private_evidence"]["signature"] = "redacted"
        errors = RIGHTS.validate_document(candidate)
        self.assertTrue(any("sensitive field" in error for error in errors), errors)

    def test_stale_conflicting_untracked_and_symlink_evidence_are_rejected(self) -> None:
        stale = copy.deepcopy(self.document)
        stale["assets"][0]["provenance"][0]["sha256"] = "0" * 64
        errors = RIGHTS.validate_document(stale)
        self.assertTrue(any("conflicting digests" in error or "digest mismatch" in error for error in errors), errors)

        untracked = copy.deepcopy(self.document)
        untracked["assets"][0]["provenance"][0] = {
            "path": "rights/not-tracked.txt",
            "sha256": "0" * 64,
            "summary": "Must not validate",
        }
        errors = RIGHTS.validate_document(untracked)
        self.assertTrue(any("not tracked by Git" in error for error in errors), errors)

        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary) / "outside.txt"
            outside.write_text("private")
            link = ROOT / "rights" / "test-evidence-link"
            try:
                link.symlink_to(outside)
                linked = copy.deepcopy(self.document)
                linked["assets"][0]["provenance"][0] = {
                    "path": "rights/test-evidence-link",
                    "sha256": RIGHTS.sha256(outside),
                    "summary": "Must not validate",
                }
                errors = RIGHTS.validate_document(linked, enforce_tracked=False)
                self.assertTrue(any("symlink" in error for error in errors), errors)
            finally:
                link.unlink(missing_ok=True)

    def test_completion_evidence_never_clears_the_wrong_state(self) -> None:
        candidate = copy.deepcopy(self.document)
        evidence = source_evidence()
        candidate["human_gates"][0]["evidence"] = evidence
        candidate["assets"][0]["uses"][0]["evidence"] = evidence
        candidate["assets"][0]["private_evidence"]["receipt"] = evidence
        errors = RIGHTS.validate_document(candidate)
        self.assertTrue(any("carries completion evidence" in error for error in errors), errors)
        self.assertTrue(any("private-evidence receipt" in error for error in errors), errors)

    def test_license_and_permission_layers_cannot_clear_each_other(self) -> None:
        candidate = copy.deepcopy(self.document)
        vendor = next(asset for asset in candidate["assets"] if asset["id"] == "mediapipe-pose-runtime")
        vendor["license"] = None
        dancer = next(asset for asset in candidate["assets"] if asset["id"] == "dancer-performance-likeness")
        dancer["uses"][0]["status"] = "cleared"
        dancer["uses"][0]["evidence"] = source_evidence()
        errors = RIGHTS.validate_document(candidate)
        self.assertTrue(any("licensed asset mediapipe-pose-runtime has no license" in error for error in errors), errors)
        self.assertTrue(any("license disagrees with the exact package/model binding" in error for error in errors), errors)
        self.assertTrue(any("cleared from disposition blocked" in error for error in errors), errors)

    def test_fixed_permissions_cannot_expire_before_the_recorded_assessment(self) -> None:
        candidate = copy.deepcopy(self.document)
        use = candidate["assets"][0]["uses"][0]
        use["term"] = "fixed"
        use["expires"] = "2026-08-03"
        errors = RIGHTS.validate_document(candidate)
        self.assertTrue(any("expired before the assessment date" in error for error in errors), errors)

    def test_exact_package_manifest_bytes_sources_text_and_rules_validate(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            blockers, identity = RIGHTS.validate_package(candidate, package)
            self.assertEqual(blockers, [])
            self.assertEqual(identity["items"], 2 + len(candidate["package_text"]))

            (package / "master.mov").write_bytes(b"tampered")
            blockers, _ = RIGHTS.validate_package(candidate, package)
            self.assertTrue(any("digest does not match" in blocker for blocker in blockers), blockers)

    def test_package_rejects_unmanifested_media_unknown_rules_and_symlinks(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            (package / "unlisted.mp4").write_bytes(b"unlisted")
            (package / "unlisted.webp").write_bytes(b"unlisted webp")
            outside = Path(temporary) / "outside.jpg"
            outside.write_bytes(b"outside")
            (package / "stills/link.jpg").symlink_to(outside)
            manifest = json.loads((package / "manifest.json").read_text())
            unknown = b"unknown"
            (package / "unknown.bin").write_bytes(unknown)
            manifest["items"].append(
                {"name": "unknown.bin", "bytes": len(unknown), "sha256": digest_bytes(unknown)}
            )
            (package / "manifest.json").write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_package(candidate, package)
            self.assertGreaterEqual(
                sum("absent from the manifest" in blocker for blocker in blockers),
                2,
                blockers,
            )
            self.assertTrue(any("symlink file" in blocker for blocker in blockers), blockers)
            self.assertTrue(any("manifest item" in blocker and "0 rights rules" in blocker for blocker in blockers), blockers)

    def test_package_attestations_are_scoped_and_never_replace_release_receipts(self) -> None:
        gate = next(row for row in self.document["human_gates"] if row["id"] == "dancer-release-and-credit")
        self.assertFalse(RIGHTS.gate_satisfied(gate, {}, allow_attestation=True))
        self.assertTrue(
            RIGHTS.gate_satisfied(gate, {"dancer-release-and-credit": True}, allow_attestation=True)
        )
        self.assertFalse(
            RIGHTS.gate_satisfied(gate, {"dancer-release-and-credit": 1}, allow_attestation=True)
        )
        self.assertFalse(
            RIGHTS.gate_satisfied(gate, {"dancer-release-and-credit": True}, allow_attestation=False)
        )
        choice = next(row for row in self.document["human_gates"] if row["id"] == "archive-library-choice")
        self.assertTrue(
            RIGHTS.gate_satisfied(choice, {"archive-library-choice": "include"}, allow_attestation=True)
        )
        self.assertFalse(
            RIGHTS.gate_satisfied(choice, {"archive-library-choice": True}, allow_attestation=True)
        )
        rejected = copy.deepcopy(gate)
        rejected["state"] = "rejected"
        self.assertFalse(
            RIGHTS.gate_satisfied(rejected, {"dancer-release-and-credit": True}, allow_attestation=True)
        )

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / "attest.yaml").write_text(
                "final-cut-only: true\nfinal-cut-only: false\n",
                encoding="utf-8",
            )
            _, blockers = RIGHTS.load_attestation(package)
            self.assertTrue(any("invalid or unreadable YAML" in blocker for blocker in blockers), blockers)

    def test_release_manifest_binds_exact_rights_register_media_and_credits(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release = make_release(Path(temporary), candidate)
            blockers, identity = RIGHTS.validate_release_manifest(candidate, release, "release")
            self.assertEqual(blockers, [])
            self.assertEqual(identity["schema"], "danse.release.v1")

            manifest = json.loads(release.read_text())
            manifest["gates"][0]["evidence"]["sha256"] = "0" * 64
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(candidate, release, "release")
            self.assertTrue(any("does not bind this exact rights register" in blocker for blocker in blockers), blockers)

    def test_release_manifest_cannot_hide_rights_rows_or_repeat_gate_identities(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release = make_release(Path(temporary), candidate)
            manifest = json.loads(release.read_text())
            manifest["media"][0]["required_for"] = ["release"]
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(candidate, release, "public")
            self.assertTrue(any("phase scope disagrees" in blocker for blocker in blockers), blockers)

            manifest = json.loads(make_release(Path(temporary), candidate).read_text())
            manifest["media"].append(copy.deepcopy(manifest["media"][0]))
            manifest["credits"].append(copy.deepcopy(manifest["credits"][0]))
            manifest["gates"].append(copy.deepcopy(manifest["gates"][0]))
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(candidate, release, "release")
            self.assertTrue(any("repeats media id" in blocker for blocker in blockers), blockers)
            self.assertTrue(any("repeats credit id" in blocker for blocker in blockers), blockers)
            self.assertTrue(any("repeats gate id" in blocker for blocker in blockers), blockers)

    def test_release_clearance_evidence_must_be_tracked_but_media_may_be_hydrated(self) -> None:
        evidence = source_evidence()
        self.assertEqual(
            RIGHTS._verify_release_source(
                ROOT,
                evidence,
                "test evidence",
                tracked=set(),
                require_tracked=False,
            ),
            [],
        )
        blockers = RIGHTS._verify_release_source(
            ROOT,
            evidence,
            "test evidence",
            tracked=set(),
            require_tracked=True,
        )
        self.assertEqual(blockers, ["test evidence source is not tracked public-safe evidence"])

    def test_receipts_are_deterministic_redacted_and_contain_exact_input_digests(self) -> None:
        first_document, first = RIGHTS.validate_all(phase="draft")
        second_document, second = RIGHTS.validate_all(phase="draft")
        self.assertEqual(first_document, second_document)
        self.assertEqual(RIGHTS.canonical_json(first), RIGHTS.canonical_json(second))
        rendered = RIGHTS.canonical_json(first)
        self.assertNotIn(str(ROOT), rendered)
        self.assertNotIn("Anthony J. Padavano and the performer", rendered)
        self.assertEqual(first["register"]["sha256"], RIGHTS.sha256(RIGHTS.REGISTER))

        _, missing_package = RIGHTS.validate_all(
            phase="package",
            package=Path("/Users/private-person/unavailable-package"),
        )
        self.assertNotIn("/Users/", RIGHTS.canonical_json(missing_package))

    def test_frozen_submission_terms_and_fixture_music_state_are_exactly_bound(self) -> None:
        submission = yaml.safe_load((ROOT / self.document["bindings"]["submission"]["source"]["path"]).read_text())
        term_ids = {row["id"] for row in submission["terms"]}
        self.assertTrue(set(self.document["bindings"]["submission"]["required_terms"]) <= term_ids)
        music = yaml.safe_load((ROOT / self.document["bindings"]["music"]["source"]["path"]).read_text())
        self.assertEqual(music["artistic_gate"]["status"], "pending")
        self.assertEqual(music["works"][0]["role"], "fixture")
        self.assertEqual(music["works"][0]["selection"]["status"], "not-selected")


if __name__ == "__main__":
    unittest.main(verbosity=2)

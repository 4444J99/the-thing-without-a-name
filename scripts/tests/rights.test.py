#!/usr/bin/env python3
"""Portable regressions for the Danse rights and attribution contract."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    (package / "provenance").mkdir()
    master = b"rights-test-master"
    origin = b"rights-test-origin"
    score = b"rights-test-score-source"
    (package / "master.mov").write_bytes(master)
    (package / "stills/origin-2017.jpg").write_bytes(origin)
    (package / "provenance/passage-score.wav").write_bytes(score)
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
        "corpus_tier": "film",
        "source_tree_sha256": RIGHTS.expected_delivery_source_sha256("film"),
        "items": [
            {
                "name": "master.mov",
                "bytes": len(master),
                "sha256": digest_bytes(master),
                "sound": {
                    "sources": audio_sources,
                    "score_sha256": digest_bytes(score),
                    "bank_fingerprint": "test-bank",
                },
            },
            {
                "name": "provenance/passage-score.wav",
                "bytes": len(score),
                "sha256": digest_bytes(score),
                "sound": {
                    "sources": audio_sources,
                    "score_sha256": digest_bytes(score),
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


def fixture_audio_identity() -> dict:
    submission = yaml.safe_load((ROOT / "submission/screendance-2027.yaml").read_text())
    return {
        "bank_fingerprint": "test-bank",
        "sources": submission["package"]["audio"]["source_recordings"],
    }


def validate_fixture_package(document: dict, package: Path) -> tuple[list[str], dict | None]:
    with mock.patch.object(RIGHTS, "current_audio_identity", return_value=fixture_audio_identity()):
        return RIGHTS.validate_package(document, package)


def fixture_phase_blockers(
    document: dict,
    phase: str,
    *,
    package: Path,
) -> tuple[list[str], dict]:
    with mock.patch.object(RIGHTS, "current_audio_identity", return_value=fixture_audio_identity()):
        return RIGHTS.phase_blockers(document, phase, package=package)


def make_release(base: Path, document: dict, *, phase: str = "release") -> tuple[Path, Path, Path]:
    root = base / "repository"
    root.mkdir(parents=True, exist_ok=True)
    evidence_path = root / "evidence.json"
    evidence_path.write_bytes((ROOT / source_evidence()["path"]).read_bytes())
    evidence = {
        "path": "evidence.json",
        "sha256": RIGHTS.sha256(evidence_path),
        "summary": "Tracked public-safe fixture evidence",
    }
    register_path = root / "rights" / "register.json"
    register_path.parent.mkdir(parents=True, exist_ok=True)
    register_path.write_bytes(RIGHTS.REGISTER.read_bytes())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "add", "evidence.json", "rights/register.json"],
        check=True,
    )
    media = []
    for rule in document["release_rules"]:
        if phase not in rule["required_for"]:
            media.append(
                {
                    "id": rule["media_id"],
                    "required_for": rule["required_for"],
                    "status": "pending",
                    "source": None,
                    "clearance": {"status": "pending"},
                }
            )
            continue
        artifact_path = root / rule["destination"]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(f"exact fixture bytes for {rule['media_id']}\n".encode())
        artifact = {
            "path": rule["destination"],
            "sha256": RIGHTS.sha256(artifact_path),
        }
        artifact["destination"] = artifact["path"]
        artifact["bytes"] = artifact_path.stat().st_size
        media.append(
            {
                "id": rule["media_id"],
                "required_for": rule["required_for"],
                "status": "ready",
                "source": artifact,
                "clearance": {"status": "cleared", "owner": "Rights test", "evidence": copy.deepcopy(evidence)},
            }
        )
    credit_rows = [
        {
            "id": rule["credit_id"],
            "name": next(
                asset["public_credit"]["label"]
                for asset in document["assets"]
                if asset["id"] == rule["asset"]
            ),
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
        "credits": credit_rows,
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
    path = root / "release-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path, root, register_path


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
            (("note", "private release at /workspace/Alice/private.json"), "machine-local path"),
            (("note", "private release at /tmp/private-release"), "machine-local path"),
            (("note", "private release at /Volumes/archive/evidence.pdf"), "machine-local path"),
            (("note", "private release at D:\\staging\\private.json"), "machine-local path"),
            (("note", "private release at \\\\server\\share\\private.json"), "machine-local path"),
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

    def test_noncanonical_relative_path_spellings_are_rejected(self) -> None:
        for spelling in (
            "media/assets//press-still.webp",
            "media/assets/./press-still.webp",
            "media/assets/press-still.webp/",
        ):
            with self.subTest(spelling=spelling):
                with self.assertRaisesRegex(RIGHTS.RightsError, "safe portable relative path"):
                    RIGHTS.safe_relative(spelling, "test path")
                candidate = copy.deepcopy(self.document)
                candidate["release_rules"][0]["destination"] = spelling
                errors = RIGHTS.validate_document(candidate)
                self.assertTrue(any("safe portable relative path" in error for error in errors), errors)
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
            base = Path(temporary)
            root = base / "repository"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("private")
            link = root / "evidence-link"
            link.symlink_to(outside)
            record = {
                "path": "evidence-link",
                "sha256": RIGHTS.sha256(outside),
                "summary": "Must not validate",
            }
            with self.assertRaisesRegex(RIGHTS.RightsError, "symlink"):
                RIGHTS.verify_record(root, record, "isolated evidence", {"evidence-link"})

    def test_completion_evidence_never_clears_the_wrong_state(self) -> None:
        candidate = copy.deepcopy(self.document)
        evidence = source_evidence()
        candidate["human_gates"][0]["evidence"] = evidence
        candidate["assets"][0]["uses"][0]["evidence"] = evidence
        candidate["assets"][0]["private_evidence"]["receipt"] = evidence
        errors = RIGHTS.validate_document(candidate)
        self.assertTrue(any("carries completion evidence" in error for error in errors), errors)
        self.assertTrue(any("private-evidence receipt" in error for error in errors), errors)

    def test_satisfied_gate_requires_a_typed_gate_authority_and_decision_receipt(self) -> None:
        gate = copy.deepcopy(self.document["human_gates"][0])
        gate["state"] = "satisfied"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "decision.json"
            path.write_text(json.dumps({"schema": "danse.corpus.v1"}))
            decision, errors = RIGHTS.validate_gate_decision_receipt(path, gate)
            self.assertIsNone(decision)
            self.assertTrue(any("typed decision contract" in item for item in errors), errors)

            path.write_text(
                json.dumps(
                    {
                        "schema": "danse.rights.decision.v1",
                        "gate_id": gate["id"],
                        "authority": gate["authority"],
                        "decision": True,
                        "required_for": gate["required_for"],
                    }
                )
            )
            decision, errors = RIGHTS.validate_gate_decision_receipt(path, gate)
            self.assertIs(decision, True)
            self.assertEqual(errors, [])

        mediapipe = next(
            rule for rule in self.document["credit_rules"] if rule["credit_id"] == "mediapipe-credit"
        )
        self.assertEqual(mediapipe["gate"], "mediapipe-attribution-retained")

    def test_cleared_asset_uses_require_typed_exact_scope_receipts(self) -> None:
        asset = next(
            row for row in self.document["assets"] if row["id"] == "mediapipe-pose-runtime"
        )
        use = asset["uses"][0]
        receipt = ROOT / use["evidence"]["path"]
        self.assertEqual(RIGHTS.validate_use_decision_receipt(receipt, asset, use), [])

        unrelated = copy.deepcopy(self.document)
        unrelated_asset = next(
            row for row in unrelated["assets"] if row["id"] == "mediapipe-pose-runtime"
        )
        unrelated_asset["uses"][0]["evidence"] = source_evidence()
        errors = RIGHTS.validate_document(unrelated)
        self.assertTrue(any("typed use-decision contract" in item for item in errors), errors)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "use-decision.json"
            value = json.loads(receipt.read_text())
            value["territory"] = "limited"
            path.write_text(json.dumps(value))
            errors = RIGHTS.validate_use_decision_receipt(path, asset, use)
            self.assertTrue(any("different territory" in item for item in errors), errors)

    def test_public_corpus_binding_authenticates_every_pages_derivative_byte(self) -> None:
        declared = self.document["bindings"]["corpus"]
        identity = RIGHTS.public_corpus_identity(ROOT, RIGHTS.tracked_paths(ROOT))
        self.assertEqual(identity["files"], declared["public_files"])
        self.assertEqual(identity["sha256"], declared["public_tree_sha256"])

        target = ROOT / "corpus/plates/browse/IMG_1570.webp"
        measure = RIGHTS._stable_file_measure

        def tampered_measure(
            path: Path,
            label: str,
            *,
            capture: bool = False,
        ) -> tuple[str, int, bytes | None]:
            digest, size, payload = measure(path, label, capture=capture)
            if path == target and label == "public corpus derivative":
                digest = "0" * 64
            return digest, size, payload

        with mock.patch.object(RIGHTS, "_stable_file_measure", side_effect=tampered_measure):
            errors = RIGHTS.validate_document(copy.deepcopy(self.document))
        self.assertTrue(any("public derivative tree digest has drifted" in item for item in errors), errors)

    def test_every_canonical_submission_assertion_remains_a_phase_owned_gate(self) -> None:
        missing = copy.deepcopy(self.document)
        missing["human_gates"] = [
            gate
            for gate in missing["human_gates"]
            if gate["attestation"] is None
            or gate["attestation"]["key"] != "link-downloadable"
        ]
        errors = RIGHTS.validate_document(missing)
        self.assertTrue(any("link-downloadable has no registered human gate" in item for item in errors), errors)

        wrong_phase = copy.deepcopy(self.document)
        gate = next(
            row
            for row in wrong_phase["human_gates"]
            if row["attestation"] is not None
            and row["attestation"]["key"] == "submitted-via-submittable"
        )
        gate["required_for"] = ["uploaded"]
        errors = RIGHTS.validate_document(wrong_phase)
        self.assertTrue(any("not owned by its canonical submitted phase" in item for item in errors), errors)

        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        candidate["human_gates"] = [
            gate
            for gate in candidate["human_gates"]
            if gate["attestation"] is None
            or gate["attestation"]["key"] != "submitted-via-submittable"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            blockers, _ = fixture_phase_blockers(candidate, "submitted", package=package)
        self.assertTrue(
            any("submitted-via-submittable has no registered human gate" in item for item in blockers),
            blockers,
        )

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

    def test_fixed_permissions_are_revalidated_on_the_shipping_date(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        use = candidate["assets"][0]["uses"][0]
        use["term"] = "fixed"
        use["expires"] = "2026-08-05"
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate, phase="public")
            on_expiry, _ = RIGHTS.phase_blockers(
                candidate,
                "public",
                release_manifest=release,
                root=root,
                register_path=register,
                as_of=RIGHTS.date(2026, 8, 5),
            )
            self.assertEqual(on_expiry, [])
            expired, inputs = RIGHTS.phase_blockers(
                candidate,
                "public",
                release_manifest=release,
                root=root,
                register_path=register,
                as_of=RIGHTS.date(2026, 8, 6),
            )
            self.assertTrue(any("fixed permission expired" in item for item in expired), expired)
            self.assertEqual(inputs["validation_date"], "2026-08-06")
            self.assertEqual(inputs["validation_timezone"], "America/New_York")

    def test_active_release_rules_recheck_fixed_requirements_outside_their_broad_phase_scope(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        asset = next(row for row in candidate["assets"] if row["id"] == "final-cut-derived-media")
        use = next(row for row in asset["uses"] if row["id"] == "delivery")
        self.assertNotIn("public", use["required_for"])
        use["term"] = "fixed"
        use["expires"] = "2026-08-05"
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate, phase="public")
            on_expiry, _ = RIGHTS.validate_release_manifest(
                candidate,
                release,
                "public",
                root=root,
                register_path=register,
                as_of=RIGHTS.date(2026, 8, 5),
            )
            self.assertEqual(on_expiry, [])
            expired, _ = RIGHTS.validate_release_manifest(
                candidate,
                release,
                "public",
                root=root,
                register_path=register,
                as_of=RIGHTS.date(2026, 8, 6),
            )
            self.assertTrue(
                any("accessible-trailer" in item and "fixed permission expired" in item for item in expired),
                expired,
            )

    def test_shipping_date_is_independent_of_the_ambient_host_timezone(self) -> None:
        identities = []
        for timezone in ("Pacific/Honolulu", "America/New_York", "UTC"):
            environment = os.environ.copy()
            environment["TZ"] = timezone
            result = subprocess.run(
                [sys.executable, "scripts/check-rights.py", "--phase", "public", "--json"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            receipt = json.loads(result.stdout)
            identities.append(
                (receipt["inputs"]["validation_date"], receipt["inputs"]["validation_timezone"])
            )
        self.assertEqual(len(set(identities)), 1, identities)
        self.assertEqual(identities[0][1], "America/New_York")

    def test_exact_package_manifest_bytes_sources_text_and_rules_validate(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            blockers, identity = validate_fixture_package(candidate, package)
            self.assertEqual(blockers, [])
            self.assertEqual(identity["items"], 3 + len(candidate["package_text"]))

            (package / "master.mov").write_bytes(b"tampered")
            blockers, _ = validate_fixture_package(candidate, package)
            self.assertTrue(any("digest does not match" in blocker for blocker in blockers), blockers)

    def test_package_binds_current_delivery_tree_and_every_text_manifest_row(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["schema"] = "/Users/Alice/private-schema"
            manifest["source_tree_sha256"] = "a" * 64
            missing_text = candidate["package_text"][0]["destination"]
            manifest["items"] = [item for item in manifest["items"] if item["name"] != missing_text]
            manifest_path.write_text(json.dumps(manifest))
            blockers, identity = validate_fixture_package(candidate, package)
            self.assertTrue(any("does not match the canonical delivery tree" in item for item in blockers), blockers)
            self.assertTrue(any("package text" in item and "absent from the manifest" in item for item in blockers), blockers)
            self.assertIsNone(identity["schema"])
            self.assertNotIn("/Users/", RIGHTS.canonical_json(identity))

    def test_package_audio_binds_manifested_score_hydrated_bank_and_rule_ids(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            master = next(item for item in manifest["items"] if item["name"] == "master.mov")
            master["sound"]["score_sha256"] = "c" * 64
            master["sound"]["bank_fingerprint"] = "invented-bank"
            manifest_path.write_text(json.dumps(manifest))
            blockers, _ = validate_fixture_package(candidate, package)
            self.assertTrue(any("manifested score source" in item for item in blockers), blockers)
            self.assertTrue(any("hydrated grain bank" in item for item in blockers), blockers)

            renamed = copy.deepcopy(candidate)
            next(rule for rule in renamed["package_rules"] if rule["id"] == "moving-image")["id"] = "film"
            blockers, _ = validate_fixture_package(renamed, make_package(Path(temporary) / "renamed", renamed))
            self.assertTrue(any("missing required package rule moving-image" in item for item in blockers), blockers)

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
            blockers, _ = validate_fixture_package(candidate, package)
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

        candidate = {
            "final-cut-only": 1,
            "archive-library-choice": True,
            "link-downloadable": False,
            "unknown-private-field": {"nested": "value"},
        }
        blockers = RIGHTS.validate_attestation(self.document, candidate)
        self.assertTrue(any("final-cut-only must be boolean" in blocker for blocker in blockers), blockers)
        self.assertTrue(any("archive-library-choice must be one registered choice" in blocker for blocker in blockers), blockers)
        self.assertTrue(any("1 unknown key" in blocker for blocker in blockers), blockers)
        self.assertFalse(any("nested" in blocker or "value" in blocker for blocker in blockers), blockers)

    def test_archive_opt_out_excludes_only_the_conditional_archive_use(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        candidate["status"] = "reviewed"
        gate = next(row for row in candidate["human_gates"] if row["id"] == "archive-library-choice")
        gate["state"] = "pending"
        gate["evidence"] = None
        asset = next(row for row in candidate["assets"] if row["id"] == "festival-archive-copy")
        asset["disposition"] = "blocked"
        asset["rights_holder"] = None
        asset["blocker"] = "The filing choice controls this conditional use."
        use = asset["uses"][0]
        use |= {
            "territory": "pending",
            "term": "pending",
            "promotion": "not-applicable",
            "archive": "pending",
            "status": "blocked",
            "evidence": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            attestation = yaml.safe_load((package / "attest.yaml").read_text())
            attestation["archive-library-choice"] = "opt-out"
            (package / "attest.yaml").write_text(yaml.safe_dump(attestation, sort_keys=True))
            blockers, _ = fixture_phase_blockers(candidate, "submitted", package=package)
            self.assertFalse(any("festival-archive-copy/festival-archive" in item for item in blockers), blockers)

            attestation["archive-library-choice"] = "include"
            (package / "attest.yaml").write_text(yaml.safe_dump(attestation, sort_keys=True))
            blockers, _ = fixture_phase_blockers(candidate, "submitted", package=package)
            self.assertTrue(any("festival-archive-copy/festival-archive" in item for item in blockers), blockers)

    def test_release_manifest_binds_exact_rights_register_media_and_credits(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate)
            blockers, identity = RIGHTS.validate_release_manifest(
                candidate, release, "release", root=root, register_path=register
            )
            self.assertEqual(blockers, [])
            self.assertEqual(identity["schema"], "danse.release.v1")

            manifest = json.loads(release.read_text())
            manifest["gates"][0]["evidence"]["sha256"] = "0" * 64
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "release", root=root, register_path=register
            )
            self.assertTrue(any("does not bind this exact rights register" in blocker for blocker in blockers), blockers)

    def test_release_media_bytes_credit_labels_and_safe_identity_are_exact(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate)
            manifest = json.loads(release.read_text())
            manifest["schema"] = "/Users/Alice/private-schema"
            manifest["release_id"] = "/Users/Alice/final-cut"
            manifest["media"][0]["source"]["destination"] = "some/other/released.bin"
            manifest["media"][1]["source"]["bytes"] += 1
            manifest["credits"][0]["name"] = "Incorrect public attribution"
            release.write_text(json.dumps(manifest))
            blockers, identity = RIGHTS.validate_release_manifest(
                candidate, release, "release", root=root, register_path=register
            )
            self.assertTrue(any("invalid release identifier" in item for item in blockers), blockers)
            self.assertTrue(any("canonical destination" in item for item in blockers), blockers)
            self.assertTrue(any("byte count is missing or stale" in item for item in blockers), blockers)
            self.assertTrue(any("does not match its approved attribution" in item for item in blockers), blockers)
            self.assertIsNone(identity["release_id"])
            self.assertIsNone(identity["schema"])
            self.assertNotIn("/Users/", RIGHTS.canonical_json(identity))

    def test_release_boundary_rejects_every_unmanifested_or_symlinked_artifact(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate)
            (root / "media/assets/unlisted.mp4").write_bytes(b"unlisted")
            nested = root / "media/assets/nested"
            nested.mkdir()
            (nested / "unlisted.bin").write_bytes(b"ordinary unlisted bytes")
            outside = Path(temporary) / "outside.txt"
            outside.write_text("outside")
            (root / "media/assets/unlisted.txt").symlink_to(outside)
            outside_directory = Path(temporary) / "outside-directory"
            outside_directory.mkdir()
            (root / "media/assets/linked-directory").symlink_to(
                outside_directory,
                target_is_directory=True,
            )
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "release", root=root, register_path=register
            )
            self.assertTrue(any("not listed in the release manifest" in item for item in blockers), blockers)
            self.assertTrue(any("symlink file" in item for item in blockers), blockers)
            self.assertTrue(any("symlink directory" in item for item in blockers), blockers)

            boundary = root / "media/assets"
            real_boundary = root / "media/assets-real"
            boundary.rename(real_boundary)
            boundary.symlink_to(real_boundary, target_is_directory=True)
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "release", root=root, register_path=register
            )
            self.assertTrue(any("boundary must not be a symlink" in item for item in blockers), blockers)

    def test_release_validation_rejects_media_or_manifest_mutation_during_inventory(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        inventory = RIGHTS._release_boundary_inventory
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate)
            media_path = root / candidate["release_rules"][0]["destination"]

            def mutate_media(repository: Path) -> tuple[set[str], list[str]]:
                media_path.write_bytes(b"changed after initial verification")
                return inventory(repository)

            with mock.patch.object(RIGHTS, "_release_boundary_inventory", side_effect=mutate_media):
                blockers, _ = RIGHTS.validate_release_manifest(
                    candidate, release, "release", root=root, register_path=register
                )
            self.assertTrue(any("changed during release validation" in item for item in blockers), blockers)

        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate)
            original_digest = RIGHTS.sha256(release)
            replacement = b'{"schema":"attacker.invalid","release_id":"evil"}\n'

            def mutate_manifest(repository: Path) -> tuple[set[str], list[str]]:
                release.write_bytes(replacement)
                return inventory(repository)

            with mock.patch.object(RIGHTS, "_release_boundary_inventory", side_effect=mutate_manifest):
                blockers, identity = RIGHTS.validate_release_manifest(
                    candidate, release, "release", root=root, register_path=register
                )
            self.assertTrue(any("manifest changed during validation" in item for item in blockers), blockers)
            self.assertEqual(identity["schema"], "danse.release.v1")
            self.assertEqual(identity["release_id"], "rights-test")
            self.assertEqual(identity["sha256"], original_digest)
            self.assertNotEqual(identity["sha256"], digest_bytes(replacement))

        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate)
            inventory_calls = 0

            def add_after_inventory(repository: Path) -> tuple[set[str], list[str]]:
                nonlocal inventory_calls
                inventory_calls += 1
                if inventory_calls == 2:
                    (root / "media/assets/late-extra.bin").write_bytes(b"late extra")
                return inventory(repository)

            with mock.patch.object(
                RIGHTS,
                "_release_boundary_inventory",
                side_effect=add_after_inventory,
            ):
                blockers, _ = RIGHTS.validate_release_manifest(
                    candidate, release, "release", root=root, register_path=register
                )
            self.assertTrue(any("boundary changed during validation" in item for item in blockers), blockers)

    def test_public_boundary_excludes_release_only_media_until_release_phase(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate, phase="public")
            manifest = json.loads(release.read_text())
            master = next(row for row in manifest["media"] if row["id"] == "score-driven-master")
            master_path = root / "media/assets/score-driven-master.mov"
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "public", root=root, register_path=register
            )
            self.assertEqual(blockers, [])

            payload = b"uncleared release-only master"
            master_path.write_bytes(payload)
            master["source"] = {
                "path": "media/assets/score-driven-master.mov",
                "destination": "media/assets/score-driven-master.mov",
                "sha256": digest_bytes(payload),
                "bytes": len(payload),
            }
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "public", root=root, register_path=register
            )
            self.assertTrue(any("not listed in the release manifest" in item for item in blockers), blockers)

    def test_release_manifest_cannot_hide_rights_rows_or_repeat_gate_identities(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            release, root, register = make_release(Path(temporary), candidate, phase="public")
            manifest = json.loads(release.read_text())
            manifest["media"][0]["required_for"] = ["release"]
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "public", root=root, register_path=register
            )
            self.assertTrue(any("phase scope disagrees" in blocker for blocker in blockers), blockers)

            release, root, register = make_release(Path(temporary), candidate, phase="public")
            manifest = json.loads(release.read_text())
            manifest["status"] = []
            manifest["media"] = [
                row for row in manifest["media"] if row["id"] != "project-page-copy"
            ]
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "public", root=root, register_path=register
            )
            self.assertTrue(any("status is not valid" in blocker for blocker in blockers), blockers)
            self.assertTrue(any("project-page-copy" in blocker for blocker in blockers), blockers)

            release, root, register = make_release(Path(temporary), candidate, phase="public")
            manifest = json.loads(release.read_text())
            manifest["gates"][0]["required_for"] = ["release"]
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "public", root=root, register_path=register
            )
            self.assertTrue(any("must govern public and release" in blocker for blocker in blockers), blockers)

            release, root, register = make_release(Path(temporary), candidate)
            manifest = json.loads(release.read_text())
            manifest["media"].append(copy.deepcopy(manifest["media"][0]))
            manifest["credits"].append(copy.deepcopy(manifest["credits"][0]))
            manifest["gates"].append(copy.deepcopy(manifest["gates"][0]))
            release.write_text(json.dumps(manifest))
            blockers, _ = RIGHTS.validate_release_manifest(
                candidate, release, "release", root=root, register_path=register
            )
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

    def test_package_receipt_binds_canonical_attestation_choices(self) -> None:
        candidate = copy.deepcopy(self.document)
        clear_requirements(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            package = make_package(Path(temporary), candidate)
            include = yaml.safe_load((package / "attest.yaml").read_text())
            include["archive-library-choice"] = "include"
            (package / "attest.yaml").write_text(yaml.safe_dump(include, sort_keys=True))
            blockers, inputs = fixture_phase_blockers(candidate, "submitted", package=package)
            self.assertEqual(blockers, [])
            include_identity = inputs["attestation"]
            self.assertEqual(include_identity["values"]["archive-library-choice"], "include")

            include["archive-library-choice"] = "opt-out"
            (package / "attest.yaml").write_text(yaml.safe_dump(include, sort_keys=True))
            blockers, inputs = fixture_phase_blockers(candidate, "submitted", package=package)
            self.assertEqual(blockers, [])
            opt_out_identity = inputs["attestation"]
            self.assertEqual(opt_out_identity["values"]["archive-library-choice"], "opt-out")
            self.assertNotEqual(include_identity["sha256"], opt_out_identity["sha256"])

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

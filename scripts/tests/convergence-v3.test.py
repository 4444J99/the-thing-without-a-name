#!/usr/bin/env python3
"""Fail-closed regressions for the additive Danse convergence v3 disposition matrix."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "danse_convergence_v3_test", ROOT / "scripts" / "check-convergence-v3.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("convergence v3 checker module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load_module()
ARCHIVE = CHECK.load(ROOT / CHECK.ARCHIVE)


def archive_errors(archive=None):
    return CHECK.validate_archive(copy.deepcopy(ARCHIVE if archive is None else archive))


class ConvergenceV3ReceiptTest(unittest.TestCase):
    def test_v1_and_v2_receipts_remain_immutable_and_valid(self) -> None:
        self.assertEqual(CHECK.V2.audit(ROOT), [])

    def test_v3_receipt_validates(self) -> None:
        self.assertEqual(CHECK.audit(ROOT), [])

    def test_v3_supersedes_v2_with_immutable_binding(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["supersedes"]["canonical_json_sha256"] = "f" * 64
        self.assertTrue(any("v2 predecessor path or digest drifted" in item for item in archive_errors(value)))

    def test_v3_digest_cannot_drift(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["coverage_note"] = value["coverage_note"] + " extra"
        self.assertTrue(any("immutable v3 receipt content digest drifted" in item for item in archive_errors(value)))

    def test_v3_schema_and_timestamp_are_exact(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["schema"] = "danse.archive-dispositions.v2"
        value["recorded_at"] = "2026-08-04T09:56:00Z"
        found = archive_errors(value)
        self.assertTrue(any("expected schema" in item for item in found))
        self.assertTrue(any("observation timestamp drifted" in item for item in found))

    def test_v3_archive_is_never_merged_wholesale(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["source"]["merge_wholesale"] = True
        self.assertTrue(any("wholesale-merge prohibition drifted" in item for item in archive_errors(value)))

    def test_v3_matrix_is_exhaustive_over_the_source_commit(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["artifacts"].pop()
        self.assertTrue(any("disposition matrix is incomplete" in item for item in archive_errors(value)))

    def test_v3_must_name_the_residual_paths_explicitly(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["artifacts"] = [
            item for item in value["artifacts"] if item["id"] != "pose-engine-cast-diagnostic"
        ]
        found = archive_errors(value)
        self.assertTrue(any("engine.js cast diagnostic is not explicitly dispositioned" in item for item in found))
        value = copy.deepcopy(ARCHIVE)
        hygiene = next(item for item in value["artifacts"] if item["id"] == "archive-hygiene-gitignore")
        hygiene["paths"] = []
        self.assertTrue(any(".gitignore hygiene coverage is not explicitly recorded" in item for item in archive_errors(value)))

    def test_v3_disposition_status_cannot_drift(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        cast = next(item for item in value["artifacts"] if item["id"] == "pose-engine-cast-diagnostic")
        cast["status"] = "active"
        self.assertTrue(any("disposition status drifted" in item for item in archive_errors(value)))

    def test_v3_artifact_paths_are_safe_and_relative(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        hygiene = next(item for item in value["artifacts"] if item["id"] == "archive-hygiene-gitignore")
        hygiene["paths"] = ["/private/tmp/leak"]
        self.assertTrue(any("unsafe source path" in item for item in archive_errors(value)))

    def test_v3_private_brainstorm_absence_stays_honest(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        brainstorm = next(item for item in value["artifacts"] if item["id"] == "private-brainstorm-history")
        brainstorm["disposition"] = "Recovered from a memory dump."
        self.assertTrue(any("cannot be reconstructed" not in item for item in archive_errors(value)))
        self.assertTrue(any("not honestly recorded" in item for item in archive_errors(value)))

    def test_v3_retains_inherited_real_room_blocker(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["issue_state"]["status"] = "active"
        self.assertTrue(any("must retain the inherited real-room blocker" in item for item in archive_errors(value)))

    def test_v3_requires_the_exhaustive_coverage_note(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value.pop("coverage_note")
        self.assertTrue(any("exhaustive source-coverage note is missing" in item for item in archive_errors(value)))

    def test_v3_private_paths_fail_across_all_receipt_versions(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["debug_path"] = "/Users/example/private-capsule"
        self.assertTrue(any("personal or local absolute path" in item for item in archive_errors(value)))


if __name__ == "__main__":
    unittest.main()

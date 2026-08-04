#!/usr/bin/env python3
"""Fail-closed regression tests for the Danse convergence receipts."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "danse_convergence_test", ROOT / "scripts/check-convergence.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load_module()
CONVERGENCE = CHECK.load(ROOT / CHECK.CONVERGENCE)
CUSTODY = CHECK.load(ROOT / CHECK.CUSTODY)
ARCHIVE = CHECK.load(ROOT / CHECK.ARCHIVE)
CLOSEOUT = (ROOT / CHECK.CLOSEOUT).read_text(encoding="utf-8")


def errors(convergence=None, custody=None, archive=None, closeout=None):
    return CHECK.validate_documents(
        copy.deepcopy(CONVERGENCE if convergence is None else convergence),
        copy.deepcopy(CUSTODY if custody is None else custody),
        copy.deepcopy(ARCHIVE if archive is None else archive),
        CLOSEOUT if closeout is None else closeout,
    )


class ConvergenceReceiptTest(unittest.TestCase):
    def test_repository_receipts_validate(self) -> None:
        self.assertEqual(errors(), [])

    def test_unknown_lifecycle_status_fails(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["branches"][0]["status"] = "probably-done"
        self.assertTrue(any("unsupported convergence status" in item for item in errors(convergence=value)))

    def test_personal_absolute_path_fails(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["worktrees"][0]["reason"] = "stored at /Users/example/private"
        self.assertTrue(any("personal or local absolute path" in item for item in errors(convergence=value)))

    def test_unrecorded_conversation_cannot_be_promoted(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        outcome = next(item for item in value["agent_outcomes"] if item["id"] == "unrecorded-conversations")
        outcome["status"] = "ported"
        self.assertTrue(any("unrecorded conversations" in item for item in errors(convergence=value)))

    def test_cleanup_requires_two_independent_verified_copies(self) -> None:
        value = copy.deepcopy(CUSTODY)
        root = value["roots"][0]
        root["cleanup_authorized"] = True
        root["status"] = "merged"
        self.assertTrue(any("cleanup bypasses" in item for item in errors(custody=value)))

    def test_two_records_on_one_medium_are_not_independent(self) -> None:
        value = copy.deepcopy(CUSTODY)
        root = value["roots"][0]
        root["independent_verified_copies"] = [
            {"medium_id": "same", "manifest_sha256": "a" * 64, "verified": True},
            {"medium_id": "same", "manifest_sha256": "a" * 64, "verified": True},
        ]
        self.assertTrue(any("copy media must be independent" in item for item in errors(custody=value)))

    def test_wholesale_archive_merge_fails(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["source"]["merge_wholesale"] = True
        self.assertTrue(any("wholesale merge" in item for item in errors(archive=value)))

    def test_missing_archive_disposition_fails(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["artifacts"].pop()
        self.assertTrue(any("disposition matrix is incomplete" in item for item in errors(archive=value)))


if __name__ == "__main__":
    unittest.main()

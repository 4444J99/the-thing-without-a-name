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

    def test_personal_absolute_paths_fail_on_every_supported_platform(self) -> None:
        absolute_paths = (
            "/Users/example/private",
            "/home/example/private",
            "/workspace/private/sound.wav",
            "/tmp/private-recording",
            "C:\\Users\\example\\secret.wav",
            "\\\\server\\share\\secret.wav",
            "//server/share/secret.wav",
            "~/private/secret.wav",
            "file:///private/secret.wav",
        )
        for absolute_path in absolute_paths:
            with self.subTest(absolute_path=absolute_path):
                value = copy.deepcopy(CONVERGENCE)
                value["worktrees"][0]["reason"] = f"stored at {absolute_path}"
                self.assertTrue(
                    any("personal or local absolute path" in item for item in errors(convergence=value))
                )

    def test_every_inventory_category_is_exact(self) -> None:
        categories = {
            "remotes": "remote snapshot is incomplete",
            "branches": "branch snapshot is incomplete",
            "worktrees": "worktree snapshot is incomplete",
            "pull_requests": "pull-request snapshot is incomplete",
            "issues": "issue receipt snapshot is incomplete",
            "agent_outcomes": "agent outcome snapshot is incomplete",
        }
        for category, message in categories.items():
            with self.subTest(category=category):
                value = copy.deepcopy(CONVERGENCE)
                value[category].pop()
                self.assertTrue(any(message in item for item in errors(convergence=value)))
        value = copy.deepcopy(CONVERGENCE)
        value["stashes"].pop("canonical_repository")
        self.assertTrue(any("stash namespace snapshot is incomplete" in item for item in errors(convergence=value)))

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

    def test_missing_medium_cannot_count_as_an_independent_copy(self) -> None:
        value = copy.deepcopy(CUSTODY)
        root = value["roots"][0]
        root["independent_verified_copies"] = [
            {"medium_id": None, "manifest_sha256": "a" * 64, "verified": True},
            {"medium_id": "archive-b", "manifest_sha256": "a" * 64, "verified": True},
        ]
        self.assertTrue(any("copy media must be independent" in item for item in errors(custody=value)))

    def test_copies_must_preserve_the_same_manifest(self) -> None:
        value = copy.deepcopy(CUSTODY)
        root = value["roots"][0]
        root["independent_verified_copies"] = [
            {"medium_id": "archive-a", "manifest_sha256": "a" * 64, "verified": True},
            {"medium_id": "archive-b", "manifest_sha256": "b" * 64, "verified": True},
        ]
        self.assertTrue(any("preserve different manifests" in item for item in errors(custody=value)))

    def test_restore_and_owner_acceptance_need_durable_receipts(self) -> None:
        for receipt in (None, "done"):
            with self.subTest(receipt=receipt):
                value = copy.deepcopy(CUSTODY)
                root = value["roots"][0]
                root["independent_verified_copies"] = [
                    {"medium_id": "archive-a", "manifest_sha256": "a" * 64, "verified": True},
                    {"medium_id": "archive-b", "manifest_sha256": "a" * 64, "verified": True},
                ]
                root["restore_rehearsal"] = {"ok": True, "receipt": receipt}
                root["human_acceptance"] = {"ok": True, "receipt": receipt}
                found = errors(custody=value)
                self.assertTrue(any("clean restore lacks a durable receipt" in item for item in found))
                self.assertTrue(any("owner acceptance lacks a durable receipt" in item for item in found))

    def test_cleanup_authorization_must_be_an_explicit_boolean(self) -> None:
        value = copy.deepcopy(CUSTODY)
        value["roots"][0].pop("cleanup_authorized")
        self.assertTrue(any("cleanup_authorized must be an explicit boolean" in item for item in errors(custody=value)))

    def test_receipt_observation_relationship_is_bound_and_contemporaneous(self) -> None:
        stale = copy.deepcopy(CUSTODY)
        stale["recorded_at"] = "2026-08-03T01:27:26Z"
        found = errors(custody=stale)
        self.assertTrue(any("timestamp does not bind receipt" in item for item in found))

        convergence = copy.deepcopy(CONVERGENCE)
        convergence["snapshot_relationships"]["custody"]["recorded_at"] = stale["recorded_at"]
        found = errors(convergence=convergence, custody=stale)
        self.assertTrue(any("observations are not contemporaneous" in item for item in found))

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

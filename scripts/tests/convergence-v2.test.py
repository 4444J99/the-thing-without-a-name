#!/usr/bin/env python3
"""Fail-closed regressions for the additive Danse convergence v2 census."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "danse_convergence_v2_test", ROOT / "scripts" / "check-convergence-v2.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("convergence v2 checker module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load_module()
CONVERGENCE = CHECK.load(ROOT / CHECK.CONVERGENCE)
CUSTODY = CHECK.load(ROOT / CHECK.CUSTODY)
ARCHIVE = CHECK.load(ROOT / CHECK.ARCHIVE)


def errors(convergence=None, custody=None, archive=None):
    return CHECK.validate_documents(
        copy.deepcopy(CONVERGENCE if convergence is None else convergence),
        copy.deepcopy(CUSTODY if custody is None else custody),
        copy.deepcopy(ARCHIVE if archive is None else archive),
    )


class ConvergenceV2ReceiptTest(unittest.TestCase):
    def test_repository_receipts_validate(self) -> None:
        self.assertEqual(CHECK.audit(ROOT), [])

    def test_v1_receipts_remain_immutable_and_valid(self) -> None:
        self.assertEqual(CHECK.V1.audit(ROOT), [])

    def test_branch_census_cannot_drop_a_visible_ref(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["branches"].pop()
        self.assertTrue(any("branch census is incomplete" in item for item in errors(convergence=value)))

    def test_cleanup_branch_requires_merge_ancestry_and_remote_parity(self) -> None:
        for field, replacement in (
            ("status", "active"),
            ("main_reachable", False),
            ("remote_parity", False),
            ("remote_head", "f" * 40),
        ):
            with self.subTest(field=field):
                value = copy.deepcopy(CONVERGENCE)
                branch = next(
                    item
                    for item in value["branches"]
                    if item["id"] == "agent/alpha-omega-control-surface"
                )
                branch[field] = replacement
                self.assertTrue(
                    any("unsafe cleanup candidate" in item for item in errors(convergence=value))
                )

    def test_candidate_worktree_must_be_clean_and_have_only_reproducible_ignored_bytes(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        worktree = next(
            item for item in value["worktrees"] if item["id"] == "release-manifest-project"
        )
        worktree["untracked_items"] = 1
        worktree["ignored_classes"].append("private-recording")
        found = errors(convergence=value)
        self.assertTrue(any("dirty worktree cannot be reclaimed" in item for item in found))
        self.assertTrue(any("cleanup bypasses inventory gates" in item for item in found))

    def test_protected_worktree_cannot_enter_cleanup(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        protected = next(
            item
            for item in value["worktrees"]
            if item["id"] == "canonical-closeout-material-custody"
        )
        protected["cleanup_authorized"] = True
        value["cleanup"]["eligible_worktrees"].append(protected["id"])
        found = errors(convergence=value)
        self.assertTrue(any("protected worktree cannot be reclaimed" in item for item in found))
        self.assertTrue(any("protected worktree set drifted" in item for item in found))

    def test_main_checkout_divergence_cannot_be_hidden(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        main = next(item for item in value["branches"] if item["id"] == "main")
        main["remote_parity"] = True
        main["cleanup_candidate"] = True
        self.assertTrue(
            any("custody-bearing divergence must remain retained" in item for item in errors(convergence=value))
        )

    def test_local_and_remote_cleanup_targets_are_exact_and_identical(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["cleanup"]["eligible_remote_branches"].pop()
        self.assertTrue(
            any("local and remote branch cleanup targets lack parity" in item for item in errors(convergence=value))
        )

    def test_pull_request_and_issue_censuses_are_exact(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["pull_requests"].pop()
        self.assertTrue(any("pull-request census is incomplete" in item for item in errors(convergence=value)))
        value = copy.deepcopy(CONVERGENCE)
        issue = next(item for item in value["issues"] if item["id"] == 15)
        issue["github_state"] = "open"
        issue["status"] = "active"
        self.assertTrue(any("state receipt drifted" in item for item in errors(convergence=value)))

    def test_successor_inherits_exact_historical_runway(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["successor_launch"]["started_at"] = "2026-08-04T09:56:00Z"
        value["successor_launch"]["deadline_at"] = "2026-08-18T09:56:00Z"
        found = errors(convergence=value)
        self.assertTrue(any("started_at: launch receipt drifted" in item for item in found))
        self.assertTrue(any("deadline_at: launch receipt drifted" in item for item in found))

    def test_successor_lineage_cannot_record_a_local_path(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["successor_launch"]["predecessor"]["path"] = "/Users/example/private-capsule"
        found = errors(convergence=value)
        self.assertTrue(any("personal or local absolute path" in item for item in found))
        self.assertTrue(any("exact redacted predecessor identity" in item for item in found))

    def test_provider_self_invocation_cannot_hide_an_extra_process_or_mutation(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        proof = value["successor_launch"]["self_invocation"]
        proof["additional_provider_processes"] = 1
        proof["receipt_sha256_before_and_after"] = "f" * 64
        self.assertTrue(
            any("provider self-invocation proof drifted" in item for item in errors(convergence=value))
        )

    def test_provider_capacity_blocker_cannot_be_promoted_to_completion(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["successor_launch"]["provider_capacity"]["status"] = "merged"
        self.assertTrue(
            any("provider-capacity blocker" in item for item in errors(convergence=value))
        )

    def test_unrecorded_conversation_cannot_be_reconstructed(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        outcome = next(
            item for item in value["agent_outcomes"] if item["id"] == "unrecorded-conversations"
        )
        outcome["status"] = "ported"
        outcome["receipt"] = "Recovered from memory."
        self.assertTrue(
            any("must remain unreconstructable" in item for item in errors(convergence=value))
        )

    def test_limen_stashes_cannot_be_claimed_as_danse(self) -> None:
        value = copy.deepcopy(CONVERGENCE)
        value["stashes"]["related_limen_repository"]["danse_subject_matches"] = 1
        self.assertTrue(any("stash absence receipt drifted" in item for item in errors(convergence=value)))

    def test_custody_cannot_be_reclaimed_without_copy_restore_and_acceptance(self) -> None:
        value = copy.deepcopy(CUSTODY)
        root = value["roots"][0]
        root["status"] = "merged"
        root["cleanup_authorized"] = True
        self.assertTrue(any("unproven reclamation" in item for item in errors(custody=value)))

    def test_custody_root_count_and_observed_sizes_are_exact(self) -> None:
        value = copy.deepcopy(CUSTODY)
        value["roots"].pop()
        self.assertTrue(any("protected-root census is incomplete" in item for item in errors(custody=value)))
        value = copy.deepcopy(CUSTODY)
        value["roots"][0]["observed_ignored_items"] -= 1
        self.assertTrue(any("observed identity or size drifted" in item for item in errors(custody=value)))

    def test_archive_is_never_merged_wholesale(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        value["source"]["merge_wholesale"] = True
        self.assertTrue(
            any("wholesale-merge prohibition drifted" in item for item in errors(archive=value))
        )

    def test_deliberate_archive_ports_keep_their_rejections_and_external_predicates(self) -> None:
        value = copy.deepcopy(ARCHIVE)
        spatial = next(
            item
            for item in value["artifacts"]
            if item["id"] == "stateful-spatial-score-prototype"
        )
        spatial["status"] = "active"
        spatial.pop("remaining_predicate")
        self.assertTrue(any("disposition status drifted" in item for item in errors(archive=value)))

    def test_private_paths_fail_across_all_v2_receipts(self) -> None:
        for label, source in (
            ("convergence", CONVERGENCE),
            ("custody", CUSTODY),
            ("archive", ARCHIVE),
        ):
            with self.subTest(label=label):
                value = copy.deepcopy(source)
                value["debug_path"] = "C:\\Users\\example\\private.wav"
                kwargs = {label: value}
                self.assertTrue(
                    any("personal or local absolute path" in item for item in errors(**kwargs))
                )


if __name__ == "__main__":
    unittest.main()

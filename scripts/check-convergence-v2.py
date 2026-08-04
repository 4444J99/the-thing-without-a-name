#!/usr/bin/env python3
"""Validate the additive Danse convergence, archive, and custody v2 receipts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_ROOT = Path("docs/continuations/alpha-omega")
CONVERGENCE = RECEIPT_ROOT / "convergence-20260804-v2.json"
CUSTODY = RECEIPT_ROOT / "private-custody-20260804-v2.json"
ARCHIVE = RECEIPT_ROOT / "archive-dispositions-20260804-v2.json"
CLOSEOUT = Path("docs/session-closeout.md")


def load_v1_checker():
    spec = importlib.util.spec_from_file_location(
        "danse_convergence_v1", ROOT / "scripts" / "check-convergence.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("v1 convergence checker could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V1 = load_v1_checker()
STATUSES = V1.STATUSES
GIT_SHA = V1.GIT_SHA
SHA256 = V1.SHA256

EXPECTED_DOCUMENT_DIGESTS = {
    "convergence": "b920b7bb1cd764e891308bdc0ac59d20495e5b0d59b9f1f6e4c73d724ce317f5",
    "custody": "30e7f3473f370874c91a05947a7bc413bfed669a9b0c1fdf75afd2784a0020f7",
    "archive": "21941ea8c7cca8c800991740dbce40043a8a9f6987d54ca4688d146198b025b6",
}
EXPECTED_SUPERSEDED = {
    "convergence": (
        "docs/continuations/alpha-omega/convergence-20260804.json",
        "48921ac617ddb1f167c5b1b51648bae845a5c2d7cc585673ba35ab51943b45c3",
    ),
    "custody": (
        "docs/continuations/alpha-omega/private-custody-20260804.json",
        "65e57686802132b2781fceef06688aee97d07bb2ab910400b758050afb3fd48e",
    ),
    "archive": (
        "docs/continuations/alpha-omega/archive-dispositions-20260804.json",
        "40265b59e750cd4fb6695be8e241e5508886d71ac6b5eff26eb9c6d0f2a9ec88",
    ),
}
EXPECTED_SOURCE_MAIN = "e61df155850dc91b5a86f0cf4d2ef891a4e6d885"
EXPECTED_BRANCHES = {
    "main",
    "agent/alpha-omega-control-surface",
    "agent/screendance-published-terms",
    "docs/alpha-omega-final-convergence-20260804",
    "docs/convergence-custody-receipt-20260803",
    "feat/canonical-import-20260802",
    "feat/installation-digital-twin-20260804",
    "feat/local-pose-input-20260803",
    "feat/music-score-fixtures-20260803",
    "feat/opportunity-registry-snapshot-20260804",
    "feat/private-custody-snapshot-20260804",
    "feat/release-manifest-project-20260804",
    "feat/rights-attribution-register-20260804",
    "feat/spatial-room-events-20260803",
    "fix/pages-hud-allowlist-20260803",
    "fix/reel-publishable-mode-closeout-20260802",
    "fix/reel-single-segment-closeout-20260802",
    "work/canonical-merge-closeout-20260802",
    "work/danse-alpha-omega-20260803",
    "work/danse-alpha-omega-20260803-s2",
}
EXPECTED_WORKTREES = {
    "canonical-default",
    "canonical-import",
    "alpha-omega-control",
    "final-convergence",
    "canonical-closeout-material-custody",
    "convergence-custody",
    "alpha-omega-first-capsule",
    "alpha-omega-successor-capsule",
    "installation-digital-twin",
    "local-pose-input",
    "music-score-fixtures",
    "opportunity-registry",
    "pages-hud-allowlist",
    "private-custody-snapshot",
    "release-manifest-project",
    "rights-attribution",
    "screendance-published-terms",
    "spatial-room-events",
}
EXPECTED_PRS = {1, 4, 5, 6, 18, 19, *range(23, 34)}
EXPECTED_ISSUE_STATE = {
    2: ("open", "blocked"),
    3: ("open", "blocked"),
    7: ("open", "active"),
    8: ("open", "blocked"),
    9: ("open", "blocked"),
    10: ("open", "blocked"),
    11: ("open", "blocked"),
    12: ("open", "blocked"),
    13: ("closed", "merged"),
    14: ("open", "blocked"),
    15: ("closed", "merged"),
    16: ("open", "blocked"),
    17: ("open", "blocked"),
    20: ("open", "active"),
    21: ("open", "blocked"),
    22: ("open", "active"),
}
EXPECTED_AGENT_OUTCOMES = {
    "limen-successor-interface",
    "traversal-repair",
    "control-surface",
    "published-terms",
    "pages-hud",
    "music-score-fixtures",
    "convergence-custody-v1",
    "opportunity-freeze",
    "local-pose-input",
    "release-manifest-project",
    "spatial-room-events",
    "rights-register",
    "private-custody-tooling",
    "installation-digital-twin",
    "successor-launch",
    "limen_1799-session",
    "music_score-session",
    "pages_hud-session",
    "configured-claude-history-search",
    "configured-agy-history-search",
    "configured-codex-history-search",
    "unrecorded-conversations",
}
EXPECTED_ELIGIBLE_WORKTREES = {
    "canonical-import",
    "alpha-omega-control",
    "convergence-custody",
    "installation-digital-twin",
    "local-pose-input",
    "music-score-fixtures",
    "opportunity-registry",
    "pages-hud-allowlist",
    "private-custody-snapshot",
    "release-manifest-project",
    "rights-attribution",
    "screendance-published-terms",
    "spatial-room-events",
}
EXPECTED_PROTECTED_WORKTREES = {
    "canonical-default",
    "final-convergence",
    "canonical-closeout-material-custody",
    "alpha-omega-first-capsule",
    "alpha-omega-successor-capsule",
}
EXPECTED_ELIGIBLE_BRANCHES = {
    "agent/alpha-omega-control-surface",
    "agent/screendance-published-terms",
    "docs/convergence-custody-receipt-20260803",
    "feat/canonical-import-20260802",
    "feat/installation-digital-twin-20260804",
    "feat/local-pose-input-20260803",
    "feat/music-score-fixtures-20260803",
    "feat/opportunity-registry-snapshot-20260804",
    "feat/private-custody-snapshot-20260804",
    "feat/release-manifest-project-20260804",
    "feat/rights-attribution-register-20260804",
    "feat/spatial-room-events-20260803",
    "fix/pages-hud-allowlist-20260803",
    "fix/reel-publishable-mode-closeout-20260802",
    "fix/reel-single-segment-closeout-20260802",
    "work/canonical-merge-closeout-20260802",
}
SAFE_IGNORED_CLASSES = {
    "editable-install-metadata",
    "python-bytecode-cache",
    "ruff-cache",
    "reproducible-release-qa-virtualenv",
    "reproducible-project-and-pdf-raster-audits",
}
EXPECTED_CUSTODY_ROOTS = {
    "canonical-closeout-material-custody": {
        "head": "aa8afef941410c0790f60ea79b1eb04deb3baa43",
        "observed_kib": 59154600,
        "observed_ignored_items": 1203,
    },
    "limen-predecessor-experiment-custody": {
        "head": "a232f2d7160e213802580e2d532a0d2d9ac65727",
        "observed_kib": 1648364,
        "observed_ignored_items": 561,
    },
}
EXPECTED_ARCHIVE_ARTIFACTS = {
    "visitor-pose-prototype": "ported",
    "stateful-spatial-score-prototype": "ported",
    "browser-audio-harness": "archived",
    "cloudflare-deployment-experiment": "superseded",
    "theoretical-framework": "archived",
    "installation-proposal": "ported",
    "submission-attestation": "superseded",
    "private-brainstorm-history": "not recorded",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def unique(records: object, key: str, label: str, errors: list[str]) -> list[dict[str, Any]]:
    return V1.unique(records, key, label, errors)


def validate_supersedes(
    document: dict[str, Any], label: str, errors: list[str]
) -> None:
    value = document.get("supersedes")
    expected_path, expected_digest = EXPECTED_SUPERSEDED[label]
    if not isinstance(value, dict):
        errors.append(f"{label}: missing immutable v1 predecessor binding")
        return
    if value.get("path") != expected_path or value.get("canonical_json_sha256") != expected_digest:
        errors.append(f"{label}: v1 predecessor path or digest drifted")


def validate_documents(
    convergence: dict[str, Any],
    custody: dict[str, Any],
    archive: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    documents = (
        (convergence, "danse.convergence.v2", "convergence"),
        (custody, "danse.private-custody.v2", "custody"),
        (archive, "danse.archive-dispositions.v2", "archive"),
    )
    for document, schema, label in documents:
        if V1.canonical_digest(document) != EXPECTED_DOCUMENT_DIGESTS[label]:
            errors.append(f"{label}: immutable v2 receipt content digest drifted")
        if document.get("schema") != schema:
            errors.append(f"{label}: expected schema {schema}")
        if document.get("recorded_at") != "2026-08-04T09:56:00Z":
            errors.append(f"{label}: observation timestamp drifted")
        V1.validate_statuses(document, label, errors)
        V1.validate_no_private_paths(document, label, errors)
        validate_supersedes(document, label, errors)

    if set(convergence.get("status_vocabulary") or []) != STATUSES:
        errors.append("convergence: lifecycle vocabulary must remain exact")
    scope = convergence.get("scope") or {}
    if scope.get("canonical_repository") != "organvm/the-thing-without-a-name":
        errors.append("convergence: canonical repository drifted")
    if scope.get("source_main") != EXPECTED_SOURCE_MAIN:
        errors.append("convergence: source main drifted")

    relationships = convergence.get("snapshot_relationships") or {}
    relationship_paths = {
        "custody": str(CUSTODY),
        "archive": str(ARCHIVE),
    }
    if set(relationships) != set(relationship_paths):
        errors.append("convergence: v2 snapshot relationships are incomplete")
    else:
        for label, path in relationship_paths.items():
            value = relationships.get(label) or {}
            if value.get("path") != path or value.get("recorded_at") != convergence.get("recorded_at"):
                errors.append(f"convergence: {label} snapshot is not contemporaneously bound")

    remotes = unique(convergence.get("remotes"), "id", "convergence.remotes", errors)
    if {item.get("id") for item in remotes} != {"canonical-origin", "predecessor-archive-origin"}:
        errors.append("convergence: remote inventory is incomplete")
    for remote in remotes:
        if remote.get("fetch") != remote.get("push"):
            errors.append(f"convergence.remotes[{remote.get('id')}]: fetch/push parity missing")

    branches = unique(convergence.get("branches"), "id", "convergence.branches", errors)
    branch_ids = {item.get("id") for item in branches}
    branch_by_id = {item.get("id"): item for item in branches}
    if branch_ids != EXPECTED_BRANCHES:
        errors.append("convergence: branch census is incomplete")
    for branch in branches:
        branch_id = branch.get("id")
        local_head = branch.get("local_head")
        remote_head = branch.get("remote_head")
        if not GIT_SHA.fullmatch(str(local_head or "")):
            errors.append(f"convergence.branches[{branch_id}]: invalid local head")
        if remote_head is not None and not GIT_SHA.fullmatch(str(remote_head)):
            errors.append(f"convergence.branches[{branch_id}]: invalid remote head")
        if branch.get("cleanup_candidate") is True:
            if not (
                branch.get("status") == "merged"
                and branch.get("main_reachable") is True
                and branch.get("remote_parity") is True
                and local_head == remote_head
                and branch_id in EXPECTED_ELIGIBLE_BRANCHES
            ):
                errors.append(f"convergence.branches[{branch_id}]: unsafe cleanup candidate")
        if not str(branch.get("receipt", "")).strip():
            errors.append(f"convergence.branches[{branch_id}]: missing outcome receipt")
    main = branch_by_id.get("main") or {}
    if main.get("cleanup_candidate") is not False or main.get("remote_parity") is not False:
        errors.append("convergence.branches[main]: custody-bearing divergence must remain retained")
    successor_branch = branch_by_id.get("work/danse-alpha-omega-20260803-s2") or {}
    if successor_branch.get("main_reachable") is not False or successor_branch.get("status") != "active":
        errors.append("convergence: successor publication branch must remain distinct and active")

    worktrees = unique(convergence.get("worktrees"), "id", "convergence.worktrees", errors)
    worktree_ids = {item.get("id") for item in worktrees}
    worktree_by_id = {item.get("id"): item for item in worktrees}
    if worktree_ids != EXPECTED_WORKTREES:
        errors.append("convergence: worktree census is incomplete")
    for worktree in worktrees:
        worktree_id = worktree.get("id")
        branch_id = worktree.get("branch")
        if not GIT_SHA.fullmatch(str(worktree.get("head", ""))):
            errors.append(f"convergence.worktrees[{worktree_id}]: invalid head")
        if branch_id is not None:
            branch = branch_by_id.get(branch_id)
            if branch is None or branch.get("local_head") != worktree.get("head"):
                errors.append(f"convergence.worktrees[{worktree_id}]: branch/head mismatch")
        if worktree.get("tracked_clean") is not True or worktree.get("untracked_items") != 0:
            if worktree.get("cleanup_authorized") is True:
                errors.append(f"convergence.worktrees[{worktree_id}]: dirty worktree cannot be reclaimed")
        ignored_classes = worktree.get("ignored_classes")
        if not isinstance(ignored_classes, list):
            errors.append(f"convergence.worktrees[{worktree_id}]: ignored inventory is not a list")
            ignored_classes = []
        if worktree.get("cleanup_authorized") is True:
            branch = branch_by_id.get(branch_id) or {}
            if (
                worktree_id not in EXPECTED_ELIGIBLE_WORKTREES
                or branch.get("cleanup_candidate") is not True
                or not set(ignored_classes) <= SAFE_IGNORED_CLASSES
            ):
                errors.append(f"convergence.worktrees[{worktree_id}]: cleanup bypasses inventory gates")
        if worktree_id in EXPECTED_PROTECTED_WORKTREES and worktree.get("cleanup_authorized") is not False:
            errors.append(f"convergence.worktrees[{worktree_id}]: protected worktree cannot be reclaimed")
    release_worktree = worktree_by_id.get("release-manifest-project") or {}
    if release_worktree.get("qa_raster_files") != 34:
        errors.append("convergence: release QA raster inventory drifted")

    cleanup = convergence.get("cleanup") or {}
    eligible_worktrees = cleanup.get("eligible_worktrees") or []
    eligible_local = cleanup.get("eligible_local_branches") or []
    eligible_remote = cleanup.get("eligible_remote_branches") or []
    protected = cleanup.get("protected_worktrees") or []
    if set(eligible_worktrees) != EXPECTED_ELIGIBLE_WORKTREES or len(eligible_worktrees) != len(set(eligible_worktrees)):
        errors.append("convergence: eligible worktree cleanup set drifted")
    if set(eligible_local) != EXPECTED_ELIGIBLE_BRANCHES or len(eligible_local) != len(set(eligible_local)):
        errors.append("convergence: eligible local branch cleanup set drifted")
    if eligible_local != eligible_remote:
        errors.append("convergence: local and remote branch cleanup targets lack parity")
    if set(protected) != EXPECTED_PROTECTED_WORKTREES or set(protected) & set(eligible_worktrees):
        errors.append("convergence: protected worktree set drifted or overlaps cleanup")

    stashes = convergence.get("stashes") or {}
    if stashes.get("canonical_repository") != []:
        errors.append("convergence: canonical stash inventory must remain empty")
    related_stashes = stashes.get("related_limen_repository") or {}
    if (
        related_stashes.get("observed_count") != 16
        or related_stashes.get("danse_subject_matches") != 0
        or related_stashes.get("status") != "not recorded"
    ):
        errors.append("convergence: related Limen stash absence receipt drifted")

    prs = unique(convergence.get("pull_requests"), "id", "convergence.pull_requests", errors)
    if {item.get("id") for item in prs} != EXPECTED_PRS:
        errors.append("convergence: pull-request census is incomplete")
    for pr in prs:
        if pr.get("status") != "merged" or not GIT_SHA.fullmatch(str(pr.get("head", ""))) or not GIT_SHA.fullmatch(str(pr.get("merge", ""))):
            errors.append(f"convergence.pull_requests[{pr.get('id')}]: non-terminal or invalid receipt")

    issues = unique(convergence.get("issues"), "id", "convergence.issues", errors)
    if {item.get("id") for item in issues} != set(EXPECTED_ISSUE_STATE):
        errors.append("convergence: issue census is incomplete")
    for issue in issues:
        expected = EXPECTED_ISSUE_STATE.get(issue.get("id"))
        if expected is not None and (issue.get("github_state"), issue.get("status")) != expected:
            errors.append(f"convergence.issues[{issue.get('id')}]: state receipt drifted")
        if not str(issue.get("receipt", "")).strip():
            errors.append(f"convergence.issues[{issue.get('id')}]: missing terminal predicate")

    related = convergence.get("related_repository") or {}
    if related.get("repository") != "organvm/limen" or related.get("default_head") != "8a867064d76b8f574b2586d2dd737ed75e550bef":
        errors.append("convergence: related Limen default receipt drifted")
    archive_branch = related.get("archive_branch") or {}
    if not (
        archive_branch.get("id") == "archive/danse-predecessor-experiments-20260802"
        and archive_branch.get("local_head") == "a232f2d7160e213802580e2d532a0d2d9ac65727"
        and archive_branch.get("remote_head") == archive_branch.get("local_head")
        and archive_branch.get("merge_wholesale") is False
    ):
        errors.append("convergence: related archive branch identity drifted")
    limen_prs = unique(related.get("pull_requests"), "id", "convergence.related.pull_requests", errors)
    if {item.get("id") for item in limen_prs} != {1799, 1804} or any(item.get("status") != "merged" for item in limen_prs):
        errors.append("convergence: Limen successor-interface PR receipt is incomplete")

    successor = convergence.get("successor_launch") or {}
    expected_successor = {
        "slug": "danse-alpha-omega-20260803-s2",
        "branch": "work/danse-alpha-omega-20260803-s2",
        "reviewed_main_merge_head": "92f6953408e5d2ce5c0cddba376d5e64a07a375b",
        "publication_head": "887dd5472adffc3b8768700502454c7ee887c5f5",
        "receipt_sha256": "2b1acb5af6d01d9adacf1f29cb96f64238c3e5b7af5b390f0c755bae52d91d1a",
        "started_at": "2026-08-03T19:22:22Z",
        "deadline_at": "2026-08-17T19:22:22Z",
        "runway_mode": "inherit",
        "provider": "codex",
        "sandbox": "workspace-write",
        "host_launch_count": 1,
        "bounded_fetch_count": 1,
        "receipt_publication_commit_count": 1,
        "provider_process_count": 1,
        "fetch_head_error": None,
    }
    for key, expected in expected_successor.items():
        if successor.get(key) != expected:
            errors.append(f"convergence.successor_launch.{key}: launch receipt drifted")
    predecessor = successor.get("predecessor") or {}
    if predecessor != {
        "slug": "danse-alpha-omega-20260803",
        "branch": "work/danse-alpha-omega-20260803",
        "receipt_sha256": "0a71bad89ea706bbeb04645b54898d2414273b90d6c77a6f3878dc1b2e9c89fd",
    }:
        errors.append("convergence: successor lineage is not the exact redacted predecessor identity")
    self_invocation = successor.get("self_invocation") or {}
    if not (
        self_invocation.get("additional_provider_processes") == 0
        and self_invocation.get("head_before_and_after") == successor.get("publication_head")
        and self_invocation.get("receipt_sha256_before_and_after") == successor.get("receipt_sha256")
        and self_invocation.get("fetch_head_sha256_before_and_after") == "9d3b3a10de29756077324262e4eebc4ed51b6f8fdb62a4aec9d6690c050782da"
        and self_invocation.get("lock_sha256_before_and_after") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ):
        errors.append("convergence: provider self-invocation proof drifted")
    if (successor.get("provider_capacity") or {}).get("status") != "blocked":
        errors.append("convergence: provider-capacity blocker is not honestly recorded")

    outcomes = unique(convergence.get("agent_outcomes"), "id", "convergence.agent_outcomes", errors)
    if {item.get("id") for item in outcomes} != EXPECTED_AGENT_OUTCOMES:
        errors.append("convergence: visible agent outcome census is incomplete")
    unrecorded = next((item for item in outcomes if item.get("id") == "unrecorded-conversations"), None)
    if not unrecorded or unrecorded.get("status") != "not recorded" or "cannot be reconstructed" not in str(unrecorded.get("receipt", "")):
        errors.append("convergence: unrecorded conversations must remain unreconstructable")
    if convergence.get("closeout_rule") != str(CLOSEOUT):
        errors.append("convergence: closeout rule link drifted")

    policy = custody.get("policy") or {}
    if not (
        policy.get("required_independent_verified_copies") == 2
        and policy.get("clean_restore_required") is True
        and policy.get("human_acceptance_required") is True
        and policy.get("tracked_receipt_is_redacted") is True
        and policy.get("private_or_personal_paths_forbidden") is True
    ):
        errors.append("custody: copy, restore, acceptance, or redaction floor drifted")
    tooling = custody.get("tooling_receipt") or {}
    if not (
        tooling.get("status") == "merged"
        and tooling.get("reviewed_head") == "ac50e3ed8bf87681559db712177b4f0870c7222b"
        and tooling.get("merge") == "7c6bf8b105e57b0f8dc91164a7e4af1e0c083730"
    ):
        errors.append("custody: snapshot-tooling receipt drifted")
    roots = unique(custody.get("roots"), "id", "custody.roots", errors)
    if {item.get("id") for item in roots} != set(EXPECTED_CUSTODY_ROOTS):
        errors.append("custody: protected-root census is incomplete")
    for root in roots:
        expected = EXPECTED_CUSTODY_ROOTS.get(root.get("id"))
        if expected is not None and any(root.get(key) != value for key, value in expected.items()):
            errors.append(f"custody.roots[{root.get('id')}]: observed identity or size drifted")
        if not (
            root.get("status") == "blocked"
            and root.get("tracked_tree_clean") is True
            and root.get("independent_verified_copies") == []
            and root.get("restore_rehearsal") == {"ok": False, "receipt": None}
            and root.get("human_acceptance") == {"ok": False, "receipt": None}
            and root.get("cleanup_authorized") is False
        ):
            errors.append(f"custody.roots[{root.get('id')}]: unproven reclamation or evidence promotion")
    material = worktree_by_id.get("canonical-closeout-material-custody") or {}
    canonical_custody = next((item for item in roots if item.get("id") == "canonical-closeout-material-custody"), {})
    if material.get("head") != canonical_custody.get("head") or material.get("ignored_items") != canonical_custody.get("observed_ignored_items"):
        errors.append("custody: canonical material worktree cross-reference drifted")

    source = archive.get("source") or {}
    if source != {
        "repository": "organvm/limen",
        "branch": "archive/danse-predecessor-experiments-20260802",
        "commit": "a232f2d7160e213802580e2d532a0d2d9ac65727",
        "remote_parity": True,
        "merge_wholesale": False,
    }:
        errors.append("archive: source identity or wholesale-merge prohibition drifted")
    artifacts = unique(archive.get("artifacts"), "id", "archive.artifacts", errors)
    if {item.get("id") for item in artifacts} != set(EXPECTED_ARCHIVE_ARTIFACTS):
        errors.append("archive: disposition matrix is incomplete")
    for artifact in artifacts:
        artifact_id = artifact.get("id")
        if artifact.get("status") != EXPECTED_ARCHIVE_ARTIFACTS.get(artifact_id):
            errors.append(f"archive.artifacts[{artifact_id}]: disposition status drifted")
        paths = artifact.get("paths")
        if not isinstance(paths, list) or any(Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
            errors.append(f"archive.artifacts[{artifact_id}]: unsafe source path")
        if not str(artifact.get("receipt", "")).strip() or not str(artifact.get("disposition", "")).strip():
            errors.append(f"archive.artifacts[{artifact_id}]: missing disposition receipt")
    brainstorm = next((item for item in artifacts if item.get("id") == "private-brainstorm-history"), None)
    if not brainstorm or "cannot be reconstructed" not in str(brainstorm.get("disposition", "")):
        errors.append("archive: private brainstorm absence is not honestly recorded")
    if (archive.get("issue_state") or {}).get("status") != "blocked":
        errors.append("archive: issue #3 must retain the inherited real-room blocker")

    return errors


def audit(root: Path = ROOT) -> list[str]:
    errors = [f"v1: {error}" for error in V1.audit(root)]
    try:
        convergence = load(root / CONVERGENCE)
        custody = load(root / CUSTODY)
        archive = load(root / ARCHIVE)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [*errors, str(exc)]
    return [*errors, *validate_documents(convergence, custody, archive)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    errors = audit(args.root.resolve())
    if errors:
        if not args.quiet:
            for error in errors:
                print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print("danse convergence v2: exact census valid; protected custody remains fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

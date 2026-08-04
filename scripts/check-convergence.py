#!/usr/bin/env python3
"""Validate Danse's redacted convergence, archive, and private-custody receipts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_ROOT = Path("docs/continuations/alpha-omega")
CONVERGENCE = RECEIPT_ROOT / "convergence-20260804.json"
CUSTODY = RECEIPT_ROOT / "private-custody-20260804.json"
ARCHIVE = RECEIPT_ROOT / "archive-dispositions-20260804.json"
CLOSEOUT = Path("docs/session-closeout.md")

STATUSES = {
    "merged",
    "active",
    "archived",
    "ported",
    "superseded",
    "blocked",
    "not recorded",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_PATH = re.compile(r"(?:^|[\s'\"`])(?:/Users/|~/|file://)")
EXPECTED_ISSUES = {2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 22}
EXPECTED_PRS = {1, 4, 5, 6, 18, 19, 23, 24, 25, 26}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def parse_time(value: object, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        errors.append(f"{label}: recorded_at must be an RFC 3339 UTC timestamp")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: invalid recorded_at {value!r}")


def walk(value: Any, label: str = "root"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{label}[{index}]")
    else:
        yield label, value


def unique(records: object, key: str, label: str, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        errors.append(f"{label}: must be a list")
        return []
    objects = [record for record in records if isinstance(record, dict)]
    if len(objects) != len(records):
        errors.append(f"{label}: every record must be an object")
    values = [record.get(key) for record in objects]
    if any(value in (None, "") for value in values):
        errors.append(f"{label}: every record needs {key}")
    if len(set(map(str, values))) != len(values):
        errors.append(f"{label}: duplicate {key}")
    return objects


def validate_statuses(document: dict[str, Any], label: str, errors: list[str]) -> None:
    for path, value in walk(document, label):
        if path.endswith(".status") and value not in STATUSES:
            errors.append(f"{path}: unsupported convergence status {value!r}")


def validate_no_private_paths(document: dict[str, Any], label: str, errors: list[str]) -> None:
    for path, value in walk(document, label):
        if isinstance(value, str) and PRIVATE_PATH.search(value):
            errors.append(f"{path}: tracked receipt contains a personal or local absolute path")


def validate_documents(
    convergence: dict[str, Any],
    custody: dict[str, Any],
    archive: dict[str, Any],
    closeout_text: str,
) -> list[str]:
    errors: list[str] = []
    expected_schemas = (
        (convergence, "danse.convergence.v1", "convergence"),
        (custody, "danse.private-custody.v1", "custody"),
        (archive, "danse.archive-dispositions.v1", "archive"),
    )
    for document, schema, label in expected_schemas:
        if document.get("schema") != schema:
            errors.append(f"{label}: expected schema {schema}")
        parse_time(document.get("recorded_at"), label, errors)
        validate_statuses(document, label, errors)
        validate_no_private_paths(document, label, errors)

    if set(convergence.get("status_vocabulary") or []) != STATUSES:
        errors.append("convergence: status_vocabulary must contain the exact closed vocabulary")

    scope = convergence.get("scope") or {}
    if scope.get("canonical_repository") != "organvm/the-thing-without-a-name":
        errors.append("convergence: wrong canonical repository")
    if not GIT_SHA.fullmatch(str(scope.get("source_main", ""))):
        errors.append("convergence: source_main must be a full Git object id")

    remotes = unique(convergence.get("remotes"), "id", "convergence.remotes", errors)
    canonical = next((record for record in remotes if record.get("id") == "canonical-origin"), None)
    if not canonical or canonical.get("fetch") != canonical.get("push"):
        errors.append("convergence: canonical fetch/push remote parity is not recorded")

    branches = unique(convergence.get("branches"), "id", "convergence.branches", errors)
    branch_ids = {record.get("id") for record in branches}
    for record in branches:
        if not GIT_SHA.fullmatch(str(record.get("head", ""))):
            errors.append(f"convergence.branches[{record.get('id')}]: head must be a full Git object id")
        if not str(record.get("receipt", "")).strip():
            errors.append(f"convergence.branches[{record.get('id')}]: missing receipt")

    worktrees = unique(convergence.get("worktrees"), "id", "convergence.worktrees", errors)
    for record in worktrees:
        branch = record.get("branch")
        if branch is not None and branch not in branch_ids:
            errors.append(f"convergence.worktrees[{record.get('id')}]: unknown branch {branch!r}")
        if not GIT_SHA.fullmatch(str(record.get("head", ""))):
            errors.append(f"convergence.worktrees[{record.get('id')}]: head must be a full Git object id")
        if record.get("cleanup_authorized") is not False:
            errors.append(
                f"convergence.worktrees[{record.get('id')}]: snapshot does not authorize cleanup"
            )

    stashes = convergence.get("stashes") or {}
    if stashes.get("canonical_repository") != []:
        errors.append("convergence: canonical stash inventory must be the observed empty list")
    related_stashes = stashes.get("related_limen_repository") or {}
    if not isinstance(related_stashes.get("observed_count"), int):
        errors.append("convergence: related Limen stash namespace needs an observed count")
    if related_stashes.get("status") != "not recorded":
        errors.append("convergence: unrelated Limen stash attribution must remain not recorded")

    prs = unique(convergence.get("pull_requests"), "id", "convergence.pull_requests", errors)
    if {record.get("id") for record in prs} != EXPECTED_PRS:
        errors.append("convergence: pull-request snapshot is incomplete")
    for record in prs:
        if not GIT_SHA.fullmatch(str(record.get("head", ""))):
            errors.append(f"convergence.pull_requests[{record.get('id')}]: head must be a full Git object id")
        if record.get("status") == "merged":
            if not GIT_SHA.fullmatch(str(record.get("merge", ""))):
                errors.append(
                    f"convergence.pull_requests[{record.get('id')}]: merge must be a full Git object id"
                )
        elif record.get("status") == "active":
            if record.get("merge") is not None:
                errors.append(f"convergence.pull_requests[{record.get('id')}]: active PR cannot name a merge")
        else:
            errors.append(f"convergence.pull_requests[{record.get('id')}]: expected active or merged")

    issues = unique(convergence.get("issues"), "id", "convergence.issues", errors)
    if {record.get("id") for record in issues} != EXPECTED_ISSUES:
        errors.append("convergence: issue receipt snapshot is incomplete")
    if any(not str(record.get("receipt", "")).strip() for record in issues):
        errors.append("convergence: every issue needs a durable owner/predicate receipt")

    outcomes = unique(convergence.get("agent_outcomes"), "id", "convergence.agent_outcomes", errors)
    if any(not str(record.get("receipt", "")).strip() for record in outcomes):
        errors.append("convergence: every visible agent outcome needs a receipt or honest absence note")
    unrecorded = next(
        (record for record in outcomes if record.get("id") == "unrecorded-conversations"), None
    )
    if not unrecorded or unrecorded.get("status") != "not recorded" or "cannot be reconstructed" not in str(
        unrecorded.get("receipt", "")
    ):
        errors.append("convergence: unrecorded conversations must be named as unreconstructable")

    policy = custody.get("policy") or {}
    required_copies = policy.get("required_independent_verified_copies")
    if required_copies != 2 or policy.get("clean_restore_required") is not True:
        errors.append("custody: the two-copy and clean-restore floor is immutable")
    if policy.get("human_acceptance_required") is not True:
        errors.append("custody: cleanup must retain the owner-acceptance gate")

    custody_roots = unique(custody.get("roots"), "id", "custody.roots", errors)
    worktree_by_id = {record.get("id"): record for record in worktrees}
    for record in custody_roots:
        copies = record.get("independent_verified_copies")
        if not isinstance(copies, list):
            errors.append(f"custody.roots[{record.get('id')}]: copies must be a list")
            copies = []
        media = set()
        for copy in copies:
            if not isinstance(copy, dict):
                errors.append(f"custody.roots[{record.get('id')}]: copy receipt must be an object")
                continue
            medium = copy.get("medium_id")
            digest = copy.get("manifest_sha256")
            if not medium or medium in media:
                errors.append(f"custody.roots[{record.get('id')}]: copy media must be independent")
            media.add(medium)
            if copy.get("verified") is not True or not SHA256.fullmatch(str(digest or "")):
                errors.append(f"custody.roots[{record.get('id')}]: invalid checksum copy receipt")

        restore_ok = (record.get("restore_rehearsal") or {}).get("ok") is True
        human_ok = (record.get("human_acceptance") or {}).get("ok") is True
        eligible = len(media) >= required_copies and restore_ok and human_ok and record.get("tracked_tree_clean") is True
        if record.get("cleanup_authorized") is True and not eligible:
            errors.append(f"custody.roots[{record.get('id')}]: cleanup bypasses copy/restore/acceptance gates")
        if record.get("cleanup_authorized") is False and record.get("status") != "blocked":
            errors.append(f"custody.roots[{record.get('id')}]: retained material must be classified blocked")
        linked = worktree_by_id.get(record.get("id"))
        if not linked or linked.get("cleanup_authorized") is not False:
            errors.append(f"custody.roots[{record.get('id')}]: no fail-closed worktree cross-reference")

    source = archive.get("source") or {}
    if source.get("merge_wholesale") is not False:
        errors.append("archive: predecessor branch must never be a wholesale merge candidate")
    if not GIT_SHA.fullmatch(str(source.get("commit", ""))):
        errors.append("archive: source commit must be exact")
    artifacts = unique(archive.get("artifacts"), "id", "archive.artifacts", errors)
    required_artifacts = {
        "visitor-pose-prototype",
        "stateful-spatial-score-prototype",
        "browser-audio-harness",
        "cloudflare-deployment-experiment",
        "theoretical-framework",
        "installation-proposal",
        "submission-attestation",
        "private-brainstorm-history",
    }
    if {record.get("id") for record in artifacts} != required_artifacts:
        errors.append("archive: disposition matrix is incomplete")
    for record in artifacts:
        paths = record.get("paths")
        if not isinstance(paths, list) or any(Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
            errors.append(f"archive.artifacts[{record.get('id')}]: paths must be safe relative paths")
        if not str(record.get("disposition", "")).strip():
            errors.append(f"archive.artifacts[{record.get('id')}]: missing disposition")
    brainstorm = next((record for record in artifacts if record.get("id") == "private-brainstorm-history"), None)
    if not brainstorm or brainstorm.get("status") != "not recorded" or "cannot be reconstructed" not in str(
        brainstorm.get("disposition", "")
    ):
        errors.append("archive: missing honest private-brainstorm absence receipt")

    normalized_closeout = " ".join(closeout_text.split())
    closeout_requirements = (
        "pushed commit and pull request",
        "explicit no-change or blocker receipt",
        "cannot be reconstructed",
        "two independent checksum-verified copies",
        "clean restore rehearsal",
    )
    for phrase in closeout_requirements:
        if phrase not in normalized_closeout:
            errors.append(f"closeout: missing required rule {phrase!r}")
    if convergence.get("closeout_rule") != str(CLOSEOUT):
        errors.append("convergence: closeout rule link is not canonical")

    return errors


def audit(root: Path = ROOT) -> list[str]:
    try:
        convergence = load(root / CONVERGENCE)
        custody = load(root / CUSTODY)
        archive = load(root / ARCHIVE)
        closeout = (root / CLOSEOUT).read_text(encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    return validate_documents(convergence, custody, archive, closeout)


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
        print("danse convergence: receipts valid; material custody remains fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

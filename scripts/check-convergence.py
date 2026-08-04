#!/usr/bin/env python3
"""Validate Danse's redacted convergence, archive, and private-custody receipts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
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
HTTPS_RECEIPT = re.compile(r"^https://\S+$")
PRIVATE_PATH = re.compile(
    r"(?:^|[\s'\"`(\[])"
    r"(?:/(?!/)[^\s'\"`)]*|//[^/\s]+/[^\s'\"`)]*|[A-Za-z]:[\\/][^\s'\"`)]*|"
    r"\\\\[^\\/\s]+[\\/][^\s'\"`)]*|~[\\/][^\s'\"`)]*|file://[^\s'\"`)]*)"
)
EXPECTED_REMOTES = {"canonical-origin", "predecessor-archive-origin"}
EXPECTED_BRANCHES = {
    "main",
    "agent/alpha-omega-control-surface",
    "agent/screendance-published-terms",
    "docs/convergence-custody-receipt-20260803",
    "feat/canonical-import-20260802",
    "feat/local-pose-input-20260803",
    "feat/music-score-fixtures-20260803",
    "fix/pages-hud-allowlist-20260803",
    "fix/reel-publishable-mode-closeout-20260802",
    "fix/reel-single-segment-closeout-20260802",
    "work/canonical-merge-closeout-20260802",
    "work/danse-alpha-omega-20260803",
    "archive/danse-predecessor-experiments-20260802",
}
EXPECTED_WORKTREES = {
    "canonical-default",
    "canonical-import",
    "alpha-omega-control",
    "canonical-closeout-material-custody",
    "convergence-custody",
    "alpha-omega-first-capsule",
    "local-pose-input",
    "music-score-fixtures",
    "pages-hud-allowlist",
    "screendance-published-terms",
    "limen-predecessor-experiment-custody",
}
EXPECTED_AGENT_OUTCOMES = {
    "traversal-repair",
    "control-surface",
    "published-terms",
    "pages-hud",
    "limen-successor-interface",
    "music-score-fixtures",
    "local-pose-input",
    "convergence-custody",
    "configured-claude-history-search",
    "configured-agy-history-search",
    "unrecorded-conversations",
}
EXPECTED_ISSUES = {2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 22}
EXPECTED_PRS = {1, 4, 5, 6, 18, 19, 23, 24, 25, 26}
EXPECTED_CUSTODY_ROOTS = {
    "canonical-closeout-material-custody",
    "limen-predecessor-experiment-custody",
}
SNAPSHOT_MAX_AGE_SECONDS = 600
EXPECTED_ARCHIVE_SOURCE = {
    "repository": "organvm/limen",
    "branch": "archive/danse-predecessor-experiments-20260802",
    "commit": "a232f2d7160e213802580e2d532a0d2d9ac65727",
    "merge_wholesale": False,
}
EXPECTED_ARCHIVE_ARTIFACTS = {
    "visitor-pose-prototype": {
        "paths": ["apps/danse/join.html", "apps/danse/index.html"],
        "status": "active",
        "disposition": "Port only the body-derived modulation concept through issue #13. Reject the CDN-dependent implementation and require explicit permission, dropout, privacy, accessibility, and no-camera behavior.",
    },
    "stateful-spatial-score-prototype": {
        "paths": ["apps/danse/engine/score.js", "apps/danse/sound/score.py"],
        "status": "superseded",
        "disposition": "Reject accumulated WebAudio scheduling and timer state as a canonical engine contract. Port only deterministic typed musical and plane-event ideas through issues #9 and #11, queried by absolute time.",
    },
    "browser-audio-harness": {
        "paths": ["apps/danse/test-audio.html"],
        "status": "archived",
        "disposition": "Retain as historical internal test material. It is not an artwork route and must not enter the Pages allowlist or release artifact.",
    },
    "cloudflare-deployment-experiment": {
        "paths": [".github/workflows/deploy-danse.yml"],
        "status": "superseded",
        "disposition": "Superseded by the staged, allowlisted GitHub Pages artifact merged in Danse PR #24. Do not port the workflow.",
    },
    "theoretical-framework": {
        "paths": ["docs/plans/2026-07-31-danse-theoretical-framework.md"],
        "status": "archived",
        "disposition": "Preserve as historical working theory. It contains unapproved claims and is not cleared public copy; any future use requires deliberate editing and release-manifest evidence.",
    },
    "installation-proposal": {
        "paths": ["docs/plans/danse-installation-spec.md", "organs/artist/chambers/danse.yaml"],
        "status": "active",
        "disposition": "Use as non-authoritative input to issue #14. Venue-approved dimensions, routing, recovery, and three wall-plug proofs must come from the canonical repository before this can be called a specification.",
    },
    "submission-attestation": {
        "paths": ["apps/danse/.work/submission/attest.yaml"],
        "status": "superseded",
        "disposition": "The archive commit removed the unsupported attestation. Filing and upload can be recorded only by the canonical phase predicates in issue #2.",
    },
    "private-brainstorm-history": {
        "paths": [],
        "status": "not recorded",
        "disposition": "No Danse-specific durable artifact was found in the configured Claude, Agy, or Codex history surfaces. Unrecorded conversations cannot be reconstructed; no creative claim is inferred from their absence.",
    },
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def parse_time(value: object, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        errors.append(f"{label}: recorded_at must be an RFC 3339 UTC timestamp")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: invalid recorded_at {value!r}")
        return None


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


def durable_receipt(value: object, root: Path = ROOT) -> bool:
    """Accept a durable HTTPS receipt or an existing tracked file under docs/."""
    if not isinstance(value, str) or not value:
        return False
    if HTTPS_RECEIPT.fullmatch(value):
        return True
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.parts[0] != "docs"
        or "." in pure.parts
        or ".." in pure.parts
        or "\\" in value
    ):
        return False
    repository = root.resolve()
    current = repository
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            return False
    try:
        target = current.resolve(strict=True)
        target.relative_to(repository)
    except (FileNotFoundError, ValueError):
        return False
    if not target.is_file():
        return False
    try:
        tracked = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "--error-unmatch", "--", value],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return tracked.returncode == 0


def validate_documents(
    convergence: dict[str, Any],
    custody: dict[str, Any],
    archive: dict[str, Any],
    closeout_text: str,
    root: Path = ROOT,
) -> list[str]:
    errors: list[str] = []
    expected_schemas = (
        (convergence, "danse.convergence.v1", "convergence"),
        (custody, "danse.private-custody.v1", "custody"),
        (archive, "danse.archive-dispositions.v1", "archive"),
    )
    observed_times: dict[str, datetime] = {}
    for document, schema, label in expected_schemas:
        if document.get("schema") != schema:
            errors.append(f"{label}: expected schema {schema}")
        recorded_at = parse_time(document.get("recorded_at"), label, errors)
        if recorded_at is not None:
            observed_times[label] = recorded_at
        validate_statuses(document, label, errors)
        validate_no_private_paths(document, label, errors)

    relationships = convergence.get("snapshot_relationships")
    expected_relationships = {
        "custody": custody.get("recorded_at"),
        "archive": archive.get("recorded_at"),
    }
    if not isinstance(relationships, dict) or set(relationships) != set(expected_relationships):
        errors.append("convergence: snapshot relationships must bind custody and archive observations")
    else:
        convergence_time = observed_times.get("convergence")
        for label, recorded_at in expected_relationships.items():
            relation = relationships.get(label)
            if not isinstance(relation, dict) or set(relation) != {
                "recorded_at",
                "relation",
                "max_age_seconds",
            }:
                errors.append(f"convergence.snapshot_relationships.{label}: unknown shape")
                continue
            if relation.get("recorded_at") != recorded_at:
                errors.append(f"convergence.snapshot_relationships.{label}: timestamp does not bind receipt")
            if relation.get("relation") != "precedes-convergence-census":
                errors.append(f"convergence.snapshot_relationships.{label}: unsupported relation")
            observed = observed_times.get(label)
            if (
                convergence_time is None
                or observed is None
                or relation.get("max_age_seconds") != SNAPSHOT_MAX_AGE_SECONDS
                or not 0
                <= (convergence_time - observed).total_seconds()
                <= SNAPSHOT_MAX_AGE_SECONDS
            ):
                errors.append(f"convergence.snapshot_relationships.{label}: observations are not contemporaneous")

    if set(convergence.get("status_vocabulary") or []) != STATUSES:
        errors.append("convergence: status_vocabulary must contain the exact closed vocabulary")

    scope = convergence.get("scope") or {}
    if scope.get("canonical_repository") != "organvm/the-thing-without-a-name":
        errors.append("convergence: wrong canonical repository")
    if not GIT_SHA.fullmatch(str(scope.get("source_main", ""))):
        errors.append("convergence: source_main must be a full Git object id")

    remotes = unique(convergence.get("remotes"), "id", "convergence.remotes", errors)
    if {record.get("id") for record in remotes} != EXPECTED_REMOTES:
        errors.append("convergence: remote snapshot is incomplete")
    canonical = next((record for record in remotes if record.get("id") == "canonical-origin"), None)
    if not canonical or canonical.get("fetch") != canonical.get("push"):
        errors.append("convergence: canonical fetch/push remote parity is not recorded")

    branches = unique(convergence.get("branches"), "id", "convergence.branches", errors)
    branch_ids = {record.get("id") for record in branches}
    branch_by_id = {record.get("id"): record for record in branches}
    if branch_ids != EXPECTED_BRANCHES:
        errors.append("convergence: branch snapshot is incomplete")
    for record in branches:
        if not GIT_SHA.fullmatch(str(record.get("head", ""))):
            errors.append(f"convergence.branches[{record.get('id')}]: head must be a full Git object id")
        if not str(record.get("receipt", "")).strip():
            errors.append(f"convergence.branches[{record.get('id')}]: missing receipt")

    worktrees = unique(convergence.get("worktrees"), "id", "convergence.worktrees", errors)
    if {record.get("id") for record in worktrees} != EXPECTED_WORKTREES:
        errors.append("convergence: worktree snapshot is incomplete")
    for record in worktrees:
        branch = record.get("branch")
        if branch is not None and branch not in branch_ids:
            errors.append(f"convergence.worktrees[{record.get('id')}]: unknown branch {branch!r}")
        elif branch is not None and record.get("head") != branch_by_id[branch].get("head"):
            errors.append(
                f"convergence.worktrees[{record.get('id')}]: head disagrees with branch {branch!r}"
            )
        if not GIT_SHA.fullmatch(str(record.get("head", ""))):
            errors.append(f"convergence.worktrees[{record.get('id')}]: head must be a full Git object id")
        if record.get("cleanup_authorized") is not False:
            errors.append(
                f"convergence.worktrees[{record.get('id')}]: snapshot does not authorize cleanup"
            )

    stashes = convergence.get("stashes") or {}
    if set(stashes) != {"canonical_repository", "related_limen_repository"}:
        errors.append("convergence: stash namespace snapshot is incomplete")
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
    if {record.get("id") for record in outcomes} != EXPECTED_AGENT_OUTCOMES:
        errors.append("convergence: agent outcome snapshot is incomplete")
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

    copy_floor = 2
    custody_roots = unique(custody.get("roots"), "id", "custody.roots", errors)
    if {record.get("id") for record in custody_roots} != EXPECTED_CUSTODY_ROOTS:
        errors.append("custody: protected root snapshot is incomplete")
    worktree_by_id = {record.get("id"): record for record in worktrees}
    for record in custody_roots:
        copies = record.get("independent_verified_copies")
        if not isinstance(copies, list):
            errors.append(f"custody.roots[{record.get('id')}]: copies must be a list")
            copies = []
        media: set[str] = set()
        manifest_digests: set[str] = set()
        for copy in copies:
            if not isinstance(copy, dict):
                errors.append(f"custody.roots[{record.get('id')}]: copy receipt must be an object")
                continue
            medium = copy.get("medium_id")
            digest = copy.get("manifest_sha256")
            medium_ok = isinstance(medium, str) and bool(medium.strip())
            normalized_medium = medium.strip() if medium_ok else None
            digest_ok = isinstance(digest, str) and bool(SHA256.fullmatch(digest))
            verified = copy.get("verified") is True
            if not medium_ok or normalized_medium in media:
                errors.append(f"custody.roots[{record.get('id')}]: copy media must be independent")
            if not verified or not digest_ok:
                errors.append(f"custody.roots[{record.get('id')}]: invalid checksum copy receipt")
            if medium_ok and verified and digest_ok:
                media.add(normalized_medium)
                manifest_digests.add(digest)

        if len(manifest_digests) > 1:
            errors.append(f"custody.roots[{record.get('id')}]: checksum copies preserve different manifests")

        restore_value = record.get("restore_rehearsal")
        acceptance_value = record.get("human_acceptance")
        if not isinstance(restore_value, dict):
            errors.append(f"custody.roots[{record.get('id')}]: restore_rehearsal must be an object")
        if not isinstance(acceptance_value, dict):
            errors.append(f"custody.roots[{record.get('id')}]: human_acceptance must be an object")
        restore = restore_value if isinstance(restore_value, dict) else {}
        acceptance = acceptance_value if isinstance(acceptance_value, dict) else {}
        restore_ok = restore.get("ok") is True
        human_ok = acceptance.get("ok") is True
        restore_receipt = restore.get("receipt")
        acceptance_receipt = acceptance.get("receipt")
        restore_receipt_ok = durable_receipt(restore_receipt, root)
        acceptance_receipt_ok = durable_receipt(acceptance_receipt, root)
        if restore_ok and not restore_receipt_ok:
            errors.append(f"custody.roots[{record.get('id')}]: clean restore lacks a durable receipt")
        if human_ok and not acceptance_receipt_ok:
            errors.append(f"custody.roots[{record.get('id')}]: owner acceptance lacks a durable receipt")
        cleanup_authorized = record.get("cleanup_authorized")
        if not isinstance(cleanup_authorized, bool):
            errors.append(f"custody.roots[{record.get('id')}]: cleanup_authorized must be an explicit boolean")
        linked = worktree_by_id.get(record.get("id"))
        eligible = (
            len(media) >= copy_floor
            and len(manifest_digests) == 1
            and restore_ok
            and restore_receipt_ok
            and human_ok
            and acceptance_receipt_ok
            and record.get("tracked_tree_clean") is True
            and linked is not None
            and linked.get("tracked_clean") is True
        )
        if cleanup_authorized is True and not eligible:
            errors.append(f"custody.roots[{record.get('id')}]: cleanup bypasses copy/restore/acceptance gates")
        if cleanup_authorized is False and record.get("status") != "blocked":
            errors.append(f"custody.roots[{record.get('id')}]: retained material must be classified blocked")
        if not linked or linked.get("cleanup_authorized") is not False:
            errors.append(f"custody.roots[{record.get('id')}]: no fail-closed worktree cross-reference")

    source = archive.get("source") or {}
    if source != EXPECTED_ARCHIVE_SOURCE:
        errors.append(
            "archive: source repository, branch, commit, and wholesale merge prohibition must remain exact"
        )
    artifacts = unique(archive.get("artifacts"), "id", "archive.artifacts", errors)
    if {record.get("id") for record in artifacts} != set(EXPECTED_ARCHIVE_ARTIFACTS):
        errors.append("archive: disposition matrix is incomplete")
    for record in artifacts:
        paths = record.get("paths")
        if not isinstance(paths, list) or any(Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
            errors.append(f"archive.artifacts[{record.get('id')}]: paths must be safe relative paths")
        if not str(record.get("disposition", "")).strip():
            errors.append(f"archive.artifacts[{record.get('id')}]: missing disposition")
        expected = EXPECTED_ARCHIVE_ARTIFACTS.get(record.get("id"))
        if expected is not None and any(record.get(key) != value for key, value in expected.items()):
            errors.append(f"archive.artifacts[{record.get('id')}]: disposition decision drifted")
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
    return validate_documents(convergence, custody, archive, closeout, root)


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

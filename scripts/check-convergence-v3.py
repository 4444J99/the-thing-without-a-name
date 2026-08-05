#!/usr/bin/env python3
"""Validate the additive Danse convergence, archive, and custody v3 receipts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_ROOT = Path("docs/continuations/alpha-omega")
ARCHIVE = RECEIPT_ROOT / "archive-dispositions-20260804-v3.json"

EXPECTED_DOCUMENT_DIGESTS = {
    "archive": "3e786ee4e578ee740addc9626a3caf2fc606efc25ac4ad920f09e7443d69f351",
}
EXPECTED_SUPERSEDED = {
    "archive": (
        "docs/continuations/alpha-omega/archive-dispositions-20260804-v2.json",
        "a416ee3bc47c7797466ab12f8c82826f6a93c8f46b5f81a8cc4ff4dbcf26a450",
    ),
}
EXPECTED_ARCHIVE_ARTIFACTS = {
    "visitor-pose-prototype": "ported",
    "pose-engine-cast-diagnostic": "superseded",
    "archive-hygiene-gitignore": "superseded",
    "stateful-spatial-score-prototype": "ported",
    "browser-audio-harness": "archived",
    "cloudflare-deployment-experiment": "superseded",
    "theoretical-framework": "archived",
    "installation-proposal": "ported",
    "submission-attestation": "superseded",
    "private-brainstorm-history": "not recorded",
}


def load_v2_checker():
    spec = importlib.util.spec_from_file_location(
        "danse_convergence_v2", ROOT / "scripts" / "check-convergence-v2.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("v2 convergence checker could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V2 = load_v2_checker()
V1 = V2.V1
STATUSES = V1.STATUSES
GIT_SHA = V1.GIT_SHA
SHA256 = V1.SHA256


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def unique(records: object, key: str, label: str, errors: list[str]) -> list[dict[str, Any]]:
    return V2.unique(records, key, label, errors)


def validate_archive(archive: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if V1.canonical_digest(archive) != EXPECTED_DOCUMENT_DIGESTS["archive"]:
        errors.append("archive: immutable v3 receipt content digest drifted")
    if archive.get("schema") != "danse.archive-dispositions.v3":
        errors.append("archive: expected schema danse.archive-dispositions.v3")
    if archive.get("recorded_at") != "2026-08-05T00:00:00Z":
        errors.append("archive: observation timestamp drifted")
    V1.validate_statuses(archive, "archive", errors)
    V1.validate_no_private_paths(archive, "archive", errors)
    supersedes = archive.get("supersedes")
    expected_path, expected_digest = EXPECTED_SUPERSEDED["archive"]
    if not isinstance(supersedes, dict):
        errors.append("archive: missing immutable v2 predecessor binding")
    elif supersedes.get("path") != expected_path or supersedes.get("canonical_json_sha256") != expected_digest:
        errors.append("archive: v2 predecessor path or digest drifted")

    source = archive.get("source") or {}
    if source != {
        "repository": "organvm/limen",
        "branch": "archive/danse-predecessor-experiments-20260802",
        "commit": "a232f2d7160e213802580e2d532a0d2d9ac65727",
        "remote_parity": True,
        "merge_wholesale": False,
    }:
        errors.append("archive: source identity or wholesale-merge prohibition drifted")

    if not str(archive.get("coverage_note", "")).strip():
        errors.append("archive: exhaustive source-coverage note is missing")

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
    cast_diagnostic = next((item for item in artifacts if item.get("id") == "pose-engine-cast-diagnostic"), None)
    if not cast_diagnostic or "apps/danse/engine/engine.js" not in (cast_diagnostic.get("paths") or []):
        errors.append("archive: engine.js cast diagnostic is not explicitly dispositioned")
    hygiene = next((item for item in artifacts if item.get("id") == "archive-hygiene-gitignore"), None)
    if not hygiene or "apps/danse/.gitignore" not in (hygiene.get("paths") or []):
        errors.append("archive: .gitignore hygiene coverage is not explicitly recorded")
    brainstorm = next((item for item in artifacts if item.get("id") == "private-brainstorm-history"), None)
    if not brainstorm or "cannot be reconstructed" not in str(brainstorm.get("disposition", "")):
        errors.append("archive: private brainstorm absence is not honestly recorded")
    if (archive.get("issue_state") or {}).get("status") != "blocked":
        errors.append("archive: issue #3 must retain the inherited real-room blocker")

    return errors


def audit(root: Path = ROOT) -> list[str]:
    errors = [f"v2: {error}" for error in V2.audit(root)]
    try:
        archive = load(root / ARCHIVE)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [*errors, str(exc)]
    return [*errors, *validate_archive(archive)]


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
        print("danse convergence v3: exhaustive disposition matrix valid; protected custody remains fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

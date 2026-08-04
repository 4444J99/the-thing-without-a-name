#!/usr/bin/env python3
"""Validate the versioned Danse release manifest at a named publication phase."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from release_contract import MANIFEST, PHASES, ROOT, ReleaseError, phase_blockers, validate_release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--phase", choices=PHASES, default="draft")
    parser.add_argument(
        "--list-gates",
        action="store_true",
        help="after draft validation, list the still-open public and release predicates",
    )
    args = parser.parse_args()
    try:
        manifest = validate_release(args.root, manifest_path=args.manifest, phase=args.phase)
    except ReleaseError as exc:
        parser.exit(1, f"release manifest: FAIL - {exc}\n")

    public = phase_blockers(manifest, "public")
    release = phase_blockers(manifest, "release")
    print(
        f"release manifest: {manifest['release_id']} {manifest['version']} "
        f"verified for {args.phase}; public blockers={len(public)}, release blockers={len(release)}"
    )
    if args.list_gates:
        for blocker in public:
            print(f"  public: {blocker}")
        for blocker in release:
            if blocker not in public:
                print(f"  release-only: {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

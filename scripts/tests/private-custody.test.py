#!/usr/bin/env python3
"""Portable, adversarial tests for private custody snapshots and restores."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "danse_private_custody_test",
        ROOT / "scripts/private_custody.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("private custody module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CUSTODY = load_module()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class CustodyFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.source = self.root / "source"
        self.primary = self.root / "primary"
        self.secondary = self.root / "secondary"
        self.restore_parent = self.root / "restore"
        for path in (self.primary, self.secondary, self.restore_parent):
            path.mkdir()
        subprocess.run(["git", "init", "--quiet", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "init", "--quiet", str(self.source)], check=True)
        git(self.source, "config", "user.name", "Danse Custody Test")
        git(self.source, "config", "user.email", "custody@example.invalid")
        git(self.source, "branch", "-M", "main")
        (self.source / ".gitignore").write_text(".work/\n", encoding="utf-8")
        (self.source / "README.md").write_text("tracked source\n", encoding="utf-8")
        git(self.source, "add", ".gitignore", "README.md")
        git(self.source, "commit", "--quiet", "-m", "fixture")
        git(self.source, "remote", "add", "origin", str(self.remote))
        git(self.source, "push", "--quiet", "-u", "origin", "main")
        material = self.source / ".work/nested/payload.bin"
        material.parent.mkdir(parents=True)
        material.write_bytes((b"danse-custody\0" * 1024) + bytes(range(128)))
        os.chmod(material, 0o640)

    def close(self) -> None:
        self.temporary.cleanup()

    def snapshot(self, snapshot_id: str = "fixture-snapshot") -> tuple[Path, Path]:
        primary = CUSTODY.create_snapshot(
            self.source,
            self.primary,
            snapshot_id,
            "origin/main",
            "equal",
        )
        secondary = CUSTODY.copy_snapshot(primary, self.secondary)
        return primary, secondary


class PrivateCustodyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CustodyFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_snapshot_copy_and_clean_restore_are_byte_exact(self) -> None:
        primary, secondary = self.fixture.snapshot()
        restore = self.fixture.restore_parent / "clean"
        with mock.patch.object(
            CUSTODY,
            "_physical_device_token",
            side_effect=("a" * 64, "b" * 64),
        ):
            receipt = CUSTODY.redacted_receipt(
                primary,
                secondary,
                restore,
                "archive-medium",
                "recovery-medium",
            )
        self.assertEqual(
            (restore / ".work/nested/payload.bin").read_bytes(),
            (self.fixture.source / ".work/nested/payload.bin").read_bytes(),
        )
        self.assertEqual(git(restore, "rev-parse", "HEAD"), git(self.fixture.source, "rev-parse", "HEAD"))
        self.assertEqual(receipt["inventory"]["entries"], 1)
        self.assertTrue(receipt["restore_rehearsal"]["ok"])
        self.assertFalse(receipt["human_acceptance"]["ok"])
        rendered = json.dumps(receipt)
        self.assertNotIn("payload.bin", rendered)
        self.assertNotIn(str(self.fixture.root), rendered)

    def test_dirty_tracked_source_fails_before_destination_bytes_are_created(self) -> None:
        (self.fixture.source / "README.md").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(CUSTODY.CustodyError, "tracked modifications"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "dirty-source",
                "origin/main",
                "equal",
            )
        self.assertEqual(list(self.fixture.primary.iterdir()), [])

    def test_remote_equality_cannot_be_replaced_by_ancestor_mode_accidentally(self) -> None:
        (self.fixture.source / "next.txt").write_text("next\n", encoding="utf-8")
        git(self.fixture.source, "add", "next.txt")
        git(self.fixture.source, "commit", "--quiet", "-m", "unpushed")
        with self.assertRaisesRegex(CUSTODY.CustodyError, "not equal"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "remote-drift",
                "origin/main",
                "equal",
            )
        self.assertEqual(list(self.fixture.primary.iterdir()), [])

    def test_escaping_material_symlink_fails_before_snapshot_creation(self) -> None:
        payload = self.fixture.source / ".work/nested/payload.bin"
        payload.unlink()
        payload.symlink_to("../../../../outside")
        with self.assertRaisesRegex(CUSTODY.CustodyError, "symlink escapes"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "escaping-link",
                "origin/main",
                "equal",
            )
        self.assertEqual(list(self.fixture.primary.iterdir()), [])

    def test_same_physical_device_cannot_count_twice(self) -> None:
        with self.assertRaisesRegex(CUSTODY.CustodyError, "independent physical devices"):
            CUSTODY.ensure_independent(self.fixture.primary, self.fixture.secondary)

    def test_tampered_copy_fails_before_restore(self) -> None:
        _, secondary = self.fixture.snapshot()
        with (secondary / "materials.tar").open("ab") as handle:
            handle.write(b"tamper")
        target = self.fixture.restore_parent / "blocked"
        with self.assertRaisesRegex(CUSTODY.CustodyError, "byte count changed"):
            CUSTODY.restore_snapshot(secondary, target)
        self.assertFalse(target.exists())

    def test_unsafe_remote_reference_is_rejected_without_git_option_injection(self) -> None:
        for value in ("--help", "origin/../main", "upstream/main", "origin/main.lock"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(CUSTODY.CustodyError, "safe origin branch"):
                    CUSTODY.create_snapshot(
                        self.fixture.source,
                        self.fixture.primary,
                        "unsafe-ref",
                        value,
                        "equal",
                    )
                self.assertEqual(list(self.fixture.primary.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

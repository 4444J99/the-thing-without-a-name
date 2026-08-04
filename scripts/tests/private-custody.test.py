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
        receipt_path = self.fixture.root / "receipt.json"
        with mock.patch.object(
            CUSTODY,
            "_medium_identity",
            side_effect=(
                CUSTODY.MediumIdentity("a" * 64, "physical-a"),
                CUSTODY.MediumIdentity("b" * 64, "physical-b"),
            ),
        ):
            receipt = CUSTODY.redacted_receipt(
                self.fixture.source,
                primary,
                secondary,
                restore,
                receipt_path,
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
        self.assertNotIn("fixture-snapshot", rendered)
        self.assertNotIn("origin/main", rendered)
        self.assertNotIn("archive-medium", rendered)
        self.assertNotIn("recovery-medium", rendered)

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

    def test_hidden_tracked_edits_cannot_be_omitted_by_index_flags(self) -> None:
        git(self.fixture.source, "update-index", "--assume-unchanged", "README.md")
        (self.fixture.source / "README.md").write_text("private hidden tracked edit\n", encoding="utf-8")
        with self.assertRaisesRegex(CUSTODY.CustodyError, "hidden index flags"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "hidden-tracked-edit",
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

    def test_every_fetch_and_push_url_must_have_exact_set_parity(self) -> None:
        mirror = self.fixture.root / "mirror.git"
        exfiltration = self.fixture.root / "unapproved.git"
        git(self.fixture.source, "config", "--add", "remote.origin.url", str(mirror))
        git(self.fixture.source, "config", "--add", "remote.origin.pushurl", str(mirror))
        git(
            self.fixture.source,
            "config",
            "--add",
            "remote.origin.pushurl",
            str(self.fixture.remote),
        )
        identity = CUSTODY._repository_identity(self.fixture.source, "origin/main", "equal")
        self.assertTrue(identity["remote_fetch_push_parity"])

        git(self.fixture.source, "config", "--add", "remote.origin.pushurl", str(exfiltration))
        with self.assertRaisesRegex(CUSTODY.CustodyError, "fetch/push parity"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "extra-push-url",
                "origin/main",
                "equal",
            )
        self.assertEqual(list(self.fixture.primary.iterdir()), [])

    def test_tag_cannot_impersonate_a_missing_remote_tracking_branch(self) -> None:
        head = git(self.fixture.source, "rev-parse", "HEAD")
        git(self.fixture.source, "tag", "--no-sign", "origin/main", head)
        git(self.fixture.source, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(git(self.fixture.source, "rev-parse", "origin/main"), head)

        with self.assertRaisesRegex(CUSTODY.CustodyError, "remote tracking reference"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "tag-is-not-a-remote",
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

    def test_windows_drive_and_unc_symlink_targets_are_not_portable(self) -> None:
        for target in (r"C:\private\outside", r"\\server\share\outside", r"folder\outside"):
            with self.subTest(target=target), self.assertRaisesRegex(
                CUSTODY.CustodyError, "symlink escapes"
            ):
                CUSTODY._safe_symlink_target(target)

    def test_late_private_file_invalidates_snapshot_before_sealing(self) -> None:
        original = CUSTODY._tar_materials

        def mutate_after_archive(source, entries, output, total):
            original(source, entries, output, total)
            (source / ".work/late.bin").write_bytes(b"late")

        with mock.patch.object(
            CUSTODY, "_tar_materials", side_effect=mutate_after_archive
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "private census changed"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "late-material",
                "origin/main",
                "equal",
            )
        self.assertFalse((self.fixture.primary / "late-material").exists())
        self.assertTrue((self.fixture.primary / ".late-material.incomplete").is_dir())

    def test_earlier_material_mutation_during_later_hash_blocks_receipt(self) -> None:
        earlier = self.fixture.source / ".work/aaa.bin"
        earlier.write_bytes(b"a" * 4096)
        primary, secondary = self.fixture.snapshot()
        original = CUSTODY._sha256
        mutated = False

        def mutate_earlier_after_later(path, progress=None):
            nonlocal mutated
            digest = original(path, progress)
            if path.name == "payload.bin" and not mutated:
                earlier.write_bytes(b"b" * 4096)
                mutated = True
            return digest

        target = self.fixture.restore_parent / "blocked-mid-census-mutation"
        with mock.patch.object(
            CUSTODY,
            "_medium_identity",
            side_effect=(
                CUSTODY.MediumIdentity("a" * 64, "physical-a"),
                CUSTODY.MediumIdentity("b" * 64, "physical-b"),
            ),
        ), mock.patch.object(
            CUSTODY, "_sha256", side_effect=mutate_earlier_after_later
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "after its completed scan"):
            CUSTODY.redacted_receipt(
                self.fixture.source,
                primary,
                secondary,
                target,
                self.fixture.root / "blocked-mid-census-mutation.json",
                "archive-medium",
                "recovery-medium",
            )
        self.assertTrue(mutated)
        self.assertFalse(target.exists())

    def test_source_mutation_during_artifact_hashing_blocks_publication(self) -> None:
        material = self.fixture.source / ".work/nested/payload.bin"
        original_payload = material.read_bytes()
        original = CUSTODY._artifact
        mutated = False

        def mutate_after_artifact(path, label):
            nonlocal mutated
            artifact = original(path, label)
            if label == "source bundle" and not mutated:
                material.write_bytes(b"x" * len(original_payload))
                mutated = True
            return artifact

        with mock.patch.object(
            CUSTODY, "_artifact", side_effect=mutate_after_artifact
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "during artifact hashing"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "artifact-race",
                "origin/main",
                "equal",
            )
        self.assertTrue(mutated)
        self.assertFalse((self.fixture.primary / "artifact-race").exists())
        self.assertTrue((self.fixture.primary / ".artifact-race.incomplete").is_dir())

    def test_late_final_directories_cannot_be_replaced_on_create_or_copy(self) -> None:
        original = CUSTODY._publish_directory_exclusive

        def create_late_target(staging, final):
            final.mkdir()
            original(staging, final)

        with mock.patch.object(
            CUSTODY,
            "_publish_directory_exclusive",
            side_effect=create_late_target,
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "appeared during publication"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "late-primary",
                "origin/main",
                "equal",
            )
        self.assertEqual(list((self.fixture.primary / "late-primary").iterdir()), [])
        self.assertTrue((self.fixture.primary / ".late-primary.incomplete").is_dir())

        primary = CUSTODY.create_snapshot(
            self.fixture.source,
            self.fixture.primary,
            "late-secondary",
            "origin/main",
            "equal",
        )
        with mock.patch.object(
            CUSTODY,
            "_publish_directory_exclusive",
            side_effect=create_late_target,
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "appeared during publication"):
            CUSTODY.copy_snapshot(primary, self.fixture.secondary)
        self.assertEqual(list((self.fixture.secondary / "late-secondary").iterdir()), [])
        self.assertTrue((self.fixture.secondary / ".late-secondary.incomplete").is_dir())

    def test_durable_publication_flushes_every_file_and_directory_boundary(self) -> None:
        parent = self.fixture.root / "durable-publication"
        staging = parent / ".snapshot.incomplete"
        final = parent / "snapshot"
        expected_control = {"control": "fixture"}
        staging.mkdir(parents=True)
        names = ("private-manifest.json", "materials.tar", "source.bundle", "control.json")
        for name in names:
            (staging / name).write_bytes(name.encode())

        events = []

        def sync_file(path):
            events.append(("file", path.name))

        def sync_directory(path):
            events.append(("directory", path.name))

        def verify(path):
            events.append(("verify", path.name))
            return expected_control

        def publish(source, destination):
            events.append(("publish", source.name, destination.name))
            os.rename(source, destination)

        with mock.patch.object(CUSTODY, "_fsync_file", side_effect=sync_file), mock.patch.object(
            CUSTODY, "_fsync_directory", side_effect=sync_directory
        ), mock.patch.object(
            CUSTODY, "verify_snapshot", side_effect=verify
        ), mock.patch.object(
            CUSTODY, "_publish_directory_exclusive", side_effect=publish
        ):
            CUSTODY._durable_publish_directory(staging, final, expected_control)

        self.assertEqual(
            events,
            [
                *(("file", name) for name in sorted(names)),
                ("directory", ".snapshot.incomplete"),
                ("directory", "durable-publication"),
                ("verify", ".snapshot.incomplete"),
                ("publish", ".snapshot.incomplete", "snapshot"),
                ("directory", "snapshot"),
                ("directory", "durable-publication"),
            ],
        )

        probe = self.fixture.root / "fsync-probe.bin"
        probe.write_bytes(b"durable")
        real_fsync = os.fsync
        fullsync_api = mock.Mock()
        with mock.patch.object(CUSTODY.sys, "platform", "darwin"), mock.patch.object(
            CUSTODY.os, "fsync", wraps=real_fsync
        ) as fsync, mock.patch.object(CUSTODY, "fcntl", fullsync_api):
            CUSTODY._fsync_file(probe)
            CUSTODY._fsync_directory(self.fixture.root)
        fsync.assert_called_once()
        self.assertEqual(fullsync_api.fcntl.call_count, 1)
        self.assertEqual(fullsync_api.fcntl.call_args.args[1], CUSTODY.F_FULLFSYNC)

        blocked_staging = parent / ".blocked.incomplete"
        blocked_final = parent / "blocked"
        blocked_staging.mkdir()
        (blocked_staging / "control.json").write_bytes(b"control")
        with mock.patch.object(
            CUSTODY,
            "_fsync_file",
            side_effect=CUSTODY.CustodyError("injected fsync failure"),
        ), mock.patch.object(CUSTODY, "_publish_directory_exclusive") as publish:
            with self.assertRaisesRegex(CUSTODY.CustodyError, "injected fsync failure"):
                CUSTODY._durable_publish_directory(
                    blocked_staging,
                    blocked_final,
                    expected_control,
                )
        publish.assert_not_called()
        self.assertTrue(blocked_staging.is_dir())
        self.assertFalse(blocked_final.exists())

        fullsync_staging = parent / ".fullsync-blocked.incomplete"
        fullsync_final = parent / "fullsync-blocked"
        fullsync_staging.mkdir()
        (fullsync_staging / "control.json").write_bytes(b"control")
        failing_fullsync = mock.Mock()
        failing_fullsync.fcntl.side_effect = OSError("injected fullfsync failure")
        with mock.patch.object(CUSTODY.sys, "platform", "darwin"), mock.patch.object(
            CUSTODY, "fcntl", failing_fullsync
        ), mock.patch.object(CUSTODY, "_publish_directory_exclusive") as publish:
            with self.assertRaisesRegex(CUSTODY.CustodyError, "durably synchronized"):
                CUSTODY._durable_publish_directory(
                    fullsync_staging,
                    fullsync_final,
                    expected_control,
                )
        publish.assert_not_called()
        self.assertTrue(fullsync_staging.is_dir())
        self.assertFalse(fullsync_final.exists())

    def test_corrupted_secondary_is_verified_while_still_incomplete(self) -> None:
        primary = CUSTODY.create_snapshot(
            self.fixture.source,
            self.fixture.primary,
            "corrupted-secondary",
            "origin/main",
            "equal",
        )
        original = CUSTODY._copy_file

        def corrupt_copy(source, destination, label):
            original(source, destination, label)
            if destination.name == "materials.tar":
                with destination.open("ab") as handle:
                    handle.write(b"corrupt-after-copy")

        with mock.patch.object(
            CUSTODY, "_copy_file", side_effect=corrupt_copy
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "byte count changed"):
            CUSTODY.copy_snapshot(primary, self.fixture.secondary)
        self.assertFalse((self.fixture.secondary / "corrupted-secondary").exists())
        self.assertTrue(
            (self.fixture.secondary / ".corrupted-secondary.incomplete").is_dir()
        )

    def test_source_bundle_is_included_in_capacity_before_staging(self) -> None:
        inventory = CUSTODY._material_inventory(self.fixture.source)
        head = git(self.fixture.source, "rev-parse", "HEAD")
        source_bundle_budget = CUSTODY._reachable_git_bytes(self.fixture.source, head)
        legacy_required = inventory.total + max(1 << 30, inventory.total // 20)
        snapshot_payload = inventory.total + source_bundle_budget
        required = snapshot_payload + max(1 << 30, snapshot_payload // 20)
        self.assertGreater(required, legacy_required + 1)

        with mock.patch.object(
            CUSTODY.shutil,
            "disk_usage",
            return_value=mock.Mock(free=legacy_required + 1),
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "insufficient free space"):
            CUSTODY.create_snapshot(
                self.fixture.source,
                self.fixture.primary,
                "bundle-capacity",
                "origin/main",
                "equal",
            )
        self.assertFalse((self.fixture.primary / "bundle-capacity").exists())
        self.assertFalse((self.fixture.primary / ".bundle-capacity.incomplete").exists())

    def test_same_physical_device_cannot_count_twice(self) -> None:
        same = CUSTODY.MediumIdentity("a" * 64, "one-physical-device")
        with mock.patch.object(
            CUSTODY, "_medium_identity", return_value=same
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "independent physical devices"):
            CUSTODY.ensure_independent(self.fixture.primary, self.fixture.secondary)

    def test_equal_opaque_medium_labels_cannot_form_two_copy_rows(self) -> None:
        primary, secondary = self.fixture.snapshot()
        with self.assertRaisesRegex(CUSTODY.CustodyError, "medium ids must be distinct"):
            CUSTODY.redacted_receipt(
                self.fixture.source,
                primary,
                secondary,
                self.fixture.restore_parent / "not-created",
                self.fixture.root / "not-created.json",
                "same-medium",
                "same-medium",
            )

    def test_tampered_copy_fails_before_restore(self) -> None:
        _, secondary = self.fixture.snapshot()
        with (secondary / "materials.tar").open("ab") as handle:
            handle.write(b"tamper")
        target = self.fixture.restore_parent / "blocked"
        with self.assertRaisesRegex(CUSTODY.CustodyError, "byte count changed"):
            CUSTODY.restore_snapshot(secondary, target)
        self.assertFalse(target.exists())

    def test_restore_capacity_includes_checkout_and_fails_before_target_creation(self) -> None:
        primary, _ = self.fixture.snapshot()
        control = CUSTODY.verify_snapshot(primary)
        expected_checkout = sum(
            (self.fixture.source / name).stat().st_size for name in (".gitignore", "README.md")
        )
        self.assertEqual(control["checkout_bytes"], expected_checkout)
        undercount = (
            control["inventory_bytes"]
            + control["artifacts"]["source.bundle"]["bytes"]
            + (1 << 30)
        )
        self.assertLess(undercount, CUSTODY._restore_required_bytes(control))

        target = self.fixture.restore_parent / "insufficient-capacity"
        with mock.patch.object(
            CUSTODY.shutil,
            "disk_usage",
            return_value=mock.Mock(free=undercount),
        ), mock.patch.object(CUSTODY, "_run") as run, self.assertRaisesRegex(
            CUSTODY.CustodyError, "insufficient free space"
        ):
            CUSTODY.restore_snapshot(primary, target)
        run.assert_not_called()
        self.assertFalse(target.exists())

        unknown = self.fixture.restore_parent / "unknown-capacity"
        with mock.patch.object(
            CUSTODY.shutil,
            "disk_usage",
            side_effect=OSError("injected capacity failure"),
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "could not be determined"):
            CUSTODY.restore_snapshot(primary, unknown)
        self.assertFalse(unknown.exists())

    def test_restore_rejects_ignored_to_untracked_classification_drift(self) -> None:
        local_material = self.fixture.source / "local-only.bin"
        local_material.write_bytes(b"excluded only by local git metadata")
        (self.fixture.source / ".git/info/exclude").write_text(
            "local-only.bin\n",
            encoding="utf-8",
        )
        primary, secondary = self.fixture.snapshot()
        control = CUSTODY.verify_snapshot(primary)
        entries = CUSTODY._manifest_entries(primary, control)
        recorded = {entry["path"]: entry["ignored"] for entry in entries}
        self.assertTrue(recorded["local-only.bin"])

        target = self.fixture.restore_parent / "classification-drift"
        with self.assertRaisesRegex(CUSTODY.CustodyError, "classification"):
            CUSTODY.restore_snapshot(secondary, target)
        self.assertTrue(target.is_dir())
        ignored, untracked = CUSTODY._material_paths(target)
        self.assertNotIn("local-only.bin", ignored)
        self.assertIn("local-only.bin", untracked)

    def test_control_symlink_cannot_escape_the_snapshot(self) -> None:
        primary, _ = self.fixture.snapshot()
        control = primary / "control.json"
        external = self.fixture.root / "external-control.json"
        external.write_bytes(control.read_bytes())
        control.unlink()
        control.symlink_to(external)
        with self.assertRaisesRegex(CUSTODY.CustodyError, "control is missing, linked"):
            CUSTODY.verify_snapshot(primary)

    def test_restore_and_receipt_paths_cannot_mutate_snapshots_or_restored_census(self) -> None:
        primary, secondary = self.fixture.snapshot()
        cases = (
            (primary / "restored-tree", self.fixture.root / "receipt-a.json"),
            (self.fixture.restore_parent / "clean-b", primary / "receipt.json"),
            (self.fixture.restore_parent / "clean-c", self.fixture.restore_parent / "clean-c/receipt.json"),
        )
        for target, receipt_path in cases:
            with self.subTest(target=target.name, receipt=receipt_path.name):
                with self.assertRaisesRegex(CUSTODY.CustodyError, "disjoint|parent must be"):
                    CUSTODY.redacted_receipt(
                        self.fixture.source,
                        primary,
                        secondary,
                        target,
                        receipt_path,
                        "archive-medium",
                        "recovery-medium",
                    )
                self.assertFalse(target.exists())
        self.assertEqual(
            {item.name for item in primary.iterdir()},
            {"control.json", "private-manifest.json", "materials.tar", "source.bundle"},
        )

    def test_source_census_drift_blocks_receipt_and_restore(self) -> None:
        primary, secondary = self.fixture.snapshot()
        (self.fixture.source / ".work/late-after-snapshot.bin").write_bytes(b"late")
        target = self.fixture.restore_parent / "blocked-source-drift"
        with mock.patch.object(
            CUSTODY,
            "_medium_identity",
            side_effect=(
                CUSTODY.MediumIdentity("a" * 64, "physical-a"),
                CUSTODY.MediumIdentity("b" * 64, "physical-b"),
            ),
        ), self.assertRaisesRegex(CUSTODY.CustodyError, "retained private census"):
            CUSTODY.redacted_receipt(
                self.fixture.source,
                primary,
                secondary,
                target,
                self.fixture.root / "blocked-source-drift.json",
                "archive-medium",
                "recovery-medium",
            )
        self.assertFalse(target.exists())

    def test_receipt_parent_is_synced_before_success_is_reported(self) -> None:
        receipt = self.fixture.root / "durable-receipt.json"
        events = []
        original_write = CUSTODY._write_new

        def write(path, payload):
            events.append(("write", path))
            original_write(path, payload)

        def sync(path):
            events.append(("sync", path))

        def report(*args, **kwargs):
            events.append(("print", args[0], kwargs.get("file")))

        argv = [
            "restore",
            "--source",
            str(self.fixture.source),
            "--primary",
            str(self.fixture.primary / "unused"),
            "--secondary",
            str(self.fixture.secondary / "unused"),
            "--primary-id",
            "archive-medium",
            "--secondary-id",
            "recovery-medium",
            "--target",
            str(self.fixture.restore_parent / "unused"),
            "--receipt",
            str(receipt),
        ]
        with mock.patch.object(
            CUSTODY, "redacted_receipt", return_value={"receipt": "fixture"}
        ), mock.patch.object(CUSTODY, "_write_new", side_effect=write), mock.patch.object(
            CUSTODY, "_fsync_directory", side_effect=sync
        ), mock.patch("builtins.print", side_effect=report):
            self.assertEqual(CUSTODY.main(argv), 0)
        self.assertEqual(
            events,
            [
                ("write", receipt),
                ("sync", receipt.parent),
                ("print", "custody: wrote one redacted restore receipt", None),
            ],
        )

        blocked = self.fixture.root / "unsynced-receipt.json"
        blocked_argv = [*argv[:-1], str(blocked)]
        events.clear()
        with mock.patch.object(
            CUSTODY, "redacted_receipt", return_value={"receipt": "fixture"}
        ), mock.patch.object(
            CUSTODY,
            "_fsync_directory",
            side_effect=CUSTODY.CustodyError("injected receipt-parent fsync failure"),
        ), mock.patch("builtins.print", side_effect=report):
            self.assertEqual(CUSTODY.main(blocked_argv), 1)
        self.assertTrue(blocked.is_file())
        self.assertFalse(
            any(
                event[0] == "print"
                and event[1] == "custody: wrote one redacted restore receipt"
                for event in events
            )
        )

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

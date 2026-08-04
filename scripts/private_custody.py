#!/usr/bin/env python3
"""Create, duplicate, and restore-test private Danse custody snapshots.

The payload manifest is deliberately private: it contains relative filenames and
travels only with the encrypted/controlled custody copies.  The restore receipt
is redacted and safe to track.  This tool never removes a source, snapshot,
partial snapshot, or restored tree, and it refuses to overwrite any target.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl module.
    fcntl = None

SCHEMA = "danse.private-custody.snapshot.v1"
RECEIPT_SCHEMA = "danse.private-custody.restore-receipt.v1"
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
CHUNK = 8 << 20
PROGRESS_INTERVAL = 4 << 30
GIT = shutil.which("git")
DISKUTIL = shutil.which("diskutil")
F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", 51) if fcntl is not None else 51


class CustodyError(RuntimeError):
    """A custody precondition or byte-integrity check failed."""


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=text,
            check=False,
        )
    except OSError as exc:
        raise CustodyError(f"required command is unavailable: {argv[0]}") from exc
    if result.returncode != 0:
        raise CustodyError(f"{argv[0]} failed with exit {result.returncode}")
    return result


def _git(source: Path, *args: str, text: bool = True):
    if GIT is None:
        raise CustodyError("required command is unavailable: git")
    return _run([GIT, "-C", str(source), *args], text=text)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _sync_regular_descriptor(descriptor: int) -> None:
    """Flush one regular file through the strongest supported durability boundary."""
    try:
        if sys.platform == "darwin":
            if fcntl is None:
                raise CustodyError("macOS full-file synchronization is unavailable")
            fcntl.fcntl(descriptor, F_FULLFSYNC)
        else:
            os.fsync(descriptor)
    except OSError as exc:
        raise CustodyError("regular file could not be durably synchronized") from exc


def _write_new(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            _sync_regular_descriptor(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _load_json(path: Path, label: str) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise CustodyError(f"{label} contains duplicate JSON keys")
            value[key] = item
        return value

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CustodyError(f"{label} is missing or invalid") from exc
    if not isinstance(value, dict):
        raise CustodyError(f"{label} must be a JSON object")
    return value


@dataclass
class Progress:
    label: str
    total: int
    seen: int = 0
    next_report: int = PROGRESS_INTERVAL

    def add(self, amount: int) -> None:
        self.seen += amount
        if self.seen >= self.next_report:
            print(
                f"custody: {self.label} {self.seen >> 30} GiB / {max(1, self.total >> 30)} GiB",
                flush=True,
            )
            self.next_report += PROGRESS_INTERVAL


@dataclass(frozen=True)
class MediumIdentity:
    """A durable store digest plus an in-process physical-device boundary."""

    durable_sha256: str
    physical_device: str


@dataclass
class _MaterialProof:
    """One complete private census plus non-portable stability metadata."""

    entries: list[dict]
    total: int
    ignored: frozenset[str]
    untracked: frozenset[str]
    metadata: dict[str, tuple[object, ...]]


class _ProgressReader:
    def __init__(self, handle: BinaryIO, progress: Progress):
        self.handle = handle
        self.progress = progress
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        value = self.handle.read(size)
        self.digest.update(value)
        self.progress.add(len(value))
        return value

    def hexdigest(self) -> str:
        return self.digest.hexdigest()


def _sha256(path: Path, progress: Progress | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
            if progress is not None:
                progress.add(len(block))
    return digest.hexdigest()


def _safe_relative(value: object, label: str = "material path") -> str:
    if not isinstance(value, str):
        raise CustodyError(f"{label} is not a safe portable relative path")
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise CustodyError(f"{label} is not a safe portable relative path")
    return pure.as_posix()


def _safe_symlink_target(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CustodyError("a material symlink escapes the portable snapshot")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or any(part == ".." for part in posix.parts)
    ):
        raise CustodyError("a material symlink escapes the portable snapshot")
    return value


def _contained_path(root: Path, relative: str, label: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink() and current != root / relative:
            raise CustodyError(f"{label} traverses an intermediate symlink")
    try:
        current.parent.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise CustodyError(f"{label} escapes the source root") from exc
    return current


def _restore_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    current = root
    for part in parts[:-1]:
        current = current / part
        if _lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise CustodyError("restore path traverses a non-directory or symlink")
        else:
            current.mkdir(mode=0o700)
    return current / parts[-1]


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _fsync_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise CustodyError("staged snapshot contains a linked or non-regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise CustodyError("staged snapshot contains a non-regular file")
            _sync_regular_descriptor(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise CustodyError("staged snapshot file could not be durably synchronized") from exc


def _fsync_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise CustodyError("snapshot durability boundary is not a regular directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise CustodyError("snapshot directory could not be durably synchronized") from exc


def _publish_directory_exclusive(staging: Path, final: Path) -> None:
    """Atomically publish one directory without replacing a late destination."""
    if staging.is_symlink() or not staging.is_dir() or staging.parent != final.parent:
        raise CustodyError("snapshot publication boundary is invalid")
    libc = ctypes.CDLL(None, use_errno=True)
    old = os.fsencode(staging)
    new = os.fsencode(final)
    ctypes.set_errno(0)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise CustodyError("exclusive snapshot publication is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(old, new, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise CustodyError("exclusive snapshot publication is unavailable")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, old, -100, new, 0x00000001)  # AT_FDCWD, RENAME_NOREPLACE
    elif os.name == "nt":
        try:
            os.rename(staging, final)
        except FileExistsError as exc:
            raise CustodyError("snapshot target appeared during publication") from exc
        return
    else:
        raise CustodyError("exclusive snapshot publication is unavailable")
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise CustodyError("snapshot target appeared during publication")
    raise CustodyError(f"exclusive snapshot publication failed: {os.strerror(error)}")


def _durable_publish_directory(staging: Path, final: Path, expected_control: dict) -> None:
    """Flush and verify a flat snapshot before exclusive publication."""
    staged = sorted(staging.iterdir(), key=lambda item: item.name)
    for path in staged:
        _fsync_file(path)
    if sorted(staging.iterdir(), key=lambda item: item.name) != staged:
        raise CustodyError("staged snapshot changed during durability synchronization")
    _fsync_directory(staging)
    _fsync_directory(staging.parent)
    staged_control = verify_snapshot(staging)
    if _json_bytes(staged_control) != _json_bytes(expected_control):
        raise CustodyError("staged snapshot control differs from the admitted control")
    _publish_directory_exclusive(staging, final)
    _fsync_directory(final)
    _fsync_directory(final.parent)


def _canonical_candidate(path: Path) -> Path:
    try:
        if _lexists(path):
            return path.resolve(strict=True)
        return path.parent.resolve(strict=True) / path.name
    except OSError as exc:
        raise CustodyError("output boundary could not be resolved") from exc


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _require_disjoint(*paths: Path) -> None:
    resolved = [_canonical_candidate(path) for path in paths]
    for index, first in enumerate(resolved):
        if any(_paths_overlap(first, second) for second in resolved[index + 1 :]):
            raise CustodyError("source, snapshots, restore target, and receipt must be disjoint")


def _require_new_path(path: Path, label: str) -> None:
    if _lexists(path):
        raise CustodyError(f"{label} already exists")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise CustodyError(f"{label} parent must be an existing regular directory")


def _safe_remote_ref(value: str) -> str:
    if (
        not value.startswith("origin/")
        or value.startswith("-")
        or any(token in value for token in ("..", "@{", "//", "\\", " "))
        or value.endswith(("/", ".", ".lock"))
    ):
        raise CustodyError("remote reference must be a safe origin branch")
    if GIT is None:
        raise CustodyError("required command is unavailable: git")
    result = subprocess.run(
        [GIT, "check-ref-format", f"refs/remotes/{value}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise CustodyError("remote reference must be a safe origin branch")
    return value


def _material_paths(source: Path) -> tuple[set[str], set[str]]:
    ignored_raw = _git(
        source,
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        text=False,
    ).stdout
    untracked_raw = _git(
        source,
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        text=False,
    ).stdout

    def decode(raw: bytes, label: str) -> set[str]:
        values: set[str] = set()
        for item in raw.split(b"\0"):
            if not item:
                continue
            try:
                value = item.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise CustodyError(f"{label} contains a non-UTF-8 path") from exc
            values.add(_safe_relative(value))
        return values

    return decode(ignored_raw, "ignored inventory"), decode(untracked_raw, "untracked inventory")


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _material_metadata(
    source: Path,
) -> tuple[frozenset[str], frozenset[str], dict[str, tuple[object, ...]]]:
    ignored_set, untracked_set = _material_paths(source)
    ignored = frozenset(ignored_set)
    untracked = frozenset(untracked_set)
    metadata: dict[str, tuple[object, ...]] = {}
    for relative in sorted(ignored | untracked):
        path = _contained_path(source, relative, "material path")
        before = path.lstat()
        if stat.S_ISREG(before.st_mode):
            kind = "file"
            target = None
        elif stat.S_ISLNK(before.st_mode):
            kind = "symlink"
            target = _safe_symlink_target(os.readlink(path))
        else:
            raise CustodyError("the material inventory contains an unsupported special file")
        after = path.lstat()
        if _stat_identity(before) != _stat_identity(after):
            raise CustodyError("the private material metadata changed during a census")
        metadata[relative] = (
            kind,
            relative in ignored,
            _stat_identity(after),
            target,
        )
    final_ignored, final_untracked = _material_paths(source)
    if (frozenset(final_ignored), frozenset(final_untracked)) != (ignored, untracked):
        raise CustodyError("the private material census changed during a metadata pass")
    return ignored, untracked, metadata


def _scan_material_inventory(source: Path) -> _MaterialProof:
    ignored_set, untracked_set = _material_paths(source)
    ignored = frozenset(ignored_set)
    untracked = frozenset(untracked_set)
    paths = sorted(ignored | untracked)
    entries: list[dict] = []
    metadata: dict[str, tuple[object, ...]] = {}
    total = 0
    progress = Progress("hashing private inventory", sum((source / path).lstat().st_size for path in paths))
    for relative in paths:
        path = _contained_path(source, relative, "material path")
        before = path.lstat()
        mode = stat.S_IMODE(before.st_mode)
        if stat.S_ISREG(before.st_mode):
            digest = _sha256(path, progress)
            after = path.lstat()
            if _stat_identity(before) != _stat_identity(after):
                raise CustodyError("a material file changed while it was being hashed")
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "bytes": before.st_size,
                    "mode": mode,
                    "sha256": digest,
                    "ignored": relative in ignored,
                }
            )
            metadata[relative] = (
                "file",
                relative in ignored,
                _stat_identity(after),
                None,
            )
            total += before.st_size
        elif stat.S_ISLNK(before.st_mode):
            target = _safe_symlink_target(os.readlink(path))
            after = path.lstat()
            if _stat_identity(before) != _stat_identity(after):
                raise CustodyError("a material symlink changed while it was inventoried")
            entries.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "target": target,
                    "mode": mode,
                    "ignored": relative in ignored,
                }
            )
            metadata[relative] = (
                "symlink",
                relative in ignored,
                _stat_identity(after),
                target,
            )
        else:
            raise CustodyError("the material inventory contains an unsupported special file")
    final_ignored, final_untracked = _material_paths(source)
    if (frozenset(final_ignored), frozenset(final_untracked)) != (ignored, untracked):
        raise CustodyError("the private material census changed while it was being hashed")
    return _MaterialProof(entries, total, ignored, untracked, metadata)


def _assert_material_metadata(source: Path, proof: _MaterialProof, when: str) -> None:
    ignored, untracked, metadata = _material_metadata(source)
    if ignored != proof.ignored or untracked != proof.untracked or metadata != proof.metadata:
        raise CustodyError(f"the private material proof changed {when}")


def _verify_material_proof(source: Path, proof: _MaterialProof, when: str) -> None:
    _assert_material_metadata(source, proof, when)

    progress = Progress("verifying private inventory", proof.total)
    for entry in proof.entries:
        path = _contained_path(source, entry["path"], "material path")
        before = path.lstat()
        if entry["type"] == "file":
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size != entry["bytes"]
                or stat.S_IMODE(before.st_mode) != entry["mode"]
                or _sha256(path, progress) != entry["sha256"]
            ):
                raise CustodyError(f"the private material proof changed {when}")
        elif (
            not stat.S_ISLNK(before.st_mode)
            or stat.S_IMODE(before.st_mode) != entry["mode"]
            or os.readlink(path) != entry["target"]
        ):
            raise CustodyError(f"the private material proof changed {when}")
        after = path.lstat()
        if _stat_identity(before) != _stat_identity(after):
            raise CustodyError(f"the private material proof changed {when}")

    _assert_material_metadata(source, proof, when)


def _material_inventory(source: Path) -> _MaterialProof:
    proof = _scan_material_inventory(source)
    _verify_material_proof(source, proof, "after its completed scan")
    return proof


def _tracked_checkout_bytes(source: Path, head: str) -> int:
    """Return the immutable byte count of every blob checked out from one commit."""
    raw = _git(
        source,
        "ls-tree",
        "-r",
        "-l",
        "-z",
        "--full-tree",
        head,
        text=False,
    ).stdout
    total = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, _path = record.partition(b"\t")
        fields = metadata.split()
        if separator != b"\t" or len(fields) != 4 or fields[1] != b"blob":
            raise CustodyError("source tracked checkout inventory is malformed")
        try:
            size = int(fields[3])
        except ValueError as exc:
            raise CustodyError("source tracked checkout inventory is malformed") from exc
        if size < 0:
            raise CustodyError("source tracked checkout inventory is malformed")
        total += size
    return total


def _repository_identity(source: Path, remote_ref: str, remote_mode: str) -> dict:
    remote_ref = _safe_remote_ref(remote_ref)
    if source.is_symlink():
        raise CustodyError("source repository root must not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise CustodyError("source repository is missing") from exc
    top = Path(str(_git(resolved, "rev-parse", "--show-toplevel").stdout).strip()).resolve()
    if top != resolved:
        raise CustodyError("source must be the repository worktree root")
    status = str(_git(resolved, "status", "--porcelain=v1", "--untracked-files=no").stdout)
    if status:
        raise CustodyError("source has tracked modifications")
    flags = _git(resolved, "ls-files", "-v", "-z", text=False).stdout
    if any(not record.startswith(b"H ") for record in flags.split(b"\0") if record):
        raise CustodyError("source uses hidden index flags such as assume-unchanged or skip-worktree")
    shallow = str(_git(resolved, "rev-parse", "--is-shallow-repository").stdout).strip()
    if shallow != "false":
        raise CustodyError("source must contain complete Git history, not a shallow checkout")
    index = _git(resolved, "ls-files", "--stage", "-z", text=False).stdout
    if any(record.startswith(b"160000 ") for record in index.split(b"\0") if record):
        raise CustodyError("source contains a submodule that a single repository bundle cannot restore")
    head = str(_git(resolved, "rev-parse", "HEAD").stdout).strip()
    remote_head = str(_git(resolved, "rev-parse", "--verify", f"{remote_ref}^{{commit}}").stdout).strip()
    if not GIT_SHA.fullmatch(head) or not GIT_SHA.fullmatch(remote_head):
        raise CustodyError("source or remote reference did not resolve to a commit")
    if remote_mode == "equal":
        if head != remote_head:
            raise CustodyError("source head is not equal to the admitted remote reference")
    elif remote_mode == "ancestor":
        result = subprocess.run(
            [GIT, "-C", str(resolved), "merge-base", "--is-ancestor", head, remote_ref],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise CustodyError("source head is not reachable from the admitted remote reference")
    else:
        raise CustodyError("remote mode must be equal or ancestor")
    fetch_urls = frozenset(
        str(_git(resolved, "remote", "get-url", "--all", "origin").stdout).splitlines()
    )
    push_urls = frozenset(
        str(_git(resolved, "remote", "get-url", "--push", "--all", "origin").stdout).splitlines()
    )
    if not fetch_urls or "" in fetch_urls or fetch_urls != push_urls:
        raise CustodyError("origin fetch/push parity is not proven")
    branch_result = subprocess.run(
        [GIT, "-C", str(resolved), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    return {
        "head": head,
        "branch": branch,
        "checkout_bytes": _tracked_checkout_bytes(resolved, head),
        "tracked_clean": True,
        "remote_ref": remote_ref,
        "remote_head": remote_head,
        "remote_mode": remote_mode,
        "remote_fetch_push_parity": True,
    }


def _tar_materials(source: Path, entries: list[dict], output: Path, total: int) -> None:
    progress = Progress("archiving private inventory", total)
    with tarfile.open(output, "x", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            relative = entry["path"]
            source_path = _contained_path(source, relative, "material path")
            info = tarfile.TarInfo(relative)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = entry["mode"]
            info.mtime = int(source_path.lstat().st_mtime)
            if entry["type"] == "symlink":
                if os.readlink(source_path) != entry["target"]:
                    raise CustodyError("a material symlink changed before archival")
                info.type = tarfile.SYMTYPE
                info.linkname = entry["target"]
                archive.addfile(info)
                continue
            info.type = tarfile.REGTYPE
            info.size = entry["bytes"]
            before = source_path.lstat()
            with source_path.open("rb") as handle:
                descriptor = os.fstat(handle.fileno())
                if (descriptor.st_dev, descriptor.st_ino) != (before.st_dev, before.st_ino):
                    raise CustodyError("a material file changed before archival")
                reader = _ProgressReader(handle, progress)
                archive.addfile(info, reader)
                if reader.hexdigest() != entry["sha256"]:
                    raise CustodyError("a material file changed between inventory and archival")
            after = source_path.lstat()
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise CustodyError("a material file changed during archival")


def _artifact(path: Path, label: str) -> dict:
    size = path.stat().st_size
    return {
        "label": label,
        "bytes": size,
        "sha256": _sha256(path, Progress(f"verifying {label}", size)),
    }


def create_snapshot(
    source: Path,
    primary_root: Path,
    snapshot_id: str,
    remote_ref: str,
    remote_mode: str,
) -> Path:
    """Create one immutable snapshot directory without overwriting anything."""
    if not ID.fullmatch(snapshot_id):
        raise CustodyError("snapshot id must be a portable lowercase identifier")
    if primary_root.is_symlink() or not primary_root.is_dir():
        raise CustodyError("primary custody root must be an existing regular directory")
    final = primary_root / snapshot_id
    staging = primary_root / f".{snapshot_id}.incomplete"
    if _lexists(final) or _lexists(staging):
        raise CustodyError("primary snapshot target already exists")

    identity = _repository_identity(source, remote_ref, remote_mode)
    inventory = _material_inventory(source)
    entries = inventory.entries
    total = inventory.total
    required_free = total + max(1 << 30, total // 20)
    if shutil.disk_usage(primary_root).free <= required_free:
        raise CustodyError("primary custody target has insufficient free space")
    staging.mkdir(mode=0o700)
    manifest = {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "source": identity,
        "inventory": {
            "entries": len(entries),
            "bytes": total,
            "ignored_entries": sum(entry["ignored"] for entry in entries),
            "untracked_entries": sum(not entry["ignored"] for entry in entries),
            "materials": entries,
        },
    }
    manifest_path = staging / "private-manifest.json"
    _write_new(manifest_path, _json_bytes(manifest))

    materials_path = staging / "materials.tar"
    _tar_materials(source, entries, materials_path, total)
    bundle_path = staging / "source.bundle"
    _git(source, "bundle", "create", str(bundle_path), "HEAD")
    _git(source, "bundle", "verify", str(bundle_path))
    bundle_heads = str(_git(source, "bundle", "list-heads", str(bundle_path)).stdout).splitlines()
    if not any(line.split(maxsplit=1)[0] == identity["head"] for line in bundle_heads if line.strip()):
        raise CustodyError("source bundle does not advertise the admitted source head")
    final_identity = _repository_identity(source, remote_ref, remote_mode)
    final_inventory = _material_inventory(source)
    if final_identity != identity or final_inventory != inventory:
        raise CustodyError("source repository or private census changed during snapshot creation")

    artifacts = {
        "private-manifest.json": _artifact(manifest_path, "private manifest"),
        "materials.tar": _artifact(materials_path, "materials archive"),
        "source.bundle": _artifact(bundle_path, "source bundle"),
    }
    _verify_material_proof(source, final_inventory, "during artifact hashing")
    sealed_identity = _repository_identity(source, remote_ref, remote_mode)
    _assert_material_metadata(source, final_inventory, "during the final source identity check")
    if sealed_identity != identity:
        raise CustodyError("source repository changed during artifact hashing")
    control = {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "source_head": identity["head"],
        "checkout_bytes": identity["checkout_bytes"],
        "remote_ref": identity["remote_ref"],
        "remote_head": identity["remote_head"],
        "remote_mode": identity["remote_mode"],
        "tracked_clean": True,
        "inventory_entries": len(entries),
        "inventory_bytes": total,
        "artifacts": artifacts,
    }
    _write_new(staging / "control.json", _json_bytes(control))
    _durable_publish_directory(staging, final, control)
    print(
        f"custody: created {snapshot_id} ({len(entries)} private entries, {total} bytes)",
        flush=True,
    )
    return final


def _mount_point(path: Path) -> Path:
    try:
        current = path.resolve(strict=True)
        device = current.stat().st_dev
        while current.parent != current and current.parent.stat().st_dev == device:
            current = current.parent
    except OSError as exc:
        raise CustodyError("custody medium could not be resolved") from exc
    return current


def _diskutil_info(value: str | Path) -> dict:
    if sys.platform != "darwin" or DISKUTIL is None:
        raise CustodyError("physical-medium proof requires macOS diskutil")
    result = _run([DISKUTIL, "info", "-plist", str(value)], text=False)
    try:
        info = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise CustodyError("physical-medium metadata is invalid") from exc
    if not isinstance(info, dict):
        raise CustodyError("physical-medium metadata is invalid")
    return info


def _medium_identity(path: Path) -> MediumIdentity:
    mount = _mount_point(path)
    mount_info = _diskutil_info(mount)
    declared_mount = mount_info.get("MountPoint")
    if not isinstance(declared_mount, str) or Path(declared_mount).resolve() != mount:
        raise CustodyError("custody path does not bind one mounted filesystem")

    physical_stores = mount_info.get("APFSPhysicalStores")
    if isinstance(physical_stores, list):
        identifiers = [
            row.get("APFSPhysicalStore")
            for row in physical_stores
            if isinstance(row, dict) and isinstance(row.get("APFSPhysicalStore"), str)
        ]
        if len(identifiers) != 1:
            raise CustodyError("custody APFS volume does not bind exactly one physical store")
        store_info = _diskutil_info(identifiers[0])
    else:
        store_info = mount_info

    whole = store_info.get("ParentWholeDisk")
    if not isinstance(whole, str) or not whole:
        if store_info.get("WholeDisk") is True and isinstance(store_info.get("DeviceIdentifier"), str):
            whole = store_info["DeviceIdentifier"]
        else:
            raise CustodyError("custody filesystem has no physical whole-disk parent")
    whole_info = _diskutil_info(whole)
    if whole_info.get("VirtualOrPhysical") != "Physical" or whole_info.get("SystemImage") is not False:
        raise CustodyError("custody target is virtual, image-backed, or not provably physical")

    durable = store_info.get("DiskUUID") or mount_info.get("VolumeUUID")
    if not isinstance(durable, str) or not durable:
        raise CustodyError("custody physical store has no durable UUID")
    durable_sha256 = hashlib.sha256(f"darwin-store-v1:{durable}".encode()).hexdigest()
    return MediumIdentity(durable_sha256=durable_sha256, physical_device=whole)


def ensure_independent(primary_root: Path, secondary_root: Path) -> tuple[str, str]:
    if (
        primary_root.is_symlink()
        or secondary_root.is_symlink()
        or not primary_root.is_dir()
        or not secondary_root.is_dir()
    ):
        raise CustodyError("custody roots must be existing regular directories")
    primary = _medium_identity(primary_root)
    secondary = _medium_identity(secondary_root)
    if (
        primary.physical_device == secondary.physical_device
        or primary.durable_sha256 == secondary.durable_sha256
    ):
        raise CustodyError("custody targets do not resolve to independent physical devices")
    return primary.durable_sha256, secondary.durable_sha256


def verify_snapshot(snapshot: Path) -> dict:
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise CustodyError("snapshot is not a regular directory")
    control_path = snapshot / "control.json"
    if control_path.is_symlink() or not control_path.is_file():
        raise CustodyError("snapshot control is missing, linked, or not a regular file")
    control = _load_json(control_path, "snapshot control")
    expected_control_keys = {
        "schema",
        "snapshot_id",
        "source_head",
        "checkout_bytes",
        "remote_ref",
        "remote_head",
        "remote_mode",
        "tracked_clean",
        "inventory_entries",
        "inventory_bytes",
        "artifacts",
    }
    if set(control) != expected_control_keys:
        raise CustodyError("snapshot control has an unknown or incomplete shape")
    if control.get("schema") != SCHEMA or not ID.fullmatch(str(control.get("snapshot_id", ""))):
        raise CustodyError("snapshot control has the wrong schema or identity")
    if (
        not GIT_SHA.fullmatch(str(control.get("source_head", "")))
        or not GIT_SHA.fullmatch(str(control.get("remote_head", "")))
        or type(control.get("checkout_bytes")) is not int
        or control["checkout_bytes"] < 0
        or control.get("remote_mode") not in {"equal", "ancestor"}
        or control.get("tracked_clean") is not True
        or type(control.get("inventory_entries")) is not int
        or control["inventory_entries"] < 0
        or type(control.get("inventory_bytes")) is not int
        or control["inventory_bytes"] < 0
    ):
        raise CustodyError("snapshot control identity or inventory is malformed")
    _safe_remote_ref(control["remote_ref"])
    artifacts = control.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "private-manifest.json",
        "materials.tar",
        "source.bundle",
    }:
        raise CustodyError("snapshot control has an incomplete artifact inventory")
    expected_files = {"control.json", *artifacts}
    if {item.name for item in snapshot.iterdir()} != expected_files:
        raise CustodyError("snapshot directory contains an unexpected artifact")
    for name, record in artifacts.items():
        path = snapshot / name
        if path.is_symlink() or not path.is_file() or not isinstance(record, dict):
            raise CustodyError("snapshot artifact is missing or unsafe")
        expected = record.get("sha256")
        if set(record) != {"label", "bytes", "sha256"}:
            raise CustodyError("snapshot artifact record has an unknown shape")
        if (
            not isinstance(record.get("label"), str)
            or not record["label"]
            or type(record.get("bytes")) is not int
            or record["bytes"] < 0
            or not isinstance(expected, str)
            or not HEX64.fullmatch(expected)
        ):
            raise CustodyError("snapshot artifact has no valid digest")
        if record.get("bytes") != path.stat().st_size:
            raise CustodyError("snapshot artifact byte count changed")
        if _sha256(path, Progress(f"verifying copied {record.get('label', 'artifact')}", path.stat().st_size)) != expected:
            raise CustodyError("snapshot artifact digest changed")
    manifest = _load_json(snapshot / "private-manifest.json", "private manifest")
    if manifest.get("snapshot_id") != control["snapshot_id"]:
        raise CustodyError("private manifest identity disagrees with snapshot control")
    _manifest_entries(snapshot, control)
    return control


def _copy_file(source: Path, destination: Path, label: str) -> None:
    total = source.stat().st_size
    progress = Progress(label, total)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
        while True:
            block = reader.read(CHUNK)
            if not block:
                break
            writer.write(block)
            progress.add(len(block))
        writer.flush()
        _sync_regular_descriptor(writer.fileno())


def copy_snapshot(primary: Path, secondary_root: Path) -> Path:
    control = verify_snapshot(primary)
    snapshot_id = control["snapshot_id"]
    if secondary_root.is_symlink() or not secondary_root.is_dir():
        raise CustodyError("secondary custody root must be an existing regular directory")
    final = secondary_root / snapshot_id
    staging = secondary_root / f".{snapshot_id}.incomplete"
    if _lexists(final) or _lexists(staging):
        raise CustodyError("secondary snapshot target already exists")
    required = sum(record["bytes"] for record in control["artifacts"].values())
    if shutil.disk_usage(secondary_root).free <= required:
        raise CustodyError("secondary custody target has insufficient free space")
    staging.mkdir(mode=0o700)
    for name in ("private-manifest.json", "materials.tar", "source.bundle", "control.json"):
        _copy_file(primary / name, staging / name, f"copying {name}")
    _durable_publish_directory(staging, final, control)
    secondary_control = verify_snapshot(final)
    if _json_bytes(secondary_control) != _json_bytes(control):
        raise CustodyError("secondary snapshot control differs from primary")
    print(f"custody: duplicated and verified {snapshot_id}", flush=True)
    return final


def _manifest_entries(snapshot: Path, control: dict) -> list[dict]:
    manifest = _load_json(snapshot / "private-manifest.json", "private manifest")
    if set(manifest) != {"schema", "snapshot_id", "source", "inventory"}:
        raise CustodyError("private manifest has an unknown or incomplete shape")
    if manifest.get("schema") != SCHEMA or manifest.get("snapshot_id") != control["snapshot_id"]:
        raise CustodyError("private manifest identity is invalid")
    source = manifest.get("source")
    if not isinstance(source, dict) or set(source) != {
        "head",
        "branch",
        "checkout_bytes",
        "tracked_clean",
        "remote_ref",
        "remote_head",
        "remote_mode",
        "remote_fetch_push_parity",
    }:
        raise CustodyError("private manifest source identity is malformed")
    if (
        source["head"] != control["source_head"]
        or source["checkout_bytes"] != control["checkout_bytes"]
        or source["remote_ref"] != control["remote_ref"]
        or source["remote_head"] != control["remote_head"]
        or source["remote_mode"] != control["remote_mode"]
        or source["tracked_clean"] is not True
        or source["remote_fetch_push_parity"] is not True
        or (source["branch"] is not None and not isinstance(source["branch"], str))
    ):
        raise CustodyError("private manifest source identity disagrees with snapshot control")
    inventory = manifest.get("inventory")
    if (
        not isinstance(inventory, dict)
        or set(inventory) != {
            "entries",
            "bytes",
            "ignored_entries",
            "untracked_entries",
            "materials",
        }
        or not isinstance(inventory.get("materials"), list)
    ):
        raise CustodyError("private manifest inventory is malformed")
    entries = inventory["materials"]
    if (
        type(inventory.get("entries")) is not int
        or inventory["entries"] != control["inventory_entries"]
        or type(inventory.get("bytes")) is not int
        or inventory["bytes"] != control["inventory_bytes"]
        or type(inventory.get("ignored_entries")) is not int
        or type(inventory.get("untracked_entries")) is not int
        or inventory["ignored_entries"] + inventory["untracked_entries"] != len(entries)
        or len(entries) != control["inventory_entries"]
    ):
        raise CustodyError("private manifest entry count disagrees with snapshot control")
    seen: set[str] = set()
    observed_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") not in {"file", "symlink"}:
            raise CustodyError("private manifest contains a malformed material record")
        relative = _safe_relative(entry.get("path"), "private manifest path")
        if relative in seen:
            raise CustodyError("private manifest repeats a material path")
        seen.add(relative)
        if type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o7777:
            raise CustodyError("private manifest contains an invalid material mode")
        if type(entry.get("ignored")) is not bool:
            raise CustodyError("private manifest contains an invalid inventory class")
        if entry["type"] == "file":
            if (
                set(entry) != {"path", "type", "bytes", "mode", "sha256", "ignored"}
                or type(entry.get("bytes")) is not int
                or entry["bytes"] < 0
                or not isinstance(entry.get("sha256"), str)
                or not HEX64.fullmatch(entry["sha256"])
            ):
                raise CustodyError("private manifest contains an invalid file record")
            observed_bytes += entry["bytes"]
        elif (
            set(entry) != {"path", "type", "target", "mode", "ignored"}
            or not isinstance(entry.get("target"), str)
        ):
            raise CustodyError("private manifest contains an invalid symlink record")
        else:
            _safe_symlink_target(entry["target"])
    if observed_bytes != control["inventory_bytes"]:
        raise CustodyError("private manifest byte count disagrees with snapshot control")
    return entries


def audit_source_snapshot(source: Path, snapshot: Path, control: dict | None = None) -> dict:
    """Prove the retained source still matches the exact private snapshot."""
    control = verify_snapshot(snapshot) if control is None else control
    manifest = _load_json(snapshot / "private-manifest.json", "private manifest")
    entries = _manifest_entries(snapshot, control)
    recorded_source = manifest["source"]
    current_source = _repository_identity(source, control["remote_ref"], control["remote_mode"])
    stable_keys = {
        "head",
        "branch",
        "checkout_bytes",
        "tracked_clean",
        "remote_ref",
        "remote_mode",
        "remote_fetch_push_parity",
    }
    if any(current_source[key] != recorded_source[key] for key in stable_keys):
        raise CustodyError("retained source identity no longer matches the private snapshot")
    current_inventory = _material_inventory(source)
    if current_inventory.total != control["inventory_bytes"] or current_inventory.entries != entries:
        raise CustodyError("retained private census no longer matches the private snapshot")
    _verify_material_proof(source, current_inventory, "during the retained-source audit")
    final_source = _repository_identity(source, control["remote_ref"], control["remote_mode"])
    _assert_material_metadata(source, current_inventory, "during the final retained-source check")
    if any(final_source[key] != recorded_source[key] for key in stable_keys):
        raise CustodyError("retained source identity changed during the private census audit")
    print("custody: retained source census matches the sealed snapshot", flush=True)
    return current_source


def _extract_materials(snapshot: Path, target: Path, entries: list[dict], total: int) -> None:
    expected = {entry["path"]: entry for entry in entries}
    seen: set[str] = set()
    progress = Progress("restoring private inventory", total)
    with tarfile.open(snapshot / "materials.tar", "r:") as archive:
        for member in archive:
            relative = _safe_relative(member.name, "archive member")
            if relative in seen or relative not in expected:
                raise CustodyError("materials archive contains an unexpected or duplicate member")
            seen.add(relative)
            entry = expected[relative]
            destination = _restore_path(target, relative)
            if destination.exists() or destination.is_symlink():
                raise CustodyError("material restore would overwrite an existing path")
            if entry["type"] == "symlink":
                if not member.issym() or member.linkname != entry["target"]:
                    raise CustodyError("archive symlink disagrees with its private manifest")
                os.symlink(member.linkname, destination)
                continue
            if not member.isfile() or member.size != entry["bytes"]:
                raise CustodyError("archive file disagrees with its private manifest")
            source = archive.extractfile(member)
            if source is None:
                raise CustodyError("archive file could not be read")
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, entry["mode"])
            digest = hashlib.sha256()
            with source, os.fdopen(descriptor, "wb") as writer:
                while True:
                    block = source.read(CHUNK)
                    if not block:
                        break
                    writer.write(block)
                    digest.update(block)
                    progress.add(len(block))
                writer.flush()
                _sync_regular_descriptor(writer.fileno())
            os.chmod(destination, entry["mode"])
            if digest.hexdigest() != entry["sha256"]:
                raise CustodyError("restored file digest disagrees with its private manifest")
    if seen != set(expected):
        raise CustodyError("materials archive is missing private manifest entries")


def _verify_restored_inventory(target: Path, entries: list[dict]) -> None:
    expected_paths = {entry["path"] for entry in entries}
    ignored_raw = _git(
        target,
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        text=False,
    ).stdout
    untracked_raw = _git(
        target,
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        text=False,
    ).stdout
    observed = {
        item.decode("utf-8", errors="strict")
        for item in ignored_raw.split(b"\0") + untracked_raw.split(b"\0")
        if item
    }
    if observed != expected_paths:
        raise CustodyError("restored private inventory is incomplete or contains extra files")
    progress = Progress("verifying restored inventory", sum(entry.get("bytes", 0) for entry in entries))
    for entry in entries:
        path = target / entry["path"]
        value = path.lstat()
        if stat.S_IMODE(value.st_mode) != entry["mode"]:
            raise CustodyError("restored material mode disagrees with its private manifest")
        if entry["type"] == "symlink":
            if not path.is_symlink() or os.readlink(path) != entry["target"]:
                raise CustodyError("restored material symlink disagrees with its private manifest")
        elif (
            not stat.S_ISREG(value.st_mode)
            or value.st_size != entry["bytes"]
            or _sha256(path, progress) != entry["sha256"]
        ):
            raise CustodyError("restored material file disagrees with its private manifest")


def _restore_required_bytes(control: dict) -> int:
    payload = (
        control["inventory_bytes"]
        + control["checkout_bytes"]
        + control["artifacts"]["source.bundle"]["bytes"]
    )
    return payload + max(1 << 30, payload // 20)


def restore_snapshot(snapshot: Path, target: Path) -> dict:
    _require_new_path(target, "restore target")
    _require_disjoint(snapshot, target)
    control = verify_snapshot(snapshot)
    entries = _manifest_entries(snapshot, control)
    try:
        free = shutil.disk_usage(target.parent).free
    except OSError as exc:
        raise CustodyError("restore target capacity could not be determined") from exc
    if free <= _restore_required_bytes(control):
        raise CustodyError("restore target has insufficient free space")
    target.mkdir(mode=0o700)
    if GIT is None:
        raise CustodyError("required command is unavailable: git")
    _run([GIT, "init", "--quiet", str(target)])
    _git(target, "fetch", "--quiet", str(snapshot / "source.bundle"), "HEAD")
    _git(target, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    restored_head = str(_git(target, "rev-parse", "HEAD").stdout).strip()
    if restored_head != control["source_head"]:
        raise CustodyError("restored source head disagrees with snapshot control")
    _extract_materials(snapshot, target, entries, control["inventory_bytes"])
    _verify_restored_inventory(target, entries)
    status = str(_git(target, "status", "--porcelain=v1", "--untracked-files=no").stdout)
    if status:
        raise CustodyError("restored source has tracked modifications")
    print(f"custody: clean restore verified for {control['snapshot_id']}", flush=True)
    return control


def redacted_receipt(
    source: Path,
    primary: Path,
    secondary: Path,
    target: Path,
    receipt_path: Path,
    primary_id: str,
    secondary_id: str,
) -> dict:
    for value, label in ((primary_id, "primary medium id"), (secondary_id, "secondary medium id")):
        if not ID.fullmatch(value):
            raise CustodyError(f"{label} must be a portable lowercase identifier")
    if primary_id == secondary_id:
        raise CustodyError("custody medium ids must be distinct")
    _require_new_path(target, "restore target")
    _require_new_path(receipt_path, "receipt target")
    _require_disjoint(source, primary, secondary, target, receipt_path)
    primary_root = primary.parent
    secondary_root = secondary.parent
    primary_device, secondary_device = ensure_independent(primary_root, secondary_root)
    first = verify_snapshot(primary)
    second = verify_snapshot(secondary)
    if (
        _json_bytes(first) != _json_bytes(second)
        or _sha256(primary / "control.json") != _sha256(secondary / "control.json")
    ):
        raise CustodyError("independent snapshot controls are not byte-identical")
    audit_source_snapshot(source, primary, first)
    restored = restore_snapshot(secondary, target)
    if restored != first:
        raise CustodyError("restore control disagrees with the verified copies")
    artifacts = first["artifacts"]
    manifest_digest = artifacts["private-manifest.json"]["sha256"]
    primary_medium = hashlib.sha256(
        f"danse-medium-v1:{primary_id}:{primary_device}".encode()
    ).hexdigest()
    secondary_medium = hashlib.sha256(
        f"danse-medium-v1:{secondary_id}:{secondary_device}".encode()
    ).hexdigest()
    return {
        "schema": RECEIPT_SCHEMA,
        "snapshot_identity_sha256": _sha256(primary / "control.json"),
        "source": {
            "head": first["source_head"],
            "remote_head_at_snapshot": first["remote_head"],
            "remote_mode": first["remote_mode"],
            "tracked_clean": first["tracked_clean"],
            "remote_fetch_push_parity": True,
        },
        "inventory": {
            "entries": first["inventory_entries"],
            "bytes": first["inventory_bytes"],
            "manifest_sha256": manifest_digest,
            "materials_sha256": artifacts["materials.tar"]["sha256"],
            "source_bundle_sha256": artifacts["source.bundle"]["sha256"],
        },
        "independent_verified_copies": [
            {
                "medium_id": primary_medium,
                "device_identity_sha256": primary_device,
                "manifest_sha256": manifest_digest,
                "verified": True,
            },
            {
                "medium_id": secondary_medium,
                "device_identity_sha256": secondary_device,
                "manifest_sha256": manifest_digest,
                "verified": True,
            },
        ],
        "restore_rehearsal": {
            "ok": True,
            "restored_from": secondary_medium,
            "source_head": first["source_head"],
            "inventory_verified": True,
            "tracked_clean": True,
        },
        "human_acceptance": {"ok": False, "receipt": None},
        "cleanup_authorized": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot", help="create and independently duplicate custody")
    snapshot.add_argument("--source", type=Path, required=True)
    snapshot.add_argument("--primary-root", type=Path, required=True)
    snapshot.add_argument("--secondary-root", type=Path, required=True)
    snapshot.add_argument("--snapshot-id", required=True)
    snapshot.add_argument("--remote-ref", required=True)
    snapshot.add_argument("--remote-mode", choices=("equal", "ancestor"), required=True)

    restore = subparsers.add_parser("restore", help="verify both copies and rehearse a clean restore")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--primary", type=Path, required=True)
    restore.add_argument("--secondary", type=Path, required=True)
    restore.add_argument("--primary-id", required=True)
    restore.add_argument("--secondary-id", required=True)
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            ensure_independent(args.primary_root, args.secondary_root)
            primary = create_snapshot(
                args.source,
                args.primary_root,
                args.snapshot_id,
                args.remote_ref,
                args.remote_mode,
            )
            copy_snapshot(primary, args.secondary_root)
        else:
            receipt = redacted_receipt(
                args.source,
                args.primary,
                args.secondary,
                args.target,
                args.receipt,
                args.primary_id,
                args.secondary_id,
            )
            _write_new(args.receipt, _json_bytes(receipt))
            _fsync_directory(args.receipt.parent)
            print("custody: wrote one redacted restore receipt", flush=True)
    except CustodyError as exc:
        print(f"custody: BLOCKED — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

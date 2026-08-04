#!/usr/bin/env python3
"""Build and verify the deliberately small Danse GitHub Pages artifact.

The repository is not a website root. This builder names the runtime files that
may be published, derives the photographic derivatives from the public corpus
manifest, rejects symlinks and path traversal, and emits a deterministic digest
manifest that binds every delivered byte to the source commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_MANIFEST = "pages-manifest.json"
ARTIFACT_SCHEMA = "danse.pages.v1"
REPOSITORY = "organvm/the-thing-without-a-name"
PUBLIC_TIERS = ("browse", "screen")
ENGINE_MODULES = (
    "engine/clock.js",
    "engine/corpus.js",
    "engine/engine.js",
    "engine/gl.js",
    "engine/grammar.js",
    "engine/mat4.js",
    "engine/program.js",
    "engine/renderer.js",
    "engine/rng.js",
    "engine/room.js",
    "engine/score.js",
)
INTERACTION_MODULES = (
    "interaction/adapter.js",
    "interaction/camera.js",
    "interaction/controller.js",
    "interaction/session.js",
)
VENDOR_BASE = "interaction/vendor/mediapipe"
VENDOR_MANIFEST = f"{VENDOR_BASE}/manifest.json"
RELEASE_MANIFEST = "release/manifest.json"
RUNTIME_FILES = (
    ".nojekyll",
    "index.html",
    "arrival.js",
    *ENGINE_MODULES,
    *INTERACTION_MODULES,
    VENDOR_MANIFEST,
    "render/program.json",
)
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
FRAME_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
IMPORT_RE = re.compile(
    r"(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)(?:\"(?P<double>\.[^\"]+)\"|'(?P<single>\.[^']+)')"
)


class ArtifactError(RuntimeError):
    """The public artifact would be incomplete or exceed its declared boundary."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactError(f"{label} must be a non-empty relative path")
    if "\\" in value or value.startswith("/"):
        raise ArtifactError(f"{label} is not a portable relative path: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ArtifactError(f"{label} contains an unsafe path component: {value!r}")
    return PurePosixPath(*parts).as_posix()


def source_file(root: Path, relative: str) -> Path:
    relative = safe_relative(relative, "allowlisted source")
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ArtifactError(f"allowlisted source is or crosses a symlink: {relative}")
    if not candidate.is_file():
        raise ArtifactError(f"allowlisted source is missing or not a regular file: {relative}")
    return candidate


def corpus_files(root: Path) -> set[str]:
    manifest_path = source_file(root, "corpus/manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"cannot read the public corpus manifest: {exc}") from exc

    if manifest.get("schema") != "danse.corpus.v1":
        raise ArtifactError(f"unsupported corpus schema: {manifest.get('schema')!r}")
    tiers = manifest.get("tiers")
    if not isinstance(tiers, dict) or set(tiers) != set(PUBLIC_TIERS):
        raise ArtifactError(
            "the public corpus manifest must declare exactly the browse and screen tiers"
        )

    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ArtifactError("the public corpus manifest has no frames")
    frame_ids: list[str] = []
    for frame in frames:
        frame_id = frame.get("id") if isinstance(frame, dict) else None
        if not isinstance(frame_id, str) or not FRAME_ID_RE.fullmatch(frame_id):
            raise ArtifactError(f"unsafe corpus frame id: {frame_id!r}")
        frame_ids.append(frame_id)
    if len(frame_ids) != len(set(frame_ids)):
        raise ArtifactError("the public corpus manifest contains duplicate frame ids")

    room = manifest.get("room")
    room_file = safe_relative(room.get("file") if isinstance(room, dict) else None, "room file")
    if len(PurePosixPath(room_file).parts) != 1 or not room_file.endswith(".webp"):
        raise ArtifactError(f"room file must be one WebP inside corpus/: {room_file!r}")
    score_file = safe_relative(manifest.get("score"), "score file")
    if len(PurePosixPath(score_file).parts) != 1 or not score_file.endswith(".json"):
        raise ArtifactError(f"score file must be one JSON file inside corpus/: {score_file!r}")

    files = {
        "corpus/manifest.json",
        f"corpus/{room_file}",
        f"corpus/{score_file}",
    }
    for tier in PUBLIC_TIERS:
        declaration = tiers[tier]
        if not isinstance(declaration, dict) or declaration.get("local") is not False:
            raise ArtifactError(f"public tier {tier!r} must be explicitly non-local")
        for kind in ("plates", "mattes"):
            template = declaration.get(kind)
            expected = f"{kind}/{tier}/<id>.webp"
            if template != expected:
                raise ArtifactError(
                    f"public tier {tier!r} must declare {kind} as {expected!r}, got {template!r}"
                )
            for frame_id in frame_ids:
                files.add(f"corpus/{template.replace('<id>', frame_id)}")
    return files


def vendor_files(root: Path) -> set[str]:
    """Resolve and authenticate the locally served pose runtime.

    The browser may load only files named by this reviewed manifest. Digesting
    them here keeps a package update from silently expanding the public surface.
    """
    manifest_path = source_file(root, VENDOR_MANIFEST)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"cannot read the pose vendor manifest: {exc}") from exc
    if set(manifest) != {"schema", "package", "model", "patch", "files"}:
        raise ArtifactError("pose vendor manifest has an unknown shape")
    if manifest.get("schema") != "danse.vendor.v1":
        raise ArtifactError(f"unsupported pose vendor schema: {manifest.get('schema')!r}")
    package = manifest.get("package")
    model = manifest.get("model")
    if not isinstance(package, dict) or set(package) != {
        "name", "version", "source", "integrity", "sha512", "license"
    }:
        raise ArtifactError("pose vendor manifest must declare package and model custody")
    if not all(isinstance(package[key], str) and package[key] for key in package):
        raise ArtifactError("pose vendor package custody fields must be non-empty strings")
    if not package["source"].startswith("https://") or not re.fullmatch(
        r"[0-9a-f]{128}", package["sha512"]
    ) or not package["integrity"].startswith("sha512-"):
        raise ArtifactError("pose vendor package source digests are invalid")
    if not isinstance(model, dict) or set(model) != {"name", "version", "source", "license"}:
        raise ArtifactError("pose vendor manifest must declare model custody")
    if not all(isinstance(model[key], str) and model[key] for key in model):
        raise ArtifactError("pose vendor model custody fields must be non-empty strings")
    if not model["source"].startswith("https://"):
        raise ArtifactError("pose vendor model source must be HTTPS")
    patch = manifest.get("patch")
    if not isinstance(patch, dict) or set(patch) != {"reason", "transformations", "upstreamSha256"}:
        raise ArtifactError("pose vendor manifest must declare its deterministic patch")
    if not isinstance(patch["reason"], str) or not patch["reason"]:
        raise ArtifactError("pose vendor deterministic patch must state a reason")
    if not isinstance(patch["transformations"], list) or not all(
        isinstance(item, str) and item for item in patch["transformations"]
    ):
        raise ArtifactError("pose vendor deterministic patch transformations are invalid")
    if not isinstance(patch["upstreamSha256"], dict) or not all(
        isinstance(path, str) and re.fullmatch(r"[0-9a-f]{64}", str(digest))
        for path, digest in patch["upstreamSha256"].items()
    ):
        raise ArtifactError("pose vendor upstream digests are invalid")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ArtifactError("pose vendor manifest has no files")

    paths: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise ArtifactError("pose vendor manifest contains a malformed file record")
        leaf = safe_relative(record["path"], "pose vendor path")
        if leaf == "manifest.json" or Path(leaf).suffix not in {".js", ".mjs", ".wasm", ".task", ".txt"}:
            raise ArtifactError(f"unsupported pose vendor file: {leaf}")
        relative = f"{VENDOR_BASE}/{leaf}"
        path = source_file(root, relative)
        if not isinstance(record["bytes"], int) or record["bytes"] < 0:
            raise ArtifactError(f"invalid pose vendor byte count for {leaf}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record["sha256"])):
            raise ArtifactError(f"invalid pose vendor SHA-256 for {leaf}")
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ArtifactError(f"pose vendor digest mismatch: {leaf}")
        if path.suffix in {".js", ".mjs"}:
            text = path.read_text(encoding="utf-8", errors="strict")
            forbidden = {
                "Date.now": r"\bDate\.now\b",
                "performance.now": r"\bperformance\.now\b",
                "Math.random": r"\bMath\.random\b",
                "crypto.getRandomValues": r"\bgetRandomValues\b",
                "runtime CDN": r"(?:cdn\.jsdelivr\.net|storage\.googleapis\.com|odml\.pa\.googleapis\.com)",
            }
            hit = next((label for label, pattern in forbidden.items() if re.search(pattern, text)), None)
            if hit:
                raise ArtifactError(f"pose vendor runtime contains forbidden {hit}: {leaf}")
        paths.append(relative)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ArtifactError("pose vendor manifest paths must be unique and sorted")
    upstream_paths = {
        f"{VENDOR_BASE}/{safe_relative(path, 'pose vendor upstream path')}"
        for path in patch["upstreamSha256"]
    }
    if not upstream_paths <= set(paths):
        raise ArtifactError("pose vendor patch names a file outside its delivered inventory")
    return {VENDOR_MANIFEST, *paths}


def release_files(root: Path) -> set[str]:
    """Resolve the exact cleared public release assets, without publishing the manifest."""
    candidate = root / RELEASE_MANIFEST
    if candidate.is_symlink():
        raise ArtifactError("release manifest must not be a symlink")
    if not candidate.exists():
        return set()
    manifest_path = source_file(root, RELEASE_MANIFEST)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"cannot read the release manifest: {exc}") from exc
    compact_top = {"schema", "release_id", "status", "media", "credits", "gates"}
    full_top = {
        "schema",
        "release_id",
        "version",
        "status",
        "opportunity_snapshot",
        "identity",
        "copy",
        "installation",
        "accessibility",
        "press",
        "claims",
        "credits",
        "media",
        "gates",
    }
    if not isinstance(manifest, dict) or set(manifest) not in (compact_top, full_top):
        raise ArtifactError("release manifest has fields outside its closed schema")
    if manifest.get("schema") != "danse.release.v1":
        raise ArtifactError("unsupported release manifest schema")
    media = manifest.get("media")
    if not isinstance(media, list):
        raise ArtifactError("release manifest has no media inventory")

    paths: set[str] = set()
    media_ids: set[str] = set()
    for row in media:
        if not isinstance(row, dict):
            raise ArtifactError("release manifest contains malformed media")
        row_keys = set(row)
        if row_keys not in (
            {"id", "required_for", "status", "source", "clearance"},
            {"id", "kind", "label", "required_for", "status", "source", "clearance", "alt_text"},
        ):
            raise ArtifactError("release manifest media has fields outside its closed schema")
        media_id = row.get("id")
        if not isinstance(media_id, str) or not media_id or media_id in media_ids:
            raise ArtifactError("release manifest media ids must be non-empty and unique")
        media_ids.add(media_id)
        phases = row.get("required_for")
        if (
            not isinstance(phases, list)
            or not phases
            or not all(isinstance(item, str) for item in phases)
            or len(phases) != len(set(phases))
        ):
            raise ArtifactError(f"release media {media_id} has invalid phase scope")
        if "public" not in phases:
            continue
        clearance = row.get("clearance")
        if not isinstance(clearance, dict) or set(clearance) not in (
            {"status"},
            {"status", "owner", "evidence"},
        ):
            raise ArtifactError(f"release media {media_id} has malformed clearance")
        ready = row.get("status") == "ready"
        cleared = clearance.get("status") == "cleared"
        if not ready and not cleared:
            continue
        if not ready or not cleared:
            raise ArtifactError(f"release media {media_id} has inconsistent public readiness")
        source = row.get("source")
        if not isinstance(source, dict) or set(source) != {
            "path",
            "destination",
            "sha256",
            "bytes",
        }:
            raise ArtifactError(f"release media {media_id} has malformed source identity")
        source_relative = safe_relative(source.get("path"), f"release media {media_id} source")
        destination = safe_relative(
            source.get("destination"),
            f"release media {media_id} destination",
        )
        if source_relative != destination or not destination.startswith("media/assets/"):
            raise ArtifactError(f"release media {media_id} is outside its public destination")
        if destination in paths:
            raise ArtifactError(f"release media destination is duplicated: {destination}")
        path = source_file(root, source_relative)
        size = source.get("bytes")
        digest = source.get("sha256")
        if (
            type(size) is not int
            or size < 0
            or size != path.stat().st_size
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest != sha256(path)
        ):
            raise ArtifactError(f"release media {media_id} source identity is stale")
        paths.add(destination)
    return paths


def validate_module_closure(root: Path, files: set[str]) -> None:
    """Fail when a published module refers to a local module outside the boundary."""
    for relative in sorted(files):
        if relative != "index.html" and not relative.endswith((".js", ".mjs")):
            continue
        text = source_file(root, relative).read_text(encoding="utf-8")
        for match in IMPORT_RE.finditer(text):
            reference = match.group("double") or match.group("single")
            reference = reference.split("?", 1)[0].split("#", 1)[0]
            joined = posixpath.normpath(
                posixpath.join(PurePosixPath(relative).parent.as_posix(), reference)
            )
            dependency = safe_relative(joined, f"module dependency from {relative}")
            if dependency not in files:
                raise ArtifactError(
                    f"published module {relative} imports non-public dependency {dependency}"
                )


def source_files(root: Path) -> tuple[str, ...]:
    root = root.absolute()
    if root.is_symlink():
        raise ArtifactError(f"source root must not be a symlink: {root}")
    root = root.resolve()
    if not root.is_dir():
        raise ArtifactError(f"source root is not a regular directory: {root}")
    files = set(RUNTIME_FILES) | corpus_files(root) | vendor_files(root) | release_files(root)
    for relative in files:
        source_file(root, relative)
    validate_module_closure(root, files)
    return tuple(sorted(files))


def source_commit(root: Path, explicit: str | None = None) -> str:
    commit = explicit
    if commit is None:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise ArtifactError(f"cannot resolve source commit: {done.stderr.strip()}")
        commit = done.stdout.strip()
    commit = commit.lower()
    if not COMMIT_RE.fullmatch(commit):
        raise ArtifactError(f"source commit must be a full 40-character Git SHA: {commit!r}")
    return commit


def artifact_inventory(root: Path) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise ArtifactError(f"artifact root is not a regular directory: {root}")
    files: set[str] = set()
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise ArtifactError(f"artifact contains a symlinked directory: {path.relative_to(root)}")
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise ArtifactError(f"artifact contains a non-regular file: {relative}")
            files.add(relative)
    return files


def verify_artifact(output: Path, expected_commit: str | None = None) -> dict:
    output = output.absolute()
    if output.is_symlink():
        raise ArtifactError(f"artifact root must not be a symlink: {output}")
    output = output.resolve()
    manifest_path = output / ARTIFACT_MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ArtifactError(f"artifact is missing {ARTIFACT_MANIFEST}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"cannot read artifact manifest: {exc}") from exc

    if set(manifest) != {"schema", "source", "files"} or manifest.get("schema") != ARTIFACT_SCHEMA:
        raise ArtifactError("artifact manifest has an unknown shape or schema")
    source = manifest.get("source")
    if not isinstance(source, dict) or set(source) != {"repository", "commit"}:
        raise ArtifactError("artifact manifest has an invalid source receipt")
    if source.get("repository") != REPOSITORY or not COMMIT_RE.fullmatch(str(source.get("commit", ""))):
        raise ArtifactError("artifact manifest source receipt is invalid")
    if expected_commit is not None and source["commit"] != source_commit(output, expected_commit):
        raise ArtifactError(
            f"artifact source commit {source['commit']} does not match expected {expected_commit}"
        )

    records = manifest.get("files")
    if not isinstance(records, list):
        raise ArtifactError("artifact manifest files must be a list")
    paths: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise ArtifactError("artifact manifest contains a malformed file record")
        relative = safe_relative(record["path"], "artifact manifest path")
        if relative == ARTIFACT_MANIFEST:
            raise ArtifactError("artifact manifest cannot digest itself")
        if not isinstance(record["bytes"], int) or record["bytes"] < 0:
            raise ArtifactError(f"invalid byte count for {relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record["sha256"])):
            raise ArtifactError(f"invalid SHA-256 for {relative}")
        path = output / PurePosixPath(relative)
        if path.is_symlink() or not path.is_file():
            raise ArtifactError(f"manifest names a missing or non-regular file: {relative}")
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ArtifactError(f"artifact digest mismatch: {relative}")
        paths.append(relative)

    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ArtifactError("artifact manifest paths must be unique and sorted")
    inventory = artifact_inventory(output)
    expected = set(paths) | {ARTIFACT_MANIFEST}
    if inventory != expected:
        extra = sorted(inventory - expected)
        missing = sorted(expected - inventory)
        raise ArtifactError(f"artifact inventory mismatch; extra={extra}, missing={missing}")
    return manifest


def build(root: Path, output: Path, commit: str) -> dict:
    root = root.absolute()
    if root.is_symlink():
        raise ArtifactError(f"source root must not be a symlink: {root}")
    root = root.resolve()
    output = output.absolute()
    if output.is_symlink():
        raise ArtifactError(f"refusing symlinked artifact output: {output}")
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise ArtifactError(f"artifact output must be absent or empty: {output}")
    output_resolved = output.resolve()
    if output_resolved == root or root in output_resolved.parents:
        raise ArtifactError("artifact output must be outside the source repository")

    commit = source_commit(root, commit)
    files = source_files(root)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for relative in files:
        source = source_file(root, relative)
        target = output / PurePosixPath(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)
        target.chmod(0o644)
        os.utime(target, (0, 0), follow_symlinks=False)
        records.append(
            {"path": relative, "bytes": target.stat().st_size, "sha256": sha256(target)}
        )

    manifest = {
        "schema": ARTIFACT_SCHEMA,
        "source": {"repository": REPOSITORY, "commit": commit},
        "files": records,
    }
    manifest_path = output / ARTIFACT_MANIFEST
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    manifest_path.chmod(0o644)
    os.utime(manifest_path, (0, 0), follow_symlinks=False)
    return verify_artifact(output, commit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="build a new artifact at this path")
    action.add_argument("--verify", type=Path, help="verify an existing artifact")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--source-commit", help="expected full source commit SHA")
    args = parser.parse_args()

    try:
        if args.output:
            commit = source_commit(args.root, args.source_commit)
            manifest = build(args.root, args.output, commit)
        else:
            manifest = verify_artifact(args.verify, args.source_commit)
    except ArtifactError as exc:
        parser.exit(1, f"pages artifact: {exc}\n")
    print(
        f"pages artifact: {len(manifest['files'])} files from "
        f"{manifest['source']['commit']} verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

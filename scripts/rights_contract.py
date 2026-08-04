#!/usr/bin/env python3
"""Strict, redacted, phase-aware rights contract for Danse.

The tracked register is an inventory and a gate, never a substitute for a
release, signature, repertoire decision, legal review, or artist attestation.
Private evidence stays outside Git. Only a public-safe receipt with an exact
digest may satisfy a tracked gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "rights" / "register.json"
SCHEMA = ROOT / "rights" / "register.schema.json"
PHASES = ("draft", "public", "package", "uploaded", "submitted", "release")
PHASE_SCOPES = {
    "draft": (),
    "public": ("public",),
    "package": ("package",),
    "uploaded": ("package", "uploaded"),
    "submitted": ("package", "uploaded", "submitted"),
    "release": ("public", "package", "release"),
}
EXPECTED_CATEGORIES = {
    "performer",
    "photograph",
    "video",
    "design",
    "pictured-object",
    "archive",
    "font",
    "texture",
    "recording",
    "still",
    "music",
    "software",
    "text",
    "installation-evidence",
    "other-third-party",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
PRIVATE_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:[\\/]Users[\\/]|file://|(?:^|\s)~[\\/])")
PRIVATE_PREFIXES = (".git/", ".work/", ".worktrees/", "pipeline/.work/", "sound/bank/")
SENSITIVE_KEYS = {
    "address",
    "credential",
    "credentials",
    "email",
    "local_path",
    "password",
    "phone",
    "private_path",
    "secret",
    "signature",
    "token",
}
RIGHTS_MEDIA_SUFFIXES = {
    ".avif",
    ".flac",
    ".gif",
    ".mov",
    ".mp4",
    ".mp3",
    ".mxf",
    ".m4v",
    ".m4a",
    ".jpg",
    ".jpeg",
    ".png",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".webp",
    ".aif",
    ".aiff",
}


class RightsError(ValueError):
    """The rights register or a bound artifact violates its contract."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RightsError(f"JSON contains duplicate key {key!r}")
        value[key] = item
    return value


def load_json(path: Path, label: str, *, expose_path: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_object)
    except (OSError, json.JSONDecodeError) as exc:
        detail = f" at {path}: {exc}" if expose_path else ": invalid or unreadable JSON"
        raise RightsError(f"cannot read {label}{detail}") from exc
    if not isinstance(value, dict):
        raise RightsError(f"{label} must be a JSON object")
    return value


def load_yaml(path: Path, label: str, *, expose_path: bool = True) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader) or {}
    except (OSError, yaml.YAMLError) as exc:
        detail = f" at {path}: {exc}" if expose_path else ": invalid or unreadable YAML"
        raise RightsError(f"cannot read {label}{detail}") from exc
    if not isinstance(value, dict):
        raise RightsError(f"{label} must be a mapping")
    return value


def safe_relative(value: object, label: str, *, expose_value: bool = True) -> str:
    if not isinstance(value, str) or not value:
        raise RightsError(f"{label} must be a non-empty relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in pure.parts):
        detail = f": {value!r}" if expose_value else ""
        raise RightsError(f"{label} is not a safe portable relative path{detail}")
    relative = pure.as_posix()
    if relative in {prefix.rstrip("/") for prefix in PRIVATE_PREFIXES} or relative.startswith(PRIVATE_PREFIXES):
        raise RightsError(f"{label} points into private or generated custody: {relative!r}")
    return relative


def regular_file(root: Path, relative: object, label: str, *, expose_value: bool = True) -> Path:
    relative = safe_relative(relative, label, expose_value=expose_value)
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise RightsError(f"repository root must be a regular directory: {root}")
    root = root.resolve()
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            detail = f": {relative!r}" if expose_value else ""
            raise RightsError(f"{label} traverses a symlink{detail}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        detail = f": {relative!r}" if expose_value else ""
        raise RightsError(f"{label} is missing or outside the repository{detail}") from exc
    if not resolved.is_file():
        detail = f": {relative!r}" if expose_value else ""
        raise RightsError(f"{label} is not a regular file{detail}")
    return resolved


def tracked_paths(root: Path) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RightsError(f"cannot query the Git source inventory: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RightsError(f"cannot query the Git source inventory: {detail or f'exit {result.returncode}'}")
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def verify_record(root: Path, record: dict[str, Any], label: str, tracked: set[str]) -> Path:
    relative = safe_relative(record.get("path"), f"{label} path")
    if relative not in tracked:
        raise RightsError(f"{label} path is not tracked by Git: {relative!r}")
    path = regular_file(root, relative, f"{label} path")
    expected = record.get("sha256")
    if not isinstance(expected, str) or not HEX64.fullmatch(expected):
        raise RightsError(f"{label} has no valid lowercase SHA-256")
    actual = sha256(path)
    if actual != expected:
        raise RightsError(f"{label} digest mismatch for {relative}: expected {expected}, got {actual}")
    return path


def _source_records(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for name, binding in document.get("bindings", {}).items():
        if isinstance(binding, dict) and isinstance(binding.get("source"), dict):
            yield f"binding {name}", binding["source"]
    for index, binding in enumerate(document.get("package_text", [])):
        if isinstance(binding, dict) and isinstance(binding.get("source"), dict):
            yield f"package_text[{index}] source", binding["source"]
    for index, gate in enumerate(document.get("human_gates", [])):
        evidence = gate.get("evidence") if isinstance(gate, dict) else None
        if isinstance(evidence, dict):
            yield f"human_gates[{index}] evidence", evidence
    for asset_index, asset in enumerate(document.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        license_row = asset.get("license")
        if isinstance(license_row, dict) and isinstance(license_row.get("evidence"), dict):
            yield f"assets[{asset_index}] license evidence", license_row["evidence"]
        private = asset.get("private_evidence")
        if isinstance(private, dict) and isinstance(private.get("receipt"), dict):
            yield f"assets[{asset_index}] private evidence receipt", private["receipt"]
        for source_index, source in enumerate(asset.get("provenance", [])):
            if isinstance(source, dict):
                yield f"assets[{asset_index}] provenance[{source_index}]", source
        for use_index, use in enumerate(asset.get("uses", [])):
            evidence = use.get("evidence") if isinstance(use, dict) else None
            if isinstance(evidence, dict):
                yield f"assets[{asset_index}] uses[{use_index}] evidence", evidence


def _strings(value: Any) -> Iterator[tuple[str, str]]:
    def walk(item: Any, location: str) -> Iterator[tuple[str, str]]:
        if isinstance(item, str):
            yield location, item
        elif isinstance(item, dict):
            for key, child in item.items():
                yield from walk(child, f"{location}.{key}" if location else str(key))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                yield from walk(child, f"{location}[{index}]")

    yield from walk(value, "")


def _keys(value: Any, location: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}" if location else str(key)
            yield child_location, key
            yield from _keys(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _keys(child, f"{location}[{index}]")


def _schema_errors(document: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return [f"rights schema is invalid: {exc.message}"]
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    rendered = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "register"
        rendered.append(f"{location}: {error.message}")
    return rendered


def _validate_bindings(
    root: Path,
    document: dict[str, Any],
    verified: dict[str, Path],
    tracked: set[str],
) -> list[str]:
    errors: list[str] = []
    bindings = document["bindings"]

    try:
        corpus = load_json(verified["binding corpus"], "corpus binding")
        declared = bindings["corpus"]
        frames = corpus.get("frames")
        if corpus.get("schema") != declared["schema"]:
            errors.append("binding corpus schema disagrees with corpus/manifest.json")
        if not isinstance(frames, list) or len(frames) != declared["frames"]:
            errors.append(f"binding corpus frame count is not {declared['frames']}")
        elif len({row.get("id") for row in frames if isinstance(row, dict)}) != len(frames):
            errors.append("binding corpus frame ids are not unique")
    except RightsError as exc:
        errors.append(str(exc))

    try:
        music = load_yaml(verified["binding music"], "music binding")
        declared = bindings["music"]
        works = music.get("works")
        gate = music.get("artistic_gate") if isinstance(music.get("artistic_gate"), dict) else {}
        if music.get("schema") != declared["schema"]:
            errors.append("binding music schema disagrees with music/repertoire.yaml")
        if not isinstance(works, list) or len(works) != declared["works"]:
            errors.append(f"binding music work count is not {declared['works']}")
        if gate.get("status") != declared["artistic_gate"]:
            errors.append("binding music artistic gate has drifted")
    except RightsError as exc:
        errors.append(str(exc))

    try:
        vendor_path = verified["binding pose_vendor"]
        vendor = load_json(vendor_path, "pose vendor binding")
        declared = bindings["pose_vendor"]
        files = vendor.get("files")
        if vendor.get("schema") != declared["schema"]:
            errors.append("binding pose_vendor schema disagrees with its manifest")
        if not isinstance(files, list) or len(files) != declared["files"]:
            errors.append(f"binding pose_vendor file count is not {declared['files']}")
            files = []
        package = vendor.get("package") if isinstance(vendor.get("package"), dict) else {}
        model = vendor.get("model") if isinstance(vendor.get("model"), dict) else {}
        if package.get("license") != declared["package_license"]:
            errors.append("binding pose_vendor package license has drifted")
        if model.get("license") != declared["model_license"]:
            errors.append("binding pose_vendor model license has drifted")
        vendor_relative = PurePosixPath(bindings["pose_vendor"]["source"]["path"]).parent
        seen: set[str] = set()
        for index, row in enumerate(files):
            if not isinstance(row, dict):
                errors.append(f"pose vendor files[{index}] is not a record")
                continue
            try:
                leaf = safe_relative(row.get("path"), f"pose vendor files[{index}] path")
                combined = (vendor_relative / leaf).as_posix()
                if combined in seen:
                    errors.append(f"pose vendor file is duplicated: {combined}")
                    continue
                seen.add(combined)
                if combined not in tracked:
                    errors.append(f"pose vendor file is not tracked: {combined}")
                    continue
                file_path = regular_file(root, combined, f"pose vendor files[{index}]")
                if row.get("sha256") != sha256(file_path):
                    errors.append(f"pose vendor file digest mismatch: {combined}")
                if row.get("bytes") != file_path.stat().st_size:
                    errors.append(f"pose vendor file byte count mismatch: {combined}")
            except RightsError as exc:
                errors.append(str(exc))
        if "interaction/vendor/mediapipe/Apache-2.0.txt" not in seen:
            errors.append("pose vendor bundle does not retain Apache-2.0.txt")
    except RightsError as exc:
        errors.append(str(exc))

    try:
        submission = load_yaml(verified["binding submission"], "submission binding")
        declared = bindings["submission"]
        if submission.get("schema") != declared["schema"]:
            errors.append("binding submission schema disagrees with its register")
        terms = {
            row.get("id")
            for row in submission.get("terms", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        missing = sorted(set(declared["required_terms"]) - terms)
        if missing:
            errors.append(f"binding submission is missing published terms: {', '.join(missing)}")
    except RightsError as exc:
        errors.append(str(exc))
    return errors


def validate_document(
    document: dict[str, Any],
    *,
    root: Path = ROOT,
    schema: dict[str, Any] | None = None,
    enforce_tracked: bool = True,
) -> list[str]:
    """Validate schema, redaction, exact sources, inventory, and rule graph."""
    errors: list[str] = []
    if schema is None:
        try:
            schema = load_json(SCHEMA, "rights schema")
        except RightsError as exc:
            return [str(exc)]
    errors.extend(_schema_errors(document, schema))
    for location, value in _strings(document):
        if PRIVATE_PATH.search(value):
            errors.append(f"{location}: contains a private or machine-local path")
        if EMAIL.search(value):
            errors.append(f"{location}: contains an email address")
        if PHONE.search(value):
            errors.append(f"{location}: contains a phone number")
    for location, key in _keys(document):
        if key.lower() in SENSITIVE_KEYS:
            errors.append(f"{location}: prohibited sensitive field {key!r}")
    if errors:
        return errors

    try:
        tracked = tracked_paths(root) if enforce_tracked else {
            record["path"] for _, record in _source_records(document)
        }
    except RightsError as exc:
        return errors + [str(exc)]

    verified: dict[str, Path] = {}
    path_digests: dict[str, str] = {}
    for label, record in _source_records(document):
        path = record.get("path")
        digest = record.get("sha256")
        if isinstance(path, str) and isinstance(digest, str):
            previous = path_digests.setdefault(path, digest)
            if previous != digest:
                errors.append(f"{label}: {path} is declared with conflicting digests")
                continue
        try:
            verified[label] = verify_record(root, record, label, tracked)
        except RightsError as exc:
            errors.append(str(exc))

    required_binding_labels = {f"binding {name}" for name in ("corpus", "music", "pose_vendor", "submission")}
    if required_binding_labels <= verified.keys():
        errors.extend(_validate_bindings(root, document, verified, tracked))

    gate_rows = document["human_gates"]
    gate_ids = [row["id"] for row in gate_rows]
    if len(gate_ids) != len(set(gate_ids)):
        errors.append("human gate ids must be unique")
    attestation_keys: set[str] = set()
    for gate in gate_rows:
        evidence = gate["evidence"]
        if gate["state"] == "satisfied" and evidence is None:
            errors.append(f"human gate {gate['id']} is satisfied without a redacted evidence receipt")
        if gate["state"] != "satisfied" and evidence is not None:
            errors.append(f"human gate {gate['id']} is {gate['state']} but carries completion evidence")
        attestation = gate["attestation"]
        if attestation is not None:
            key = attestation["key"]
            if key in attestation_keys:
                errors.append(f"attestation key is reused: {key}")
            attestation_keys.add(key)
            values = attestation["values"]
            if attestation["kind"] == "boolean" and values != [True]:
                errors.append(f"human gate {gate['id']} boolean attestation must accept only true")
            if attestation["kind"] == "choice" and (
                len(values) < 2 or not all(isinstance(value, str) and value for value in values)
            ):
                errors.append(f"human gate {gate['id']} choice attestation needs at least two named values")

    asset_rows = document["assets"]
    assessed_on = date.fromisoformat(document["assessment"]["date"])
    asset_ids = [row["id"] for row in asset_rows]
    if len(asset_ids) != len(set(asset_ids)):
        errors.append("asset ids must be unique")
    categories = {row["category"] for row in asset_rows}
    if categories != EXPECTED_CATEGORIES:
        missing = sorted(EXPECTED_CATEGORIES - categories)
        extra = sorted(categories - EXPECTED_CATEGORIES)
        errors.append(f"asset category census is incomplete (missing={missing}, extra={extra})")

    assets: dict[str, dict[str, Any]] = {row["id"]: row for row in asset_rows}
    vendor_asset = assets.get("mediapipe-pose-runtime")
    if vendor_asset is None:
        errors.append("the exact MediaPipe vendor bundle has no asset disposition")
    else:
        vendor_license = vendor_asset["license"] or {}
        declared_vendor = document["bindings"]["pose_vendor"]
        if not (
            vendor_license.get("spdx")
            == declared_vendor["package_license"]
            == declared_vendor["model_license"]
        ):
            errors.append("the MediaPipe asset license disagrees with the exact package/model binding")
    uses: dict[tuple[str, str], dict[str, Any]] = {}
    for asset in asset_rows:
        asset_id = asset["id"]
        disposition = asset["disposition"]
        license_row = asset["license"]
        blocker = asset["blocker"]
        if disposition in {"owned", "licensed"} and not asset["rights_holder"]:
            errors.append(f"asset {asset_id} with disposition {disposition} must name a rights holder")
        if disposition == "licensed" and license_row is None:
            errors.append(f"licensed asset {asset_id} has no license")
        if disposition != "licensed" and license_row is not None:
            errors.append(f"non-licensed asset {asset_id} carries a license record")
        if disposition in {"owned", "licensed", "public-domain-with-provenance"} and not asset["provenance"]:
            errors.append(f"asset {asset_id} has no public-safe provenance")
        if disposition == "blocked" and not blocker:
            errors.append(f"blocked asset {asset_id} does not state its blocker")
        if disposition == "excluded" and blocker is not None:
            errors.append(f"excluded asset {asset_id} must not carry an unresolved blocker")

        credit = asset["public_credit"]
        if credit["state"] == "approved" and not credit["label"]:
            errors.append(f"asset {asset_id} has an approved blank public credit")
        private = asset["private_evidence"]
        if private["state"] == "verified" and private["receipt"] is None:
            errors.append(f"asset {asset_id} claims verified private evidence without a redacted receipt")
        if private["state"] != "verified" and private["receipt"] is not None:
            errors.append(f"asset {asset_id} carries a private-evidence receipt while {private['state']}")
        if private["state"] == "not-required" and private["custodian"] is not None:
            errors.append(f"asset {asset_id} does not require private evidence but names a custodian")

        local_use_ids: set[str] = set()
        for use in asset["uses"]:
            use_id = use["id"]
            key = (asset_id, use_id)
            if use_id in local_use_ids:
                errors.append(f"asset {asset_id} repeats use id {use_id}")
            local_use_ids.add(use_id)
            uses[key] = use
            status = use["status"]
            if status == "cleared":
                if disposition not in {"owned", "licensed", "public-domain-with-provenance"}:
                    errors.append(f"asset {asset_id} use {use_id} is cleared from disposition {disposition}")
                if use["evidence"] is None:
                    errors.append(f"asset {asset_id} use {use_id} is cleared without evidence")
                if use["territory"] == "pending" or use["term"] == "pending":
                    errors.append(f"asset {asset_id} use {use_id} is cleared with unsettled territory or term")
                if use["promotion"] == "pending" or use["archive"] == "pending":
                    errors.append(f"asset {asset_id} use {use_id} is cleared with unsettled promotion or archive scope")
                if use["medium"] in {"press", "festival-promotion"} and use["promotion"] != "allowed":
                    errors.append(f"asset {asset_id} use {use_id} cannot serve promotion")
                if use["medium"] == "festival-archive" and use["archive"] not in {"allowed", "opt-out"}:
                    errors.append(f"asset {asset_id} use {use_id} has no archive choice")
            elif use["evidence"] is not None:
                errors.append(f"asset {asset_id} use {use_id} is {status} but carries completion evidence")
            if disposition == "blocked" and status == "cleared":
                errors.append(f"blocked asset {asset_id} has cleared use {use_id}")
            if disposition == "excluded" and status != "excluded":
                errors.append(f"excluded asset {asset_id} has non-excluded use {use_id}")
            if use["term"] == "fixed" and use["expires"] is None:
                errors.append(f"asset {asset_id} use {use_id} has a fixed term without expiry")
            if use["term"] == "fixed" and use["expires"] is not None:
                if date.fromisoformat(use["expires"]) < assessed_on:
                    errors.append(f"asset {asset_id} use {use_id} expired before the assessment date")
            if use["term"] != "fixed" and use["expires"] is not None:
                errors.append(f"asset {asset_id} use {use_id} has an expiry outside a fixed term")

    gates_by_id = {gate["id"]: gate for gate in gate_rows}
    dancer = assets.get("dancer-performance-likeness")
    dancer_gate = gates_by_id.get("dancer-release-and-credit")
    if dancer is None:
        errors.append("the performer inventory has no dancer performance/likeness disposition")
    elif dancer_gate is None:
        errors.append("the dancer performance/likeness disposition has no human gate")
    elif dancer_gate["state"] != "satisfied":
        if dancer["rights_holder"] is not None:
            errors.append("the dancer must remain unnamed until the dancer gate has a redacted receipt")
        if dancer["public_credit"]["label"] is not None:
            errors.append("the dancer public credit must remain withheld until approved")
    else:
        if dancer["private_evidence"]["state"] != "verified":
            errors.append("a satisfied dancer gate requires verified private evidence with a redacted receipt")
        if dancer["public_credit"]["state"] == "pending":
            errors.append("a satisfied dancer gate must record the approved or not-required credit disposition")

    package_text_ids: set[str] = set()
    destinations: set[str] = set()
    for row in document["package_text"]:
        if row["id"] in package_text_ids:
            errors.append(f"package text id is duplicated: {row['id']}")
        package_text_ids.add(row["id"])
        if row["destination"] in destinations:
            errors.append(f"package text destination is duplicated: {row['destination']}")
        destinations.add(row["destination"])
        if row["gate"] not in gate_ids:
            errors.append(f"package text {row['id']} names unknown gate {row['gate']}")

    package_rule_ids: set[str] = set()
    for rule in document["package_rules"]:
        if rule["id"] in package_rule_ids:
            errors.append(f"package rule id is duplicated: {rule['id']}")
        package_rule_ids.add(rule["id"])
        try:
            expression = re.compile(rule["pattern"])
            if expression.fullmatch(""):
                errors.append(f"package rule {rule['id']} matches an empty path")
        except re.error as exc:
            errors.append(f"package rule {rule['id']} has invalid regex: {exc}")
        for requirement in rule["requirements"]:
            key = (requirement["asset"], requirement["use"])
            if key not in uses:
                errors.append(f"package rule {rule['id']} names unknown asset/use {key[0]}/{key[1]}")

    release_media_ids: set[str] = set()
    for rule in document["release_rules"]:
        if rule["media_id"] in release_media_ids:
            errors.append(f"release media rule is duplicated: {rule['media_id']}")
        release_media_ids.add(rule["media_id"])
        for requirement in rule["requirements"]:
            key = (requirement["asset"], requirement["use"])
            if key not in uses:
                errors.append(f"release rule {rule['media_id']} names unknown asset/use {key[0]}/{key[1]}")

    credit_ids: set[str] = set()
    for rule in document["credit_rules"]:
        if rule["credit_id"] in credit_ids:
            errors.append(f"release credit rule is duplicated: {rule['credit_id']}")
        credit_ids.add(rule["credit_id"])
        if rule["asset"] not in assets:
            errors.append(f"credit rule {rule['credit_id']} names unknown asset {rule['asset']}")
        if rule["gate"] not in gate_ids:
            errors.append(f"credit rule {rule['credit_id']} names unknown gate {rule['gate']}")

    if document["status"] == "cleared":
        pending_gates = [gate["id"] for gate in gate_rows if gate["state"] != "satisfied"]
        blocked_uses = [f"{asset}/{use}" for (asset, use), row in uses.items() if row["status"] == "blocked"]
        if pending_gates or blocked_uses:
            errors.append("register status cannot be cleared while gates or uses remain blocked")
    return errors


def load_register(
    register_path: Path = REGISTER,
    schema_path: Path = SCHEMA,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    tracked = tracked_paths(root)
    for path, label in ((register_path, "rights register"), (schema_path, "rights schema")):
        if path.is_symlink() or not path.is_file():
            raise RightsError(f"{label} must be a regular tracked file")
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise RightsError(f"{label} must stay inside the canonical repository") from exc
        if relative not in tracked:
            raise RightsError(f"{label} must be tracked by Git")
    document = load_json(register_path, "rights register")
    schema = load_json(schema_path, "rights schema")
    errors = validate_document(document, root=root, schema=schema)
    if errors:
        raise RightsError("rights register:\n  - " + "\n  - ".join(errors))
    return document


def _asset_use_index(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (asset["id"], use["id"]): use
        for asset in document["assets"]
        for use in asset["uses"]
    }


def _requirement_blockers(
    requirements: list[dict[str, str]],
    uses: dict[tuple[str, str], dict[str, Any]],
    label: str,
) -> list[str]:
    blockers: list[str] = []
    for requirement in requirements:
        asset_id, use_id = requirement["asset"], requirement["use"]
        use = uses[(asset_id, use_id)]
        if use["status"] != "cleared":
            blockers.append(f"{label} requires {asset_id}/{use_id}, which is {use['status']}: {use['note']}")
    return blockers


def _external_root(path: Path, label: str) -> Path:
    path = path.absolute()
    if path.is_symlink() or not path.is_dir():
        raise RightsError(f"{label} must be an existing regular directory")
    return path.resolve()


def _external_file(root: Path, relative: str, label: str) -> Path:
    relative = safe_relative(relative, label, expose_value=False)
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise RightsError(f"{label} traverses a symlink")
    try:
        path = candidate.resolve(strict=True)
        path.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise RightsError(f"{label} is missing or outside the package") from exc
    if not path.is_file():
        raise RightsError(f"{label} is not a regular file")
    return path


def load_attestation(package: Path | None) -> tuple[dict[str, Any], list[str]]:
    if package is None:
        return {}, []
    blockers: list[str] = []
    try:
        root = _external_root(package, "package")
        path = _external_file(root, "attest.yaml", "package attestation")
        value = load_yaml(path, "package attestation", expose_path=False)
    except RightsError as exc:
        blockers.append(str(exc))
        return {}, blockers
    return value, blockers


def validate_attestation(
    document: dict[str, Any],
    attestation: dict[str, Any],
    *,
    root: Path = ROOT,
) -> list[str]:
    """Reject unknown or ill-typed package assertions without echoing their values."""
    blockers: list[str] = []
    contracts = {
        gate["attestation"]["key"]: gate["attestation"]
        for gate in document["human_gates"]
        if gate["attestation"] is not None
    }
    try:
        submission_path = regular_file(
            root,
            document["bindings"]["submission"]["source"]["path"],
            "submission binding",
        )
        submission = load_yaml(submission_path, "submission binding")
    except RightsError as exc:
        return [str(exc)]
    for section in ("requirements", "approvals"):
        for row in submission.get(section, []):
            if not isinstance(row, dict) or row.get("check") != "manual" or not isinstance(row.get("id"), str):
                continue
            choices = row.get("choices")
            contract = {
                "kind": "choice" if isinstance(choices, list) and choices else "boolean",
                "values": choices if isinstance(choices, list) and choices else [True],
            }
            existing = contracts.get(row["id"])
            if existing is not None and (
                existing["kind"] != contract["kind"] or existing["values"] != contract["values"]
            ):
                blockers.append(f"attestation contract disagrees for registered gate {row['id']}")
            contracts.setdefault(row["id"], contract)

    unknown = [key for key in attestation if not isinstance(key, str) or key not in contracts]
    if unknown:
        blockers.append(f"package attestation contains {len(unknown)} unknown key(s)")
    for key, record in contracts.items():
        if key not in attestation or attestation[key] is None:
            continue
        value = attestation[key]
        if record["kind"] == "boolean":
            if type(value) is not bool:
                blockers.append(f"package attestation {key} must be boolean or null")
        elif not isinstance(value, str) or value not in record["values"]:
            blockers.append(f"package attestation {key} must be one registered choice or null")
    return blockers


def gate_satisfied(gate: dict[str, Any], attestation: dict[str, Any], *, allow_attestation: bool) -> bool:
    if gate["state"] == "satisfied":
        return True
    if gate["state"] == "rejected":
        return False
    record = gate["attestation"]
    if not allow_attestation or record is None:
        return False
    value = attestation.get(record["key"])
    if record["kind"] == "boolean":
        # bool is a subclass of int in Python: membership alone would let the
        # YAML integer ``1`` satisfy a human-authored ``true`` gate.
        return value is True and any(candidate is True for candidate in record["values"])
    return isinstance(value, str) and value in record["values"]


def validate_package(
    document: dict[str, Any],
    package: Path,
    *,
    root: Path = ROOT,
) -> tuple[list[str], dict[str, Any] | None]:
    blockers: list[str] = []
    uses = _asset_use_index(document)
    try:
        package_root = _external_root(package, "package")
        manifest_path = _external_file(package_root, "manifest.json", "package manifest")
        manifest = load_json(manifest_path, "package manifest", expose_path=False)
    except RightsError as exc:
        return [str(exc)], None

    if manifest.get("schema") != "danse.delivery.manifest.v1":
        blockers.append("package manifest schema is not danse.delivery.manifest.v1")
    if not isinstance(manifest.get("source_tree_sha256"), str) or not HEX64.fullmatch(manifest["source_tree_sha256"]):
        blockers.append("package manifest has no exact source-tree SHA-256")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        blockers.append("package manifest has no items")
        items = []

    rules = [(rule, re.compile(rule["pattern"])) for rule in document["package_rules"]]
    item_names: set[str] = set()
    moving = {"master.mov", "midnight-moment.mov", "trailer.mp4", "screener.mp4", "reel.mp4"}
    submission = load_yaml(regular_file(root, document["bindings"]["submission"]["source"]["path"], "submission binding"), "submission binding")
    expected_audio = sorted(((submission.get("package") or {}).get("audio") or {}).get("source_recordings") or [])
    expected_origin = (((submission.get("package") or {}).get("origin_still") or {}).get("source_sha256"))

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            blockers.append(f"package manifest item {index} is not a record")
            continue
        try:
            name = safe_relative(
                item.get("name"),
                f"package manifest item {index} name",
                expose_value=False,
            )
        except RightsError as exc:
            blockers.append(str(exc))
            continue
        if name in item_names:
            blockers.append(f"package manifest repeats item {name}")
            continue
        item_names.add(name)
        matched = [rule for rule, expression in rules if expression.fullmatch(name)]
        public_label = name if len(matched) == 1 else f"manifest item {index}"
        try:
            path = _external_file(package_root, name, f"package {public_label}")
            expected_digest = item.get("sha256")
            if not isinstance(expected_digest, str) or not HEX64.fullmatch(expected_digest):
                blockers.append(f"package {public_label} has no valid SHA-256")
            elif sha256(path) != expected_digest:
                blockers.append(f"package {public_label} digest does not match its manifest")
            if item.get("bytes") != path.stat().st_size:
                blockers.append(f"package {public_label} byte count does not match its manifest")
        except RightsError as exc:
            blockers.append(str(exc))

        if len(matched) != 1:
            blockers.append(f"package manifest item {index} matches {len(matched)} rights rules; exactly one is required")
        else:
            blockers.extend(_requirement_blockers(matched[0]["requirements"], uses, f"package item {name}"))

        if name in moving:
            sound = item.get("sound")
            if not isinstance(sound, dict):
                blockers.append(f"package audio item {name} has no score/source provenance")
            else:
                if sorted(sound.get("sources") or []) != expected_audio:
                    blockers.append(f"package audio item {name} does not name the exact registered recordings")
                if not isinstance(sound.get("score_sha256"), str) or not HEX64.fullmatch(sound["score_sha256"]):
                    blockers.append(f"package audio item {name} has no exact score digest")
                if not isinstance(sound.get("bank_fingerprint"), str) or not sound["bank_fingerprint"]:
                    blockers.append(f"package audio item {name} has no bank fingerprint")
        if name == "stills/origin-2017.jpg" and (
            item.get("source_sha256") != expected_origin or item.get("copy_mode") != "byte-identical"
        ):
            blockers.append("package origin still is not bound byte-identically to its registered source")

    for directory, dirnames, filenames in __import__("os").walk(package_root, topdown=True, followlinks=False):
        base = Path(directory)
        for name in list(dirnames):
            candidate = base / name
            if candidate.is_symlink():
                blockers.append("package contains a symlink directory")
                dirnames.remove(name)
        for name in filenames:
            candidate = base / name
            relative = candidate.relative_to(package_root).as_posix()
            if candidate.is_symlink():
                blockers.append("package contains a symlink file")
            elif candidate.suffix.lower() in RIGHTS_MEDIA_SUFFIXES and relative not in item_names:
                blockers.append("rights-bearing package media is absent from the manifest")

    for binding in document["package_text"]:
        destination = binding["destination"]
        try:
            staged = _external_file(package_root, destination, f"package text {binding['id']}")
            if sha256(staged) != binding["source"]["sha256"]:
                blockers.append(f"package text {binding['id']} does not match its tracked source")
        except RightsError as exc:
            blockers.append(str(exc))

    identity = {
        "schema": manifest.get("schema"),
        "sha256": sha256(manifest_path),
        "items": len(items),
    }
    return blockers, identity


def _verify_release_source(
    root: Path,
    source: object,
    label: str,
    *,
    tracked: set[str],
    require_tracked: bool,
) -> list[str]:
    if not isinstance(source, dict):
        return [f"{label} has no source record"]
    try:
        relative = safe_relative(source.get("path"), f"{label} source", expose_value=False)
    except RightsError as exc:
        return [str(exc)]
    if require_tracked and relative not in tracked:
        return [f"{label} source is not tracked public-safe evidence"]
    try:
        path = regular_file(root, relative, f"{label} source", expose_value=False)
    except RightsError as exc:
        return [str(exc)]
    expected = source.get("sha256")
    if not isinstance(expected, str) or not HEX64.fullmatch(expected) or sha256(path) != expected:
        return [f"{label} source digest is missing or stale"]
    return []


def validate_release_manifest(
    document: dict[str, Any],
    release_manifest: Path,
    phase: str,
    *,
    root: Path = ROOT,
    register_path: Path = REGISTER,
) -> tuple[list[str], dict[str, Any] | None]:
    blockers: list[str] = []
    try:
        tracked = tracked_paths(root)
    except RightsError as exc:
        return [str(exc)], None
    try:
        if release_manifest.is_symlink() or not release_manifest.is_file():
            raise RightsError("release manifest must be an existing regular file")
        manifest = load_json(release_manifest, "release manifest", expose_path=False)
    except RightsError as exc:
        return [str(exc)], None
    if manifest.get("schema") != "danse.release.v1":
        blockers.append("release manifest schema is not danse.release.v1")
    required_status = {"public-approved", "released"} if phase == "public" else {"released"}
    if manifest.get("status") not in required_status:
        blockers.append(f"release manifest status {manifest.get('status')!r} is not valid for {phase}")

    uses = _asset_use_index(document)
    release_rules = {row["media_id"]: row for row in document["release_rules"]}
    media_rows = manifest.get("media")
    if not isinstance(media_rows, list):
        blockers.append("release manifest has no media inventory")
        media_rows = []
    media_ids: set[str] = set()
    for row in media_rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            blockers.append("release manifest contains malformed media")
            continue
        media_id = row["id"]
        if media_id in media_ids:
            blockers.append(f"release manifest repeats media id {media_id}")
            continue
        media_ids.add(media_id)
        rule = release_rules.get(media_id)
        if rule is None:
            blockers.append(f"release media {media_id} has no rights rule")
            continue
        declared_phases = row.get("required_for")
        if not isinstance(declared_phases, list) or sorted(declared_phases) != sorted(rule["required_for"]):
            blockers.append(f"release media {media_id} phase scope disagrees with its rights rule")
            continue
        if phase not in rule["required_for"]:
            continue
        blockers.extend(_requirement_blockers(rule["requirements"], uses, f"release media {media_id}"))
        if row.get("status") != "ready":
            blockers.append(f"release media {media_id} is not ready")
        clearance = row.get("clearance") if isinstance(row.get("clearance"), dict) else {}
        if clearance.get("status") != "cleared":
            blockers.append(f"release media {media_id} clearance is not cleared")
        blockers.extend(
            _verify_release_source(
                root,
                row.get("source"),
                f"release media {media_id}",
                tracked=tracked,
                require_tracked=False,
            )
        )
        blockers.extend(
            _verify_release_source(
                root,
                clearance.get("evidence"),
                f"release media {media_id} clearance",
                tracked=tracked,
                require_tracked=True,
            )
        )
    missing_media = sorted(set(release_rules) - media_ids)
    if missing_media:
        blockers.append(f"release manifest is missing rights-ruled media: {', '.join(missing_media)}")

    credit_rules = {row["credit_id"]: row for row in document["credit_rules"]}
    credit_rows = manifest.get("credits")
    if not isinstance(credit_rows, list):
        blockers.append("release manifest has no credit inventory")
        credit_rows = []
    credit_ids: set[str] = set()
    gates = {gate["id"]: gate for gate in document["human_gates"]}
    assets = {asset["id"]: asset for asset in document["assets"]}
    for row in credit_rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            blockers.append("release manifest contains malformed credit")
            continue
        credit_id = row["id"]
        if credit_id in credit_ids:
            blockers.append(f"release manifest repeats credit id {credit_id}")
            continue
        credit_ids.add(credit_id)
        rule = credit_rules.get(credit_id)
        if rule is None:
            blockers.append(f"release credit {credit_id} has no rights rule")
            continue
        if row.get("status") != "cleared" or not row.get("name"):
            blockers.append(f"release credit {credit_id} is not cleared and named")
        blockers.extend(
            _verify_release_source(
                root,
                row.get("evidence"),
                f"release credit {credit_id}",
                tracked=tracked,
                require_tracked=True,
            )
        )
        if gates[rule["gate"]]["state"] != "satisfied":
            blockers.append(f"release credit {credit_id} depends on pending gate {rule['gate']}")
        if assets[rule["asset"]]["public_credit"]["state"] != "approved":
            blockers.append(f"release credit {credit_id} depends on unapproved asset credit {rule['asset']}")
    missing_credits = sorted(set(credit_rules) - credit_ids)
    if missing_credits:
        blockers.append(f"release manifest is missing rights-ruled credits: {', '.join(missing_credits)}")

    release_gates: dict[str, dict[str, Any]] = {}
    gate_rows = manifest.get("gates")
    if not isinstance(gate_rows, list):
        blockers.append("release manifest has no gate inventory")
        gate_rows = []
    for row in gate_rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            blockers.append("release manifest contains malformed gate")
            continue
        gate_id = row["id"]
        if gate_id in release_gates:
            blockers.append(f"release manifest repeats gate id {gate_id}")
            continue
        release_gates[gate_id] = row
    rights_gate = release_gates.get("rights-register")
    if not rights_gate or rights_gate.get("state") != "satisfied":
        blockers.append("release manifest rights-register gate is not satisfied")
    elif phase not in (rights_gate.get("required_for") or []):
        blockers.append(f"release manifest rights-register gate is not required for {phase}")
    else:
        evidence = rights_gate.get("evidence")
        expected_path = register_path.resolve()
        expected_digest = sha256(register_path)
        if not isinstance(evidence, dict):
            blockers.append("release manifest rights-register gate has no evidence")
        else:
            try:
                evidence_path = regular_file(
                    root,
                    evidence.get("path"),
                    "release rights-register evidence",
                    expose_value=False,
                )
                if evidence_path != expected_path or evidence.get("sha256") != expected_digest:
                    blockers.append("release manifest does not bind this exact rights register")
            except RightsError as exc:
                blockers.append(str(exc))

    identity = {
        "schema": manifest.get("schema"),
        "sha256": sha256(release_manifest),
        "release_id": manifest.get("release_id"),
    }
    return blockers, identity


def phase_blockers(
    document: dict[str, Any],
    phase: str,
    *,
    package: Path | None = None,
    release_manifest: Path | None = None,
    root: Path = ROOT,
    register_path: Path = REGISTER,
) -> tuple[list[str], dict[str, Any]]:
    if phase not in PHASES:
        raise RightsError(f"unknown rights phase {phase!r}")
    inputs: dict[str, Any] = {}
    if phase == "draft":
        return [], inputs

    scopes = set(PHASE_SCOPES[phase])
    blockers: list[str] = []
    if document["status"] == "draft":
        blockers.append(f"rights register status is draft; {phase} requires reviewed evidence")
    if phase == "release" and document["status"] != "cleared":
        blockers.append(f"rights register status is {document['status']}; release requires cleared")

    attestation, attestation_blockers = load_attestation(package)
    if scopes & {"package", "uploaded", "submitted"}:
        blockers.extend(attestation_blockers)
        if not attestation_blockers:
            blockers.extend(validate_attestation(document, attestation, root=root))
    allow_attestation = phase in {"package", "uploaded", "submitted"}
    for gate in document["human_gates"]:
        if scopes.intersection(gate["required_for"]) and not gate_satisfied(
            gate, attestation, allow_attestation=allow_attestation
        ):
            blockers.append(f"human gate {gate['id']} is {gate['state']}: {gate['note']}")

    for asset in document["assets"]:
        for use in asset["uses"]:
            if not scopes.intersection(use["required_for"]):
                continue
            if use["status"] == "cleared":
                continue
            if use["status"] == "excluded" and asset["disposition"] == "excluded":
                continue
            blockers.append(f"asset use {asset['id']}/{use['id']} is {use['status']}: {use['note']}")

    if "package" in scopes:
        if package is None:
            blockers.append(f"{phase} requires --package with an exact delivery manifest")
        else:
            package_blockers, identity = validate_package(document, package, root=root)
            blockers.extend(package_blockers)
            if identity is not None:
                inputs["package_manifest"] = identity

    if phase in {"public", "release"}:
        if release_manifest is None:
            blockers.append(f"{phase} requires --release-manifest with an exact release inventory")
        else:
            release_blockers, identity = validate_release_manifest(
                document,
                release_manifest,
                phase,
                root=root,
                register_path=register_path,
            )
            blockers.extend(release_blockers)
            if identity is not None:
                inputs["release_manifest"] = identity
    return sorted(set(blockers)), inputs


def build_receipt(
    document: dict[str, Any],
    register_path: Path,
    schema_path: Path,
    phase: str,
    blockers: list[str],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "danse.rights.receipt.v1",
        "phase": phase,
        "status": "ready" if not blockers else "blocked",
        "register": {
            "schema": document["schema"],
            "sha256": sha256(register_path),
            "schema_sha256": sha256(schema_path),
            "assessment_date": document["assessment"]["date"],
            "status": document["status"],
        },
        "inventory": {
            "assets": len(document["assets"]),
            "human_gates": len(document["human_gates"]),
            "package_rules": len(document["package_rules"]),
            "release_rules": len(document["release_rules"]),
        },
        "inputs": inputs,
        "blockers": blockers,
    }


def validate_all(
    *,
    register_path: Path = REGISTER,
    schema_path: Path = SCHEMA,
    phase: str = "draft",
    package: Path | None = None,
    release_manifest: Path | None = None,
    root: Path = ROOT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    document = load_register(register_path, schema_path, root=root)
    blockers, inputs = phase_blockers(
        document,
        phase,
        package=package,
        release_manifest=release_manifest,
        root=root,
        register_path=register_path,
    )
    return document, build_receipt(document, register_path, schema_path, phase, blockers, inputs)


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=REGISTER)
    parser.add_argument("--schema", type=Path, default=SCHEMA)
    parser.add_argument("--phase", choices=PHASES, default="draft")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--receipt", type=Path, help="write a deterministic, redacted validation receipt")
    parser.add_argument("--json", action="store_true", help="print the complete receipt as JSON")
    args = parser.parse_args(argv)

    try:
        _, receipt = validate_all(
            register_path=args.register,
            schema_path=args.schema,
            phase=args.phase,
            package=args.package,
            release_manifest=args.release_manifest,
        )
    except RightsError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(canonical_json(receipt), encoding="utf-8")
    if args.json:
        print(canonical_json(receipt), end="")
    else:
        print("Danse rights and attribution")
        print(f"  register  {receipt['register']['sha256']}")
        print(
            f"  inventory {receipt['inventory']['assets']} assets · "
            f"{receipt['inventory']['human_gates']} human gates · "
            f"{receipt['inventory']['package_rules']} package rules · "
            f"{receipt['inventory']['release_rules']} release rules"
        )
        print(f"  phase     {args.phase} — {receipt['status'].upper()}")
        for blocker in receipt["blockers"]:
            print(f"  [BLOCK] {blocker}")
        if not receipt["blockers"]:
            print("  [ok] exact public-safe sources, redaction, inventory, and rule graph validate")
    return 0 if receipt["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())

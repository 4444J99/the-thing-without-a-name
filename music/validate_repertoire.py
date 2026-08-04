#!/usr/bin/env python3
"""Validate Danse's layered music rights/provenance register.

The JSON Schema describes the interchange shape.  This dependency-free semantic
validator additionally resolves tracked paths, verifies SHA-256 bytes, and keeps
composition status from being mistaken for edition, MIDI, performance,
recording, or sample clearance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTER = ROOT / "music" / "repertoire.yaml"
DEFAULT_SCHEMA = ROOT / "music" / "repertoire.schema.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LAYERS = ("composition", "edition", "arrangement_midi", "performance", "recording", "samples")
RIGHTS_STATUSES = {
    "absent",
    "fixture-only",
    "project-authored",
    "public-domain",
    "licensed",
    "restricted",
    "unverified",
    "not-applicable",
}
SELECTABLE = {
    "composition": {"project-authored", "public-domain", "licensed"},
    "edition": {"not-applicable", "project-authored", "public-domain", "licensed"},
    "arrangement_midi": {"project-authored", "licensed"},
    "performance": {"project-authored", "licensed"},
    "recording": {"project-authored", "licensed"},
    "samples": {"none", "project-authored", "licensed"},
}
VISUAL_CHANNELS = {"divergence", "spread", "azimuth", "elevation", "projK", "turnover"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_register(path: Path = DEFAULT_REGISTER) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    return data if isinstance(data, dict) else {}


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def validate_document(
    register: dict[str, Any],
    *,
    root: Path = ROOT,
    check_derived: bool = True,
) -> list[str]:
    errors: list[str] = []

    def error(location: str, message: str) -> None:
        errors.append(f"{location}: {message}")

    def mapping(value: Any, location: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            error(location, "must be a mapping")
            return {}
        return value

    def evidence(layer: dict[str, Any], location: str) -> None:
        rows = layer.get("evidence")
        if not isinstance(rows, list) or not rows:
            error(f"{location}.evidence", "must contain at least one evidence record")
            return
        for index, row in enumerate(rows):
            row = mapping(row, f"{location}.evidence[{index}]")
            if not isinstance(row.get("kind"), str) or not row["kind"].strip():
                error(f"{location}.evidence[{index}].kind", "must be non-empty")
            if not isinstance(row.get("citation"), str) or not row["citation"].strip():
                error(f"{location}.evidence[{index}].citation", "must be non-empty")

    def source(value: Any, location: str, *, required: bool) -> tuple[str, str] | None:
        if value is None:
            if required:
                error(location, "is required")
            return None
        row = mapping(value, location)
        relative = row.get("path")
        digest = row.get("sha256")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            error(f"{location}.path", "must be a non-empty repository-relative path")
            return None
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            error(f"{location}.sha256", "must be a lowercase SHA-256 digest")
            return None
        candidate = root / relative
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            error(f"{location}.path", f"does not resolve to a tracked source: {relative}")
            return relative, digest
        if candidate.is_symlink() or not candidate.is_file() or not _inside(root.resolve(), resolved):
            error(f"{location}.path", "must be a regular file inside the repository")
            return relative, digest
        actual = sha256(candidate)
        if actual != digest:
            error(f"{location}.sha256", f"declares {digest}, actual {actual}")
        return relative, digest

    if register.get("schema") != "danse.music.repertoire.v1":
        error("schema", "must be danse.music.repertoire.v1")
    gate = mapping(register.get("artistic_gate"), "artistic_gate")
    if gate.get("status") not in {"pending", "accepted", "rejected"}:
        error("artistic_gate.status", "must be pending, accepted, or rejected")
    if not isinstance(gate.get("authority"), str) or not gate["authority"].strip():
        error("artistic_gate.authority", "must name the human authority")
    if not isinstance(gate.get("note"), str) or not gate["note"].strip():
        error("artistic_gate.note", "must explain the gate")
    if gate.get("status") == "accepted" and not gate.get("evidence"):
        error("artistic_gate.evidence", "is required when the gate is accepted")

    works = register.get("works")
    if not isinstance(works, list) or not works:
        error("works", "must contain at least one work")
        return errors
    seen: set[str] = set()
    for index, candidate in enumerate(works):
        location = f"works[{index}]"
        work = mapping(candidate, location)
        work_id = work.get("id")
        if not isinstance(work_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", work_id):
            error(f"{location}.id", "must be a stable lowercase identifier")
            work_id = f"index-{index}"
        if work_id in seen:
            error(f"{location}.id", "must be unique")
        seen.add(work_id)
        if work.get("role") not in {"fixture", "candidate", "repertoire"}:
            error(f"{location}.role", "must be fixture, candidate, or repertoire")

        selection = mapping(work.get("selection"), f"{location}.selection")
        selection_status = selection.get("status")
        if selection_status not in {"not-selected", "pending", "selected", "rejected"}:
            error(f"{location}.selection.status", "has an unknown status")
        if not isinstance(selection.get("authority"), str) or not selection["authority"].strip():
            error(f"{location}.selection.authority", "must name the human authority")
        if work.get("role") == "fixture" and selection_status == "selected":
            error(f"{location}.selection.status", "a contract fixture cannot be selected as repertoire")

        layer_rows: dict[str, dict[str, Any]] = {}
        for layer_name in LAYERS:
            layer = mapping(work.get(layer_name), f"{location}.{layer_name}")
            layer_rows[layer_name] = layer
            status = layer.get("status")
            allowed = RIGHTS_STATUSES | ({"none"} if layer_name == "samples" else set())
            if status not in allowed:
                error(f"{location}.{layer_name}.status", f"unknown rights status {status!r}")
            evidence(layer, f"{location}.{layer_name}")

        composition = layer_rows["composition"]
        for field in ("title", "composer", "date"):
            if composition.get(field) in (None, ""):
                error(f"{location}.composition.{field}", "is required")
        arrangement = layer_rows["arrangement_midi"]
        performance = layer_rows["performance"]
        arrangement_source = source(
            arrangement.get("source"),
            f"{location}.arrangement_midi.source",
            required=arrangement.get("status") not in {"absent", "unverified"},
        )
        performance_source = source(
            performance.get("source"),
            f"{location}.performance.source",
            required=performance.get("status") not in {"absent", "unverified"},
        )
        for layer_name in ("composition", "edition", "recording"):
            layer = layer_rows[layer_name]
            source(layer.get("source"), f"{location}.{layer_name}.source", required=False)

        samples = layer_rows["samples"]
        items = samples.get("items")
        if not isinstance(items, list):
            error(f"{location}.samples.items", "must be a list")
        else:
            if samples.get("status") == "none" and items:
                error(f"{location}.samples.items", "must be empty when sample status is none")
            for item_index, item_value in enumerate(items):
                item = mapping(item_value, f"{location}.samples.items[{item_index}]")
                source(item.get("source"), f"{location}.samples.items[{item_index}].source", required=True)
                if not item.get("license"):
                    error(f"{location}.samples.items[{item_index}].license", "is required")

        score = mapping(work.get("score"), f"{location}.score")
        midi_source = source(score.get("source_midi"), f"{location}.score.source_midi", required=True)
        if arrangement_source and midi_source and arrangement_source != midi_source:
            error(f"{location}.score.source_midi", "must identify the exact arrangement/MIDI bytes")
        if performance_source and midi_source and performance_source != midi_source:
            error(f"{location}.performance.source", "must identify the exact performed MIDI bytes")
        bindings = score.get("cue_bindings")
        if not isinstance(bindings, dict):
            error(f"{location}.score.cue_bindings", "must be a mapping")
        else:
            for cue_id, binding_value in bindings.items():
                binding = mapping(binding_value, f"{location}.score.cue_bindings.{cue_id}")
                if not isinstance(binding.get("window_beats"), (int, float)) or binding["window_beats"] <= 0:
                    error(f"{location}.score.cue_bindings.{cue_id}.window_beats", "must be positive")
                visual = mapping(binding.get("visual"), f"{location}.score.cue_bindings.{cue_id}.visual")
                unknown = set((visual.get("channel_offsets") or {})) - VISUAL_CHANNELS
                if unknown:
                    error(
                        f"{location}.score.cue_bindings.{cue_id}.visual.channel_offsets",
                        f"unknown channel(s): {', '.join(sorted(unknown))}",
                    )

        roles = work.get("dramatic_roles")
        if not isinstance(roles, list) or not roles or any(not isinstance(role, str) or not role for role in roles):
            error(f"{location}.dramatic_roles", "must name one or more program movements")
        elif len(set(roles)) != len(roles):
            error(f"{location}.dramatic_roles", "must not repeat a movement")

        recording_status = layer_rows["recording"].get("status")
        if composition.get("status") == "public-domain" and recording_status in {"restricted", "unverified"}:
            error(
                f"{location}.recording.status",
                "public-domain composition status does not clear a restricted or unverified recording",
            )
        if selection_status == "selected":
            if gate.get("status") != "accepted" or not selection.get("evidence"):
                error(f"{location}.selection", "selected repertoire requires the accepted human gate and evidence")
            for layer_name, permitted in SELECTABLE.items():
                status = layer_rows[layer_name].get("status")
                if status not in permitted:
                    error(
                        f"{location}.{layer_name}.status",
                        f"selected repertoire requires one of {', '.join(sorted(permitted))}; got {status!r}",
                    )

        derived = work.get("derived_artifacts")
        if not isinstance(derived, list):
            error(f"{location}.derived_artifacts", "must be a list")
        elif check_derived:
            for artifact_index, artifact_value in enumerate(derived):
                artifact = mapping(artifact_value, f"{location}.derived_artifacts[{artifact_index}]")
                source(
                    {"path": artifact.get("path"), "sha256": artifact.get("sha256")},
                    f"{location}.derived_artifacts[{artifact_index}]",
                    required=True,
                )

    return errors


def validate_schema_document(path: Path = DEFAULT_SCHEMA) -> list[str]:
    try:
        schema = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"schema document: {exc}"]
    errors = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("schema document: must declare JSON Schema draft 2020-12")
    if schema.get("properties", {}).get("schema", {}).get("const") != "danse.music.repertoire.v1":
        errors.append("schema document: does not bind danse.music.repertoire.v1")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("register", nargs="?", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--allow-stale-derived", action="store_true", help="skip derived artifact byte checks")
    args = parser.parse_args()
    try:
        register = load_register(args.register)
    except (OSError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    errors = [*validate_schema_document(), *validate_document(register, check_derived=not args.allow_stale_derived)]
    if errors:
        for row in errors:
            print(f"FAIL: {row}")
        return 1
    print(f"ok: {args.register.relative_to(ROOT)} ({len(register['works'])} work(s); artistic gate {register['artistic_gate']['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

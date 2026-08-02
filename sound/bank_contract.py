#!/usr/bin/env python3
"""Dependency-free validation for Danse's private grain-bank index.

The bank is ignored because its WAV payloads come from private recordings, but
every consumer must agree on what makes that local artifact usable. Keeping the
contract here lets preflight, the score, and the portable invariant runner ask
the same question without importing NumPy or SciPy.
"""

from __future__ import annotations

import json
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "danse.sound.bank.v1"
RATE = 48_000
KINDS = ("bed", "sustained", "transient")
AXES = ("centroid", "brightness", "flatness", "decay", "attack", "zcr")


def wav_contract(path: Path) -> str | None:
    """Return a dependency-free payload error, or ``None`` for usable WAV bytes."""
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                return "not a RIFF/WAVE file"
            fmt: tuple[int, int, int, int] | None = None
            data_size = 0
            while chunk := handle.read(8):
                if len(chunk) != 8:
                    return "truncated chunk header"
                chunk_id, size = struct.unpack("<4sI", chunk)
                payload = handle.read(size)
                if len(payload) != size:
                    return f"truncated {chunk_id.decode(errors='replace')} chunk"
                if size % 2:
                    handle.read(1)
                if chunk_id == b"fmt " and size >= 16:
                    audio_format, channels, rate, _, block_align, _ = struct.unpack("<HHIIHH", payload[:16])
                    fmt = audio_format, channels, rate, block_align
                elif chunk_id == b"data":
                    data_size += size
    except OSError as exc:
        return f"unreadable: {exc}"
    if fmt is None:
        return "missing fmt chunk"
    audio_format, channels, rate, block_align = fmt
    if audio_format not in {1, 3, 0xFFFE}:
        return f"unsupported WAV format {audio_format}"
    if channels < 1 or block_align < 1:
        return "invalid channel/block alignment"
    if rate != RATE:
        return f"sample rate {rate}, expected {RATE}"
    if data_size < block_align:
        return "audio payload is empty"
    return None


@dataclass(frozen=True)
class BankAudit:
    sources: tuple[str, ...] = ()
    fingerprint: str | None = None
    grain_count: int = 0
    provenance_errors: tuple[str, ...] = ()
    index_errors: tuple[str, ...] = ()
    payload_errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not (self.provenance_errors or self.index_errors or self.payload_errors)

    def summary(self) -> str:
        errors = self.provenance_errors + self.index_errors + self.payload_errors
        return "; ".join(errors[:4]) if errors else f"{self.grain_count} grains from {len(self.sources)} sources"


def audit_bank(index: Path, expected_sources: list[str] | tuple[str, ...] | None = None) -> BankAudit:
    """Return every structural, provenance, and payload failure in one pass."""
    if not index.is_file():
        return BankAudit(index_errors=(f"missing {index}",))
    try:
        data = json.loads(index.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return BankAudit(index_errors=(f"unreadable bank index: {exc}",))
    if not isinstance(data, dict):
        return BankAudit(index_errors=("bank index is not an object",))

    provenance: list[str] = []
    structural: list[str] = []
    payload: list[str] = []

    if data.get("schema") != SCHEMA:
        structural.append(f"schema {data.get('schema')!r}, expected {SCHEMA}")
    if data.get("rate") != RATE:
        structural.append(f"rate {data.get('rate')!r}, expected {RATE}")

    fingerprint = data.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        structural.append("fingerprint is missing")
        fingerprint = None

    source_rows = data.get("sources")
    if not isinstance(source_rows, list):
        source_rows = []
        provenance.append("sources is not a list")
    sources = tuple(
        row["name"]
        for row in source_rows
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]
    )
    if len(sources) != len(source_rows) or len(set(sources)) != len(sources):
        provenance.append("source names are missing or duplicated")
    if expected_sources is not None and sorted(sources) != sorted(expected_sources):
        provenance.append("bank sources do not match the submission register")

    grains = data.get("grains")
    if not isinstance(grains, list) or not grains:
        return BankAudit(
            sources=sources,
            fingerprint=fingerprint,
            provenance_errors=tuple(provenance),
            index_errors=tuple(structural + ["grains is not a non-empty list"]),
        )

    ids: list[str] = []
    kinds: Counter[str] = Counter()
    stray_sources: set[str] = set()
    values: dict[str, set[float]] = {axis: set() for axis in AXES}
    malformed = 0
    bank_root = index.parent
    for grain in grains:
        if not isinstance(grain, dict):
            malformed += 1
            continue
        grain_id = grain.get("id")
        source = grain.get("source")
        kind = grain.get("kind")
        if not isinstance(grain_id, str) or not grain_id:
            malformed += 1
        else:
            ids.append(grain_id)
            grain_path = bank_root / f"{grain_id}.wav"
            if not grain_path.is_file():
                payload.append(f"missing {grain_id}.wav")
            elif error := wav_contract(grain_path):
                payload.append(f"invalid {grain_id}.wav: {error}")
        if not isinstance(source, str) or source not in sources:
            stray_sources.add(str(source))
        if isinstance(kind, str):
            kinds[kind] += 1
        else:
            malformed += 1
        for axis in AXES:
            value = grain.get(axis)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[axis].add(float(value))
            else:
                malformed += 1

    if malformed:
        structural.append(f"{malformed} malformed grain field(s)")
    if len(ids) != len(set(ids)):
        structural.append("grain ids are duplicated")
    missing_kinds = [kind for kind in KINDS if kinds[kind] == 0]
    unknown_kinds = sorted(set(kinds) - set(KINDS))
    if missing_kinds:
        structural.append(f"empty pool(s): {', '.join(missing_kinds)}")
    if unknown_kinds:
        structural.append(f"unknown pool(s): {', '.join(unknown_kinds)}")
    floor = max(8, len(grains) // 10)
    flat = [f"{axis} has {len(values[axis])}" for axis in AXES if len(values[axis]) < floor]
    if flat:
        structural.append("descriptor spread too small: " + ", ".join(flat))
    if stray_sources:
        provenance.append("unregistered grain source(s): " + ", ".join(sorted(stray_sources)))

    return BankAudit(
        sources=sources,
        fingerprint=fingerprint,
        grain_count=len(grains),
        provenance_errors=tuple(provenance),
        index_errors=tuple(structural),
        payload_errors=tuple(payload),
    )

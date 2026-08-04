#!/usr/bin/env python3
"""Generate the small, project-authored MIDI contract fixture.

This is test material, not a repertoire choice or a musical work approved for
release.  Its 390-second span mirrors the nominal shares in render/program.json
so fixture tests can exercise every movement without changing the artwork.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "music" / "fixtures" / "generated-study.mid"
TPQ = 480
TOTAL_BEATS = 780

MOVEMENTS = (
    (0, "ONE"),
    (80, "ASSEMBLY"),
    (130, "DIVISION"),
    (240, "PHRASE"),
    (450, "STILLNESS"),
    (570, "RESEED"),
    (772, "SIGNATURE"),
)

PHRASES = (
    (0, "origin"),
    (80, "assembly"),
    (130, "division"),
    (240, "countable"),
    (450, "stillness"),
    (570, "reseed"),
    (772, "signature"),
)

CUES = (
    (0, "origin-entry", "cue", 100),
    (130, "division-entry", "cue", 100),
    (240, "phrase-entry", "cue", 100),
    (256, "phrase-accent-a", "accent", 96),
    (264, "phrase-accent-b", "accent", 112),
    (272, "phrase-accent-c", "accent", 127),
    (450, "stillness-entry", "cue", 72),
    (570, "reseed-entry", "cue", 110),
    (616, "reseed-accent-a", "accent", 116),
    (680, "reseed-accent-b", "accent", 124),
    (772, "signature-entry", "cue", 64),
)

DYNAMICS = (
    (0, 48),
    (80, 56),
    (130, 76),
    (240, 104),
    (450, 36),
    (570, 92),
    (772, 24),
)


def variable_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("MIDI delta cannot be negative")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def meta(kind: int, payload: bytes) -> bytes:
    return bytes((0xFF, kind)) + variable_length(len(payload)) + payload


def text_meta(kind: int, value: str) -> bytes:
    return meta(kind, value.encode("ascii"))


def make_track(events: list[tuple[int, bytes]]) -> bytes:
    body = bytearray()
    previous = 0
    for _, (tick, event) in sorted(enumerate(events), key=lambda item: (item[1][0], item[0])):
        body.extend(variable_length(tick - previous))
        body.extend(event)
        previous = tick
    return b"MTrk" + struct.pack(">I", len(body)) + body


def fixture_bytes() -> bytes:
    conductor: list[tuple[int, bytes]] = [
        (0, text_meta(0x03, "Danse generated contract fixture")),
        (0, meta(0x51, (500_000).to_bytes(3, "big"))),
        (0, meta(0x58, bytes((4, 2, 24, 8)))),
    ]
    for beat, movement in MOVEMENTS:
        conductor.append((beat * TPQ, text_meta(0x06, f"movement:{movement}")))
    for beat, phrase in PHRASES:
        conductor.append((beat * TPQ, text_meta(0x06, f"phrase:{phrase}")))
    for beat, cue_id, kind, strength in CUES:
        conductor.append((beat * TPQ, text_meta(0x06, f"cue:{cue_id}:{kind}:{strength}")))
    conductor.append((TOTAL_BEATS * TPQ, meta(0x2F, b"")))

    voice: list[tuple[int, bytes]] = [
        (0, text_meta(0x03, "fixture-piano")),
        (0, bytes((0xC0, 0))),
    ]
    for beat, value in DYNAMICS:
        voice.append((beat * TPQ, bytes((0xB0, 11, value))))
    for index, (beat, _cue_id, _kind, strength) in enumerate(CUES):
        pitch = 60 + (index % 8)
        start = beat * TPQ
        voice.append((start, bytes((0x90, pitch, strength))))
        voice.append((min(TOTAL_BEATS * TPQ, start + TPQ), bytes((0x80, pitch, 0))))
    voice.append((TOTAL_BEATS * TPQ, meta(0x2F, b"")))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, 2, TPQ)
    return header + make_track(conductor) + make_track(voice)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="require the tracked fixture to match the generator")
    args = parser.parse_args()
    expected = fixture_bytes()
    if args.check:
        if not args.out.is_file() or args.out.read_bytes() != expected:
            parser.error(f"{args.out} is absent or stale; regenerate it without --check")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(expected)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the native-tempo Danse Delibes chamber MIDI.

MuseScore is the authoritative MSCZ-to-MIDI exporter.  Its exported MIDI is an
ignored, hydrated intermediate; this script verifies those bytes, removes MIDI
port metadata that the Danse compiler deliberately rejects, maps the five
accordion voices to strings, translates Sylvia's five written triangle cues,
retains Coppelia's timpani, and appends the four-second silent SIGNATURE tail.

No event in either musical movement is time-scaled.  The output therefore owns
the exact native exported durations (149.152297125 + 197.744046 seconds) and a
four-second silent signature, for 350.896343125 seconds total.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "music" / "delibes-screendance-suite.mid"
DEFAULT_MANIFEST = ROOT / "music" / "adaptation.json"
MUSESCORE = Path("/Applications/MuseScore 4.app/Contents/MacOS/mscore")
DIVISION = 480
SIGNATURE_TICKS = 4 * DIVISION * 2  # 120 BPM: four seconds.

SOURCES = (
    {
        "id": "sylvia-valse-lente",
        "score": ROOT / "music" / "sources" / "Valse-Lente-Delibes.mscz",
        "score_sha256": "76e183b57c7f035a319bf5a7c5691d61c8a5f3af61f9d1b860047f7b28e6dc70",
        "export": ROOT / ".work" / "music" / "Valse-Lente-Delibes.mid",
        "export_sha256": "3c3aa0859fc4ed4017b4bf93fa2b23e34f3eefb7f27b8b52768fa12d04499026",
        "tracks": 5,
        "duration_ticks": 150_240,
    },
    {
        "id": "coppelia-valse",
        "score": ROOT / "music" / "sources" / "Valse-Coppelia.mscz",
        "score_sha256": "86e1eaad1e99fcf3f275af9c59cde94580d9f9bfb10f7d366053a398295001c7",
        "export": ROOT / ".work" / "music" / "Valse-Coppelia.mid",
        "export_sha256": "075a3121923e1b7bce17cf5ce2cca65cd01afe9cf988008be8a71b8ebeb58f88",
        "tracks": 6,
        "duration_ticks": 272_160,
    },
)

STRING_VOICES = (
    ("violin-i", "Violin I", 0, 40),
    ("violin-ii", "Violin II", 1, 40),
    ("viola", "Viola", 2, 41),
    ("cello", "Cello", 3, 42),
    ("contrabass", "Contrabass", 4, 43),
)

# The source encodes these as non-playing cross-head notes labelled "triangle"
# in the fifth accordion staff.  They were recovered by enabling only those five
# annotated notes in a temporary score and diffing the deterministic MIDI export.
SYLVIA_TRIANGLE_TICKS = (11_520, 24_480, 37_440, 50_400, 89_280)


@dataclass(frozen=True)
class MidiEvent:
    tick: int
    priority: int
    order: int
    kind: str
    status: int
    data: bytes


@dataclass(frozen=True)
class MidiFile:
    division: int
    tracks: tuple[tuple[MidiEvent, ...], ...]
    ends: tuple[int, ...]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def variable_length(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise ValueError("truncated MIDI variable-length value")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("MIDI variable-length value exceeds four bytes")


def encode_variable_length(value: int) -> bytes:
    if type(value) is not int or value < 0 or value >= 1 << 28:
        raise ValueError(f"invalid MIDI variable-length value {value!r}")
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def parse_midi(path: Path) -> MidiFile:
    data = path.read_bytes()
    if len(data) < 14 or data[:4] != b"MThd" or struct.unpack(">I", data[4:8])[0] != 6:
        raise ValueError(f"{path}: invalid Standard MIDI File header")
    midi_format, track_count, division = struct.unpack(">HHH", data[8:14])
    if midi_format != 1 or division & 0x8000:
        raise ValueError(f"{path}: expected format-1 metrical MIDI")
    position = 14
    tracks: list[tuple[MidiEvent, ...]] = []
    ends: list[int] = []
    for track_index in range(track_count):
        if position + 8 > len(data) or data[position : position + 4] != b"MTrk":
            raise ValueError(f"{path}: missing MIDI track {track_index}")
        length = struct.unpack(">I", data[position + 4 : position + 8])[0]
        payload = data[position + 8 : position + 8 + length]
        if len(payload) != length:
            raise ValueError(f"{path}: truncated MIDI track {track_index}")
        position += 8 + length
        cursor = 0
        tick = 0
        running: int | None = None
        order = 0
        events: list[MidiEvent] = []
        while cursor < len(payload):
            delta, cursor = variable_length(payload, cursor)
            tick += delta
            if cursor >= len(payload):
                raise ValueError(f"{path}: track {track_index} ends after a delta")
            status = payload[cursor]
            if status & 0x80:
                cursor += 1
                running = status if status < 0xF0 else None
            elif running is not None:
                status = running
            else:
                raise ValueError(f"{path}: track {track_index} has data without running status")
            if status == 0xFF:
                if cursor >= len(payload):
                    raise ValueError(f"{path}: truncated meta event")
                meta_type = payload[cursor]
                cursor += 1
                size, cursor = variable_length(payload, cursor)
                body = payload[cursor : cursor + size]
                cursor += size
                if len(body) != size:
                    raise ValueError(f"{path}: truncated meta payload")
                events.append(MidiEvent(tick, 10, order, "meta", meta_type, body))
                running = None
            elif status in {0xF0, 0xF7}:
                size, cursor = variable_length(payload, cursor)
                body = payload[cursor : cursor + size]
                cursor += size
                if len(body) != size:
                    raise ValueError(f"{path}: truncated system-exclusive payload")
                events.append(MidiEvent(tick, 10, order, "sysex", status, body))
                running = None
            else:
                family = status >> 4
                width = 1 if family in {0xC, 0xD} else 2
                body = payload[cursor : cursor + width]
                cursor += width
                if len(body) != width or any(byte & 0x80 for byte in body):
                    raise ValueError(f"{path}: invalid channel event")
                priority = 50 if family in {0x8, 0x9} else 20
                events.append(MidiEvent(tick, priority, order, "channel", status, body))
            order += 1
        tracks.append(tuple(events))
        ends.append(tick)
    if position != len(data):
        raise ValueError(f"{path}: bytes follow the declared MIDI tracks")
    return MidiFile(division, tuple(tracks), tuple(ends))


def meta(tick: int, meta_type: int, text_or_bytes: str | bytes, priority: int, order: int) -> MidiEvent:
    data = text_or_bytes.encode("utf-8") if isinstance(text_or_bytes, str) else text_or_bytes
    return MidiEvent(tick, priority, order, "meta", meta_type, data)


def channel(tick: int, status: int, data: bytes, priority: int, order: int) -> MidiEvent:
    return MidiEvent(tick, priority, order, "channel", status, data)


def write_track(events: list[MidiEvent], end_tick: int) -> bytes:
    body = bytearray()
    previous = 0
    rows = [event for event in events if not (event.kind == "meta" and event.status == 0x2F)]
    for event in sorted(rows, key=lambda item: (item.tick, item.priority, item.order)):
        if event.tick < previous or event.tick > end_tick:
            raise ValueError("MIDI event lies outside its track")
        body.extend(encode_variable_length(event.tick - previous))
        previous = event.tick
        if event.kind == "meta":
            body.extend((0xFF, event.status))
            body.extend(encode_variable_length(len(event.data)))
            body.extend(event.data)
        elif event.kind == "sysex":
            body.append(event.status)
            body.extend(encode_variable_length(len(event.data)))
            body.extend(event.data)
        else:
            body.append(event.status)
            body.extend(event.data)
    body.extend(encode_variable_length(end_tick - previous))
    body.extend(b"\xff\x2f\x00")
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def source_channel_events(
    parsed: MidiFile,
    source_track: int,
    *,
    offset: int,
    output_channel: int,
) -> tuple[list[MidiEvent], int]:
    rows = []
    active: dict[tuple[int, int], list[int]] = {}
    clamped = 0
    for event in parsed.tracks[source_track]:
        if event.kind != "channel":
            continue
        family = event.status >> 4
        source_channel = event.status & 0x0F
        tick = event.tick
        if family == 0x9 and event.data[1] > 0:
            active.setdefault((source_channel, event.data[0]), []).append(event.tick)
        elif family == 0x8 or (family == 0x9 and event.data[1] == 0):
            key = (source_channel, event.data[0])
            if active.get(key):
                start_tick = active[key].pop(0)
                # MuseScore represents a small set of positive written notes as
                # same-tick on/off pairs after quantization.  Preserve their
                # authored onset and give only the closure the minimum legal
                # MIDI duration; do not weaken the downstream compiler.
                if tick <= start_tick:
                    tick = start_tick + 1
                    clamped += 1
        if family == 0xC:
            continue
        if family == 0xB and event.data[0] in {0, 32, 11}:
            continue
        status = (event.status & 0xF0) | output_channel
        rows.append(MidiEvent(tick + offset, event.priority, event.order + 10_000, "channel", status, event.data))
    return rows, clamped


def conductor_meta(parsed: MidiFile, offset: int) -> list[MidiEvent]:
    rows = []
    order = 0
    for track in parsed.tracks:
        for event in track:
            if event.kind == "meta" and event.status in {0x51, 0x58}:
                rows.append(MidiEvent(event.tick + offset, 10, order, "meta", event.status, event.data))
                order += 1
    # MuseScore duplicates no conductor state in the current sources, but make
    # that assumption executable in case a later exporter does.
    unique: dict[tuple[int, int], MidiEvent] = {}
    for event in rows:
        key = (event.tick, event.status)
        previous = unique.get(key)
        if previous and previous.data != event.data:
            raise ValueError(f"conflicting conductor metadata at tick {event.tick}")
        unique[key] = event
    return list(unique.values())


def phrase_ticks(start: int, duration: int) -> list[int]:
    # Both source movements are in 3/4 at 480 TPQ.  Eight-bar phrases are
    # authored for the adaptation; omit a boundary that would leave <2 bars.
    phrase = 8 * 3 * DIVISION
    minimum_tail = 2 * 3 * DIVISION
    ticks = [start]
    local = phrase
    while local + minimum_tail <= duration:
        ticks.append(start + local)
        local += phrase
    return ticks


def expression_curve(count: int, *, floor: int, ceiling: int) -> list[int]:
    if count <= 1:
        return [floor]
    values = []
    for index in range(count):
        u = Fraction(index, count - 1)
        # A restrained rise with a small phrase-scale ebb, all integer-authored.
        base = floor + round(float(u) * (ceiling - floor))
        ebb = (0, 3, 0, -2)[index % 4]
        values.append(max(1, min(127, base + ebb)))
    return values


def build(sylvia: MidiFile, coppelia: MidiFile) -> tuple[bytes, dict[str, object]]:
    if sylvia.division != DIVISION or coppelia.division != DIVISION:
        raise ValueError("source exports must use 480 ticks per quarter")
    if len(sylvia.tracks) != 5 or len(coppelia.tracks) != 6:
        raise ValueError("source export track count changed")
    sylvia_end = max(sylvia.ends)
    coppelia_length = max(coppelia.ends)
    if sylvia_end != 150_240 or coppelia_length != 272_160:
        raise ValueError("source export duration ticks changed")
    coppelia_start = sylvia_end
    signature_start = sylvia_end + coppelia_length
    end_tick = signature_start + SIGNATURE_TICKS

    conductor = [meta(0, 0x03, "Danse Delibes native score", 0, 0)]
    conductor += conductor_meta(sylvia, 0)
    conductor += conductor_meta(coppelia, coppelia_start)
    # Silence owns one explicit, exact four-second signature at 120 BPM.
    conductor.append(meta(signature_start, 0x51, (500_000).to_bytes(3, "big"), 5, 0))

    movement_rows = ((0, "SYLVIA"), (coppelia_start, "COPPELIA"), (signature_start, "SIGNATURE"))
    for order, (tick, name) in enumerate(movement_rows):
        conductor.append(meta(tick, 0x06, f"movement:{name}", 30, order))

    sylvia_phrases = phrase_ticks(0, sylvia_end)
    coppelia_phrases = phrase_ticks(coppelia_start, coppelia_length)
    for index, tick in enumerate(sylvia_phrases, 1):
        conductor.append(meta(tick, 0x06, f"phrase:sylvia-{index:02d}", 31, index))
    for index, tick in enumerate(coppelia_phrases, 1):
        conductor.append(meta(tick, 0x06, f"phrase:coppelia-{index:02d}", 31, index))
    conductor.append(meta(signature_start, 0x06, "phrase:signature", 31, 999))

    cue_rows = [
        (0, "sylvia-entry", "cue", 72),
        (69_120, "sylvia-lift", "accent", 84),
        (coppelia_start, "coppelia-entry", "cue", 78),
        (coppelia_start + 138_240, "coppelia-lift", "accent", 92),
        (coppelia_start + 241_920, "coppelia-climax", "accent", 108),
        (signature_start, "signature-entry", "cue", 64),
    ]
    for index, tick in enumerate(SYLVIA_TRIANGLE_TICKS, 1):
        cue_rows.append((tick, f"sylvia-triangle-{index:02d}", "accent", 70))
    for order, (tick, cue_id, kind, strength) in enumerate(cue_rows):
        conductor.append(meta(tick, 0x06, f"cue:{cue_id}:{kind}:{strength}", 32, order))

    sylvia_expression = expression_curve(len(sylvia_phrases), floor=72, ceiling=98)
    coppelia_expression = expression_curve(len(coppelia_phrases), floor=78, ceiling=112)
    expression = list(zip(sylvia_phrases, sylvia_expression)) + list(zip(coppelia_phrases, coppelia_expression))

    tracks: list[bytes] = [write_track(conductor, end_tick)]
    clamped_notes = 0
    for _voice_id, name, output_channel, program in STRING_VOICES:
        rows = [meta(0, 0x03, name, 0, 0)]
        sylvia_rows, sylvia_clamped = source_channel_events(
            sylvia,
            output_channel,
            offset=0,
            output_channel=output_channel,
        )
        coppelia_rows, coppelia_clamped = source_channel_events(
            coppelia,
            output_channel,
            offset=coppelia_start,
            output_channel=output_channel,
        )
        rows += sylvia_rows
        rows += coppelia_rows
        clamped_notes += sylvia_clamped + coppelia_clamped
        rows.append(channel(0, 0xC0 | output_channel, bytes((program,)), 30, 0))
        rows.append(channel(coppelia_start, 0xC0 | output_channel, bytes((program,)), 30, 1))
        for order, (tick, value) in enumerate(expression):
            rows.append(channel(tick, 0xB0 | output_channel, bytes((11, value)), 40, order))
        rows.append(channel(signature_start, 0xB0 | output_channel, bytes((11, 0)), 40, 999))
        tracks.append(write_track(rows, end_tick))

    triangle = [meta(0, 0x03, "Triangle", 0, 0)]
    for order, tick in enumerate(SYLVIA_TRIANGLE_TICKS):
        triangle.append(channel(tick, 0x99, bytes((81, 70)), 50, order * 2))
        triangle.append(channel(tick + 120, 0x89, bytes((81, 0)), 50, order * 2 + 1))
    tracks.append(write_track(triangle, end_tick))

    timpani = [meta(0, 0x03, "Timpani", 0, 0)]
    timpani_rows, timpani_clamped = source_channel_events(
        coppelia,
        5,
        offset=coppelia_start,
        output_channel=5,
    )
    timpani += timpani_rows
    clamped_notes += timpani_clamped
    timpani.append(channel(coppelia_start, 0xC5, bytes((47,)), 30, 0))
    # The source score declares/contains the timpani notes but its exported
    # mixer state sets CC7 to zero.  The Danse adaptation explicitly retains
    # those accents, so restore an audible fixed volume after the source reset.
    timpani.append(channel(coppelia_start, 0xB5, bytes((7, 100)), 35, 0))
    for order, (at, level) in enumerate(zip(coppelia_phrases, coppelia_expression)):
        timpani.append(channel(at, 0xB5, bytes((11, level)), 40, order))
    tracks.append(write_track(timpani, end_tick))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), DIVISION)
    payload = header + b"".join(tracks)
    details: dict[str, object] = {
        "duration_ticks": end_tick,
        "sylvia_ticks": sylvia_end,
        "coppelia_ticks": coppelia_length,
        "signature_ticks": SIGNATURE_TICKS,
        "track_count": len(tracks),
        "phrase_count": len(sylvia_phrases) + len(coppelia_phrases) + 1,
        "triangle_ticks": list(SYLVIA_TRIANGLE_TICKS),
        "minimum_tick_closures": clamped_notes,
    }
    return payload, details


def exporter_version(executable: Path) -> str:
    result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, check=False)
    text = (result.stdout + result.stderr).strip()
    if "4.7.4" not in text:
        raise ValueError(f"MuseScore 4.7.4 is required; got {text!r}")
    return "4.7.4"


def export_source(executable: Path, score: Path, output: Path) -> None:
    result = subprocess.run([str(executable), "-o", str(output), str(score)], capture_output=True, check=False)
    # MuseScore 4.7.4 can crash after atomically writing a complete MIDI on the
    # current macOS/Rosetta host.  Exit status is therefore not accepted as the
    # receipt: the exact expected digest and a complete structural parse are.
    if not output.is_file():
        diagnostic = (result.stdout + result.stderr).decode("utf-8", errors="replace")[-1000:]
        raise ValueError(f"MuseScore did not create {output}: {diagnostic}")


def resolve_exports(args: argparse.Namespace) -> tuple[list[Path], str]:
    if args.rebuild_exports:
        executable = args.musescore.resolve()
        version = exporter_version(executable)
        temporary = tempfile.TemporaryDirectory(prefix="danse-musescore-")
        args._temporary = temporary  # Keep the directory alive through build.
        directory = Path(temporary.name)
        paths = []
        for source in SOURCES:
            target = directory / (source["id"] + ".mid")
            export_source(executable, source["score"], target)
            paths.append(target)
        return paths, version
    return [Path(source["export"]) for source in SOURCES], "4.7.4"


def validate_inputs(paths: list[Path]) -> tuple[MidiFile, MidiFile]:
    parsed = []
    for source, path in zip(SOURCES, paths):
        if sha256(source["score"]) != source["score_sha256"]:
            raise ValueError(f"source score hash changed: {source['score']}")
        if not path.is_file():
            raise ValueError(f"missing hydrated MuseScore export: {path}")
        actual = sha256(path)
        if actual != source["export_sha256"]:
            raise ValueError(f"MuseScore export hash changed for {source['id']}: {actual}")
        midi = parse_midi(path)
        if len(midi.tracks) != source["tracks"] or max(midi.ends) != source["duration_ticks"]:
            raise ValueError(f"MuseScore export structure changed for {source['id']}")
        parsed.append(midi)
    return parsed[0], parsed[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--check", action="store_true", help="require existing MIDI and manifest identities to match")
    parser.add_argument("--rebuild-exports", action="store_true", help="re-export exact MSCZ sources before adapting")
    parser.add_argument("--musescore", type=Path, default=MUSESCORE)
    args = parser.parse_args()
    paths, version = resolve_exports(args)
    sylvia, coppelia = validate_inputs(paths)
    payload, details = build(sylvia, coppelia)
    output_digest = hashlib.sha256(payload).hexdigest()
    if args.check:
        if not args.out.is_file() or args.out.read_bytes() != payload:
            raise SystemExit(f"FAIL: {args.out} is not the deterministic adapted MIDI")
        manifest = json.loads(args.manifest.read_text())
        if manifest.get("output", {}).get("sha256") != output_digest:
            raise SystemExit(f"FAIL: {args.manifest} does not bind adapted MIDI {output_digest}")
        print(f"ok: {args.out.relative_to(ROOT)} ({output_digest}; MuseScore {version})")
        return 0
    args.out.write_bytes(payload)
    print(json.dumps({"path": str(args.out), "sha256": output_digest, **details}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Find the recordings made in that room — and say honestly what was found.

The score is generative from the same seed as the picture, which makes it
rights-clean by construction. What makes it *true* is the material: not a
synthesiser pretending to be a room, but sound actually recorded in the apartment
the 162 photographs were made in. That is the difference between a score and a
screensaver, and it is the one part of the sound the machine cannot manufacture.

So this catalogues rather than guesses. It writes `sources.json` — every
candidate, where it came from, how long it is, and how confident the provenance
is — and it never moves or converts an original. Re-run it with `--root` pointing
somewhere new and it folds the new find into the same catalogue.

Four channels, in descending order of how likely they are to hold that room:

  photos    Photos.app, via AppleScript. THE SANCTIONED PATH — the library is
            TCC-blocked at the shell, and this is the same channel the 161 stills
            came out of. A video shot that afternoon carries the room's actual
            air, which is the best material that could possibly exist for this.
  spotlight indexed audio in a date window, wherever it lives
  roots     an explicit directory walk, for drives Spotlight has not indexed
  daw       Logic / GarageBand projects, whose Media folders hold raw takes

    sound/resolve.py                       # catalogue what is reachable
    sound/resolve.py --root <mounted-drive>     # add a drive
    sound/resolve.py --photos --export .work/audio   # pull from Photos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CATALOGUE = HERE / "sources.json"
REGISTER = HERE.parent / "submission/screendance-2027.yaml"

# The shoot. Anything recorded near it is a candidate for having that room in it;
# anything recorded IN the window is a candidate for being that room.
SHOOT = date(2017, 6, 20)
NEAR_DAYS = 120

# Confirmed by looking at a frame from each and seeing the apartment: the same
# cream textured wall, the same carpet, the Universal Monsters leaning on the
# floor with `Creature from the Black Lagoon` at the left end.
#
# A date near the shoot only makes a recording a CANDIDATE. Of eleven within a
# fortnight, TWO are that room and the rest are a television, a baseball field, a
# backyard fence and a face — and the nearest by filename (IMG_1568, two numbers
# before the corpus's first still) is someone filming a TV. Adjacency is not
# provenance; recognising the wall is.
#
# FULL FILENAMES, not stems. Photos appends " (1)" when two DIFFERENT assets share
# an original filename, so matching on a stripped stem lets an unrelated asset
# inherit a confirmation: `IMG_1920 (1).MOV` is a backyard, and it was licensed
# into the bed by `IMG_1920.mov`'s name. Exact match, or nothing.
# Looked at, and NOT confirmed — recorded here so the judgement survives instead
# of being re-litigated every run. These stay out of the bed.
#
#   IMG_1920.mov      the same corkboard, the same light-wood furniture, the same
#                     berber carpet, a guitar-shaped outline behind the backpack —
#                     but the frame never shows the poster wall. Resemblance is
#                     not recognition. Promote it if he says it is that apartment.
#   IMG_1920 (1).MOV  a backyard fence. Not the room, not even indoors.
#   IMG_1802.MOV      a face, filling the frame for all 12 seconds. Nothing to
#                     recognise.
UNCONFIRMED_ROOM = {
    "IMG_1920.mov": "resembles it — same corkboard and carpet — but no poster wall in frame",
    "IMG_1920 (1).MOV": "a backyard fence, not the apartment",
    "IMG_1802.MOV": "a face close-up for its whole length; no room visible",
}

AUDIO = {".wav", ".aif", ".aiff", ".m4a", ".mp3", ".caf", ".flac", ".ogg"}
VIDEO = {".mov", ".mp4", ".m4v"}

DEFAULT_ROOTS = [
    Path.home() / "Music",
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Library/Application Support/com.apple.voicememos",
    Path.home() / "Library/Mobile Documents",
]

# Two bulk property reads, then filter in Python.
#
# NOT `whose date > t`: `date` collides with AppleScript's `date` class inside a
# `whose` clause and Photos answers -1700, "Can't make date into type specifier".
# And building the comparison date has its own trap — `set month to 6` on the 31st
# of a month rolls into July, because June has thirty days. Reading every
# property in one Apple Event sidesteps both: 28,000 items in about eleven
# seconds, and the filtering happens somewhere with a real date type.
PHOTOS_QUERY = """
tell application "Photos"
  set fs to filename of every media item
  set ds to date of every media item
end tell
set AppleScript's text item delimiters to linefeed
set out to {}
repeat with i from 1 to (count of fs)
  set end of out to ((item i of fs) & tab & ((item i of ds) as string))
end repeat
return out as string
"""

MONTHS = {
    m: i
    for i, m in enumerate(
        "january february march april may june july august september october november december".split(), 1
    )
}


def probe(path: Path) -> dict | None:
    """Duration, rate and channels — or None if there is no audio stream in it."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
        data = json.loads(out.stdout or "{}")
    except Exception:
        return None
    streams = data.get("streams") or []
    if not streams:
        return None
    fmt = data.get("format") or {}
    return {
        "codec": streams[0].get("codec_name"),
        "rate": int(streams[0].get("sample_rate") or 0),
        "channels": int(streams[0].get("channels") or 0),
        "seconds": round(float(fmt.get("duration") or 0), 2),
    }


def fingerprint(path: Path, limit: int = 1 << 20) -> str:
    """First megabyte. Enough to dedupe the same take found down two paths,
    cheap enough to run over a whole drive."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        h.update(fh.read(limit))
    return h.hexdigest()[:16]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def registered_room_assets(register: Path) -> dict[str, str]:
    data = yaml.safe_load(register.read_text()) or {}
    audio = ((data.get("package") or {}).get("audio") or {})
    names = audio.get("source_recordings") or []
    digests = audio.get("source_sha256") or {}
    out = {name: digests.get(name) for name in names}
    invalid = [
        name
        for name, digest in out.items()
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
    ]
    if invalid:
        raise SystemExit(f"missing registered source digest(s): {', '.join(invalid)}")
    return {name: digest.lower() for name, digest in out.items()}


def room_content_matches(path: Path, expected_digest: str | None) -> tuple[bool, str | None]:
    digest = sha256_file(path) if expected_digest else None
    return digest == expected_digest, digest


def provenance(path: Path, when: date | None) -> tuple[str, str]:
    """How much this file's origin is actually known. Stated, never inferred into
    confidence it has not earned — a bed that turns out to be from the wrong room
    is worse than a bed that was labelled unknown."""
    if when is None:
        return "unknown", "no date on the file"
    delta = abs((when - SHOOT).days)
    if delta == 0:
        return "shoot-day", f"recorded {when} — the afternoon itself"
    if delta <= NEAR_DAYS:
        return "near-shoot", f"recorded {when}, {delta} days from the shoot"
    return "other", f"recorded {when}"


def created(path: Path) -> date | None:
    try:
        st = path.stat()
        # macOS keeps a real birth time; mtime on a copied file is a lie.
        stamp = getattr(st, "st_birthtime", None) or st.st_mtime
        return date.fromtimestamp(stamp)
    except OSError:
        return None


def walk(roots: list[Path], depth: int) -> list[Path]:
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath)
            if len(here.parts) - base >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if Path(name).suffix.lower() in AUDIO | VIDEO:
                    found.append(here / name)
    return found


def spotlight(years: tuple[int, int]) -> list[Path]:
    q = (
        "kMDItemContentTypeTree == 'public.audio' && "
        f"kMDItemContentCreationDate >= $time.iso({years[0]}-01-01T00:00:00Z) && "
        f"kMDItemContentCreationDate <= $time.iso({years[1]}-01-01T00:00:00Z)"
    )
    out = subprocess.run(["mdfind", q], capture_output=True, text=True, check=False)
    hits = [Path(line) for line in out.stdout.splitlines() if line]
    # Factory sample libraries are indexed too and are not anybody's recordings.
    return [p for p in hits if "/Library/Audio/Apple Loops" not in str(p) and "/EXS Factory Samples/" not in str(p)]


def _photo_date(stamp: str) -> date | None:
    """Photos hands back a locale-formatted string: "Tuesday, June 20, 2017 at …"."""
    parts = stamp.replace(",", " ").split()
    for i, word in enumerate(parts):
        month = MONTHS.get(word.lower())
        if month and i + 2 < len(parts):
            try:
                return date(int(parts[i + 2]), month, int(parts[i + 1]))
            except ValueError:
                return None
    return None


def photos(window_days: int) -> list[dict]:
    """Ask Photos.app what video it holds from around the shoot. Read-only.

    A video shot that afternoon carries the room's actual air, and is the single
    best piece of material this score could have.
    """
    lo = date.fromordinal(SHOOT.toordinal() - window_days)
    hi = date.fromordinal(SHOOT.toordinal() + window_days)
    out = subprocess.run(["osascript", "-e", PHOTOS_QUERY], capture_output=True, text=True, check=False, timeout=300)
    if out.returncode != 0:
        return [{"error": (out.stderr or "osascript failed").strip().splitlines()[-1]}]

    items, unparsed = [], 0
    for line in out.stdout.splitlines():
        name, _, stamp = line.partition("\t")
        if Path(name).suffix.lower() not in VIDEO:
            continue
        when = _photo_date(stamp)
        if when is None:
            unparsed += 1
            continue
        if lo <= when <= hi:
            items.append({"filename": name, "date": when.isoformat(), "days_from_shoot": (when - SHOOT).days})
    if unparsed:
        items.append({"note": f"{unparsed} video dates could not be parsed from Photos' locale format"})
    return items


# `with timeout of` is not optional. Each `whose filename is` scans 28,000 items,
# and the default Apple Event timeout is sixty seconds — without this the export
# dies with -1712 partway through and leaves you guessing which half arrived.
PHOTOS_EXPORT = """
with timeout of 1800 seconds
  tell application "Photos"
    set picks to {}
    repeat with n in %(names)s
      try
        set picks to picks & (every media item whose filename is (n as string))
      end try
    end repeat
    if (count of picks) is 0 then return "none"
    export picks to (POSIX file "%(dest)s") with using originals
    return (count of picks) as string
  end tell
end timeout
"""


def export_from_photos(names: list[str], dest: Path) -> tuple[int, str]:
    """Pull specific videos out of Photos. The library is TCC-blocked at the
    shell, so this is the only sanctioned route — and it is the same one the 161
    stills came out of."""
    # ABSOLUTE. `POSIX file "…"` resolves against something that is not
    # this process's working directory, and Photos then reports a happy export
    # count for files that landed nowhere you will ever find them.
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in dest.iterdir()}
    script = PHOTOS_EXPORT % {
        "names": "{" + ", ".join(f'"{n}"' for n in names) + "}",
        "dest": str(dest),
    }
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False, timeout=1900)
    if out.returncode != 0:
        return 0, (out.stderr or "osascript failed").strip().splitlines()[-1]
    # Count what actually appeared on disk, not what Photos said it picked.
    landed = [p for p in dest.iterdir() if p.name not in before]
    if not landed:
        return 0, f"Photos reported {out.stdout.strip()} matches but wrote nothing to {dest}"
    return len(landed), ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, action="append", default=[], help="extra directory to walk (repeatable)")
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--photos", action="store_true", help="also ask Photos.app for video from around the shoot")
    ap.add_argument("--photos-window", type=int, default=NEAR_DAYS)
    ap.add_argument("--no-spotlight", action="store_true")
    ap.add_argument("--out", type=Path, default=CATALOGUE)
    ap.add_argument(
        "--export", type=Path, metavar="DIR", help="export the nearest Photos videos to DIR and catalogue them"
    )
    ap.add_argument(
        "--export-window", type=int, default=15, help="days either side of the shoot to export (default 15)"
    )
    ap.add_argument(
        "--room",
        default="",
        help="additional comma-separated NAME=SHA256 approvals; registered assets come from the submission register",
    )
    ap.add_argument("--register", type=Path, default=REGISTER)
    args = ap.parse_args()
    if args.export:
        args.photos = True

    roots = [*DEFAULT_ROOTS, *args.root]
    confirmed = registered_room_assets(args.register)
    for atom in (value.strip() for value in args.room.split(",") if value.strip()):
        name, separator, digest = atom.partition("=")
        if not separator or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            ap.error("--room entries must be NAME=SHA256")
        confirmed[name] = digest.lower()
    seen: dict[str, dict] = {}

    print("looking for the room\n")
    candidates: list[tuple[Path, str]] = []
    for p in walk(roots, args.depth):
        candidates.append((p, "roots"))
    print(f"  roots      {len(candidates):>5} files under {len(roots)} directories")

    if not args.no_spotlight:
        before = len(candidates)
        for p in spotlight((SHOOT.year - 2, SHOOT.year + 3)):
            candidates.append((p, "spotlight"))
        print(f"  spotlight  {len(candidates) - before:>5} indexed audio near {SHOOT.year}")

    photo_items = []
    if args.photos:
        photo_items = photos(args.photos_window)
        err = next((i["error"] for i in photo_items if "error" in i), None)
        if err:
            print(f"  photos     UNREACHABLE — {err}")
            print("             if this is a permissions prompt, allow it and re-run")
            photo_items = []
        else:
            print(f"  photos     {len(photo_items):>5} videos within ±{args.photos_window} days of the shoot")

    if args.export and photo_items:
        near = sorted(
            (i for i in photo_items if "filename" in i and abs(i["days_from_shoot"]) <= args.export_window),
            key=lambda i: abs(i["days_from_shoot"]),
        )
        names = list(dict.fromkeys(i["filename"] for i in near))
        if names:
            n, err = export_from_photos(names, args.export)
            print(
                f"  export     {n} of {len(names)} within ±{args.export_window} days → {args.export}"
                + (f" — {err}" if err else "")
            )
            for p in walk([args.export], 2):
                candidates.append((p, "photos"))
        else:
            print(f"  export     nothing within ±{args.export_window} days of the shoot")

    print()
    for path, channel in candidates:
        try:
            if not path.is_file() or path.stat().st_size < 4096:
                continue
        except OSError:
            continue
        info = probe(path)
        if not info or info["seconds"] < 1.0:
            continue
        key = fingerprint(path)
        if key in seen:
            seen[key]["also_at"].append(str(path))
            continue
        when = created(path)
        tier, why = provenance(path, when)
        # `room` is the only field that licenses a recording into the bed. It is
        # set by having LOOKED at the footage, not by arithmetic on a filename.
        expected_digest = confirmed.get(path.name)
        in_room, content_digest = room_content_matches(path, expected_digest)
        if in_room:
            tier, why = "room", "the apartment itself — confirmed by frame and registered content digest"
        elif expected_digest:
            tier, why = "unconfirmed-room", "registered filename with different content digest"
        elif path.name in UNCONFIRMED_ROOM:
            tier, why = "unconfirmed-room", UNCONFIRMED_ROOM[path.name]
        seen[key] = {
            "id": key,
            "path": str(path),
            "room": in_room,
            "sha256": content_digest if in_room else None,
            "also_at": [],
            "channel": channel,
            "created": when.isoformat() if when else None,
            "provenance": tier,
            "note": why,
            **info,
        }

    rows = sorted(
        seen.values(), key=lambda r: (r["provenance"] != "shoot-day", r["provenance"] != "near-shoot", -r["seconds"])
    )
    catalogue = {
        "schema": "danse.sound.sources.v1",
        "shoot": SHOOT.isoformat(),
        "roots": [str(r) for r in roots],
        "photos_videos": photo_items,
        "sources": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalogue, indent=1) + "\n")

    by_tier: dict[str, list] = {}
    for r in rows:
        by_tier.setdefault(r["provenance"], []).append(r)
    total = sum(r["seconds"] for r in rows)
    print(
        f"{len(rows)} distinct recordings · {total / 60:.1f} minutes · {args.out.relative_to(Path.cwd()) if args.out.is_relative_to(Path.cwd()) else args.out}"
    )
    for tier in ("room", "unconfirmed-room", "shoot-day", "near-shoot", "other", "unknown"):
        group = by_tier.get(tier, [])
        if not group:
            continue
        mins = sum(r["seconds"] for r in group) / 60
        print(f"  {tier:<11} {len(group):>4} recordings · {mins:>6.1f} min")
        for r in group[:3]:
            print(f"      {r['seconds']:>7.1f}s  {Path(r['path']).name}  ({r['note']})")

    room = by_tier.get("room", [])
    if room:
        secs = sum(r["seconds"] for r in room)
        print(f"\nTHE ROOM: {len(room)} recordings, {secs / 60:.1f} minutes of that apartment's actual air.")
        print("This is the bed. Everything else the score does is generated from the seed.")
    elif not by_tier.get("shoot-day") and not by_tier.get("near-shoot"):
        print(
            "\nNothing dated near 20 June 2017 is reachable from here.\n"
            "The recordings exist — point this at them:  resolve.py --root /path/to/them\n"
            "If they are in Photos as video, run:         resolve.py --photos"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

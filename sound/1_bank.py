#!/usr/bin/env python3
"""The room, cut into grains — the second corpus.

The picture selects fragments of a body out of 162 photographs, indexed by
geometry: where the figure stood, which joints Vision could find. The sound does
the same thing one axis over. The two confirmed recordings from that apartment
are cut into grains and indexed by what a grain SOUNDS like — spectral centroid,
brightness, decay — so `score.py` can ask "what is dark and slow?" the way
`grammar.js` asks "who is standing in this part of the room?", and answer it out
of the index without touching a sample.

No synthesis anywhere in the chain. Every sample that reaches the film was
recorded in that apartment in 2017.

Three kinds, because the score needs three different things:

    bed        long, quiet, low-flux stretches. The room's air, continuous
               underneath everything. Cut by sliding a window, NOT by onsets —
               the whole point of a bed is the part where nothing happens, and
               an onset detector is blind to it by construction.
    sustained  mid-length tonal material. One per active plane; depth sets pitch
               and reverb send, so the chord literally IS the spatial arrangement.
    transient  short and sharp. Limb events, decimated hard in the score — most
               of these are thrown away, and that is the decision that makes the
               rhythm countable by a dancer instead of by a profiler.

Requires ffmpeg (decode) plus numpy and scipy. Deliberately NOT librosa: the
analysis here is four textbook descriptors over one STFT, and this repo's CI
should not grow a 60 MB dependency to compute a weighted mean.

    sound/1_bank.py            # build from every room: true source
    sound/1_bank.py --list     # what would be cut, without cutting
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import stft

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CATALOGUE = HERE / "sources.json"
BANK = HERE / "bank"
INDEX = BANK / "bank.json"

SR = 48_000
WIN = 2048
HOP = 512

# A grain shorter than this is a click, not material.
MIN_GRAIN = 0.06
# Onset-cut grains stop here even if the next onset is far away; past this the
# material belongs to the bed, which is cut a different way.
MAX_GRAIN = 2.5

# Bed windows: long enough to be air rather than an event, overlapped so the
# score has choices at any point in the recording.
BED_WINDOW = 4.0
BED_STRIDE = 2.0

# A grain quieter than this is the noise floor between events.
SILENCE_DBFS = -55.0

# How far past a grain's own end its ring is watched, so `decay` measures the
# event rather than the cut. Never mixed into the grain — see `describe`.
DECAY_LOOKAHEAD = 2.0

# Where a grain sits on the transient/sustained line — by ATTACK and length, not
# by decay. Nothing in a continuous room recording decays quickly: the air is
# always there, so even a hand clap's fitted T20 is seconds long. What separates
# a hit from a tone here is how much of its energy arrives in the first 30 ms.
TRANSIENT_MAX_SECONDS = 0.45
TRANSIENT_ATTACK = 0.25

# Every written grain is peak-normalised here; the score re-applies level from
# the `rms` recorded off the ORIGINAL, so relative loudness survives the trip.
GRAIN_PEAK_DBFS = -1.0


@dataclass
class Grain:
    """One fragment of that room, and everything the score needs to choose it."""

    id: str
    source: str
    kind: str  # bed | sustained | transient
    t0: float  # seconds into the source recording
    seconds: float
    rms: float  # of the ORIGINAL, before normalisation
    peak: float
    centroid: float  # Hz
    brightness: float  # 85% rolloff / Nyquist, in [0, 1]
    flatness: float  # dB; very negative is tonal, near 0 is noise
    decay: float  # dB per second, <= 0. Air is ~0; a sharp event is steeply negative.
    attack: float  # share of energy in the first 30 ms
    zcr: float


def db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def decode(path: Path) -> np.ndarray:
    """One recording as mono float32 at SR, via ffmpeg.

    `-map 0:a:0` because these are .MOV files: the audio is the point and the
    video track is 250 MB of it that we do not want piped through a socket.
    """
    out = subprocess.run(
        # fmt: off
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-f",
            "f32le",
            "-",
        ],
        # fmt: on
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32).astype(np.float64)


def spectra(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Magnitude spectrogram, its frequency axis, and its time axis."""
    freqs, times, z = stft(x, fs=SR, nperseg=WIN, noverlap=WIN - HOP, boundary=None, padded=False)
    return np.abs(z), freqs, times


def onsets(mag: np.ndarray, times: np.ndarray) -> list[float]:
    """Onset times by spectral flux with an adaptive threshold.

    Half-wave-rectified flux: only INCREASES in a bin count, because a bin that
    falls is the tail of the previous event, not the start of a new one. The
    threshold is a local median plus a fixed margin, so a loud passage does not
    swamp a quiet one — the room has both.
    """
    if mag.shape[1] < 3:
        return []
    flux = np.maximum(0.0, np.diff(mag, axis=1)).sum(axis=0)
    flux = flux / (flux.max() or 1.0)

    # Local median over ~0.4 s either side.
    span = max(3, int(0.4 * SR / HOP))
    pad = np.pad(flux, span, mode="edge")
    local = np.array([np.median(pad[i : i + 2 * span + 1]) for i in range(len(flux))])
    thresh = local + 0.06

    hits = []
    last = -1e9
    for i in range(1, len(flux) - 1):
        if flux[i] < thresh[i]:
            continue
        if flux[i] < flux[i - 1] or flux[i] < flux[i + 1]:
            continue  # local maximum only
        t = float(times[i + 1])  # diff shifted the axis by one frame
        if t - last < 0.05:
            continue  # one event, not a flam
        hits.append(t)
        last = t
    return hits


def describe(x: np.ndarray, tail: np.ndarray | None = None) -> dict[str, float] | None:
    """The descriptors that let a grain be chosen without hearing it.

    `tail` is the audio that FOLLOWS the grain in the source, and it exists for
    one reason: decay is measured across the grain plus its tail, never across
    the grain alone. Grains are cut onset-to-onset, so a grain almost always ends
    while its own event is still ringing — measured inside its own bounds, 263 of
    the first 265 grains reported a decay exactly equal to their duration, which
    is not a measurement of anything. The tail is not part of the grain and never
    reaches the film; it is only how far the ring is watched.
    """
    if len(x) < WIN:
        x = np.pad(x, (0, WIN - len(x)))
    mag, freqs, _ = spectra(x)
    if mag.size == 0:
        return None

    power = (mag**2).mean(axis=1)
    total = power.sum()
    if total <= 0:
        return None

    centroid = float((freqs * power).sum() / total)
    cumulative = np.cumsum(power)
    rolloff = float(freqs[int(np.searchsorted(cumulative, 0.85 * total))])
    # Geometric over arithmetic mean, IN DECIBELS. The linear ratio is ~1e-4 for
    # every real recording — rounded for storage it collapsed to four distinct
    # values across the whole bank, an index axis that indexes nothing. In dB it
    # spreads across tens of units and sorts tonal material from noisy.
    positive = power[power > 0]
    flat = float(np.exp(np.log(positive).mean()) / positive.mean()) if len(positive) else 1.0

    # Decay as a SLOPE IN dB PER SECOND — the fitted decay curve itself, not a
    # time derived from it. Two earlier forms of this measurement failed, and the
    # reason is a property of the material rather than of the arithmetic:
    #
    #   "seconds until 20 dB below peak"  — never happens. This room's floor sits
    #     about 20 dB under a voice, so 250 of 265 grains never crossed and each
    #     reported the length of its own lookahead window.
    #   "T20 extrapolated from the slope"  — saturates. Dividing 20 dB by a slope
    #     that is genuinely near zero pinned 131 of 265 grains at the cap.
    #
    # In a live room nothing decays: the air stays at level, so the honest number
    # is how fast a grain falls TOWARD that air, which is a slope. Air reads ~0,
    # a hand clap reads steeply negative, and there is no cap to hit. A consumer
    # that wants a ring time can divide — and can decide for itself what a flat
    # slope means, which is a judgement this function should not be making.
    #
    # Fit from -1 dB (past the attack transient) to -15 dB (before the curve
    # flattens into the floor and biases the slope shallow).
    watched = np.concatenate([x, tail]) if tail is not None and len(tail) else x
    env = np.sqrt((spectra(watched)[0] ** 2).mean(axis=0))
    top = int(env.argmax())
    peak = float(env[top]) if len(env) else 0.0
    decay = 0.0
    if peak > 0 and len(env) - top >= 6:
        curve = 20 * np.log10(np.maximum(env[top:], 1e-12) / peak)
        t_axis = np.arange(len(curve)) * HOP / SR
        under = np.nonzero(curve <= -15.0)[0]
        lo = int(np.argmax(curve <= -1.0)) if (curve <= -1.0).any() else 0
        hi = int(under[0]) if len(under) else len(curve)
        if hi - lo < 4:  # never got 15 dB down — fit the whole tail instead
            lo, hi = 0, len(curve)
        decay = min(0.0, float(np.polyfit(t_axis[lo:hi], curve[lo:hi], 1)[0]))

    head = x[: int(0.03 * SR)]
    energy = float((x**2).sum())
    attack = float((head**2).sum() / energy) if energy > 0 else 0.0
    zcr = float((np.diff(np.signbit(x)) != 0).sum() / max(len(x) - 1, 1))

    return {
        "centroid": round(centroid, 1),
        "brightness": round(rolloff / (SR / 2), 4),
        "flatness": round(10 * math.log10(max(flat, 1e-12)), 2),
        "decay": round(decay, 2),
        "attack": round(attack, 4),
        "zcr": round(zcr, 4),
    }


def envelope(n: int, kind: str) -> np.ndarray:
    """Fades that keep a grain from clicking at either end.

    Bed and sustained grains get long equal-power fades because the score
    crossfades and layers them; a transient gets a near-instant head, because
    fading in an attack is deleting the only interesting part of it.
    """
    fade_in = 0.002 if kind == "transient" else 0.030
    fade_out = 0.015 if kind == "transient" else 0.030
    a = min(int(fade_in * SR), n // 2)
    b = min(int(fade_out * SR), n // 2)
    win = np.ones(n)
    if a:
        win[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if b:
        win[n - b :] = np.sin(np.linspace(np.pi / 2, 0, b)) ** 2
    return win


def cut(x: np.ndarray, source: str, quiet: bool) -> list[tuple[Grain, np.ndarray]]:
    """One recording into grains: a bed by sliding window, events by onset."""
    mag, _, times = spectra(x)
    out: list[tuple[Grain, np.ndarray]] = []

    def emit(t0: float, t1: float, kind: str) -> None:
        i0, i1 = int(t0 * SR), int(t1 * SR)
        seg = x[i0:i1]
        if len(seg) < MIN_GRAIN * SR:
            return
        rms = float(np.sqrt((seg**2).mean()))
        peak = float(np.abs(seg).max())
        if db(rms) < SILENCE_DBFS or peak <= 0:
            return
        d = describe(seg, x[i1 : i1 + int(DECAY_LOOKAHEAD * SR)])
        if d is None:
            return
        if kind == "event":
            sharp = (t1 - t0) <= TRANSIENT_MAX_SECONDS and d["attack"] >= TRANSIENT_ATTACK
            kind = "transient" if sharp else "sustained"

        shaped = seg * envelope(len(seg), kind)
        shaped = shaped / (np.abs(shaped).max() or 1.0) * (10 ** (GRAIN_PEAK_DBFS / 20))
        gid = hashlib.sha256(f"{source}:{t0:.4f}:{t1:.4f}".encode()).hexdigest()[:16]
        out.append(
            (
                Grain(
                    id=gid,
                    source=source,
                    kind=kind,
                    t0=round(t0, 4),
                    seconds=round((i1 - i0) / SR, 4),
                    rms=round(rms, 6),
                    peak=round(peak, 6),
                    **d,
                ),
                shaped.astype(np.float32),
            )
        )

    # The bed: overlapping windows kept only where nothing much happens. `flux`
    # per window measures how eventful it is; the quietest 60% become air.
    flux = np.maximum(0.0, np.diff(mag, axis=1)).sum(axis=0)
    duration = len(x) / SR
    windows: list[tuple[float, float]] = []
    t = 0.0
    while t + BED_WINDOW <= duration:
        lo = int(t * SR / HOP)
        hi = int((t + BED_WINDOW) * SR / HOP)
        windows.append((t, float(flux[lo:hi].mean()) if hi > lo else 0.0))
        t += BED_STRIDE
    if windows:
        calm = np.quantile([w[1] for w in windows], 0.60)
        for t0, score in windows:
            if score <= calm:
                emit(t0, t0 + BED_WINDOW, "bed")

    # The events.
    hits = onsets(mag, times)
    if not quiet:
        print(f"    {len(hits)} onsets, {len(windows)} bed windows")
    for i, t0 in enumerate(hits):
        nxt = hits[i + 1] if i + 1 < len(hits) else duration
        emit(t0, min(nxt, t0 + MAX_GRAIN), "event")

    return out


def room_sources(catalogue: Path) -> list[dict]:
    """Only `room: true`. This is the one gate on what may reach the film."""
    if not catalogue.exists():
        sys.exit(f"no catalogue at {catalogue} — run sound/resolve.py first")
    data = json.loads(catalogue.read_text())
    rooms = [s for s in data.get("sources", []) if s.get("room")]
    if not rooms:
        sys.exit(
            "the catalogue has no `room: true` source.\n"
            "A recording is licensed into the bed by someone looking at a frame and\n"
            "recognising the apartment — never by a date, a filename or a resemblance."
        )
    return rooms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", type=Path, default=CATALOGUE)
    ap.add_argument("--out", type=Path, default=BANK)
    ap.add_argument("--list", action="store_true", help="report what would be cut and stop")
    args = ap.parse_args()

    rooms = room_sources(args.catalogue)
    print(f"the room: {len(rooms)} recordings, {sum(s['seconds'] for s in rooms) / 60:.1f} minutes\n")
    for s in rooms:
        print(f"  {s['seconds']:>7.1f}s  {Path(s['path']).name}")
    if args.list:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    grains: list[Grain] = []
    print()
    for s in rooms:
        path = Path(s["path"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            print(f"  MISSING  {path}")
            continue
        name = path.name
        print(f"  cutting  {name}")
        x = decode(path)
        for grain, audio in cut(x, name, quiet=False):
            wavfile.write(args.out / f"{grain.id}.wav", SR, audio)
            grains.append(grain)

    if not grains:
        sys.exit("no grains — every candidate was below the silence floor")

    grains.sort(key=lambda g: (g.kind, g.source, g.t0))
    payload = {
        "schema": "danse.sound.bank.v1",
        "rate": SR,
        "sources": [{"name": Path(s["path"]).name, "seconds": s["seconds"], "note": s["note"]} for s in rooms],
        "grains": [asdict(g) for g in grains],
    }
    # A fingerprint of the index, so a rendered score can name the bank it was
    # cut from and a re-cut that changes nothing is provably a no-op.
    body = json.dumps(payload["grains"], sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(body.encode()).hexdigest()[:16]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "bank.json").write_text(json.dumps(payload, indent=2) + "\n")

    print()
    for kind in ("bed", "sustained", "transient"):
        pool = [g for g in grains if g.kind == kind]
        if not pool:
            print(f"  {kind:<10} 0")
            continue
        secs = sum(g.seconds for g in pool)
        cen = sum(g.centroid for g in pool) / len(pool)
        dec = sum(g.decay for g in pool) / len(pool)
        print(f"  {kind:<10} {len(pool):>4} grains · {secs:>6.1f}s · centroid {cen:>6.0f} Hz · decay {dec:>6.1f} dB/s")
    print(f"\n{len(grains)} grains · {args.out}/bank.json · fingerprint {payload['fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

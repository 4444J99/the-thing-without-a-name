#!/usr/bin/env python3
"""f(seed, program) -> stereo WAV. The same seed as the picture.

The score is not written to the film; it is DERIVED from it. `control.mjs` runs
the real engine over the real program and reports what the picture is doing —
how deep each plane hangs, where it sits across the frame, which cells swapped
photograph — and this turns that into sound using only grains cut from the two
recordings confirmed to be from that apartment.

    bed        the room's air, continuous, crossfaded end to end. It is under
               everything because the room was under everything.
    voices     eight planes sampled through the depth order. Depth sets pitch
               and darkness, lateral position sets pan, so the chord IS the
               spatial arrangement — when the camera moves and the depth order
               changes, the chord voices itself differently.
    events     a cell swapping photograph fires a transient, DECIMATED to about
               one in eight. Throwing most of them away is the single decision
               that makes the rhythm countable by a dancer rather than by a
               profiler: at 30 re-casts a second, keeping them all is a wash.
    reseed     each epoch turn takes a bed grain down two octaves and swells it,
               then cuts to silence. Shortening across the accelerating reseeds.

Two things this deliberately does NOT do:

  * No synthesis. There is no oscillator anywhere. The sub-bass under a reseed is
    the room itself at quarter speed. If it is in the film, it was in the air of
    that apartment in 2017.
  * No artificial reverb. The material was recorded IN the room, so it already
    carries the room; convolving it with a synthetic hall would only smear what
    is already true. Distance is rendered the way distance actually reaches an
    ear — quieter, darker (air absorbs treble), lower, and narrower — which is
    filtering of the room's own sound rather than an invention of a second space.

    sound/score.py                       # one complete passage
    sound/score.py --window trailer
    sound/score.py --seed 0x5F1E --window reel
    sound/score.py --music-score music/score.json  # fails closed until cleared stems exist
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from scipy.io import wavfile
    from scipy.signal import lfilter, resample_poly
except ModuleNotFoundError as exc:  # keep --help and control-track tests usable on a static checkout
    np = None
    wavfile = None
    lfilter = None
    resample_poly = None
    DEPENDENCY_ERROR = exc
else:
    DEPENDENCY_ERROR = None

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bank_contract import audit_bank  # noqa: E402

BANK = HERE / "bank"
CONTROL = HERE / "control.mjs"
OUT = HERE / "out"

SR = 48_000

# Delivery. Soundscape Park is outdoors and Midnight Moment is Times Square, so
# the true-peak ceiling is not decorative — a limiter downstream that finds an
# inter-sample overshoot will pump the whole bed.
TARGET_LUFS = -16.0
TRUE_PEAK_DBTP = -1.0

# One event in eight. See the module docstring: this is a compositional decision
# with a number attached, not a performance budget.
EVENT_KEEP = 0.125
# And no more than this many in any one second, whatever the picture is doing.
EVENT_MAX_PER_SEC = 6

# How far a plane's depth moves it. `z` runs about -1.1 to +1.1; NEGATIVE IS
# FARTHER (the camera looks down -Z), so a far plane is slowed, darkened and
# pulled toward the middle.
SEMITONES_AT_FAR = -12.0
SEMITONES_AT_NEAR = 4.0
CUTOFF_AT_FAR = 900.0
CUTOFF_AT_NEAR = 12_000.0

# A voice holds its grain this long before re-picking, jittered per slot. Shorter
# and it chatters; longer and it stops tracking the arrangement.
VOICE_SECONDS = (5.0, 11.0)

BED_DB = -20.0
VOICE_DB = -21.0
EVENT_DB = -17.0
SWELL_DB = -14.0


# ── the same hash the picture uses ─────────────────────────────────────────────
# `rng.py` is a value-for-value port of engine/rng.js, so a sound decision and a
# picture decision made from the same seed and the same words agree. It lives in
# its own dependency-free module because `check-danse.py` holds the two
# implementations to identical values on every run.

from rng import pick, rand  # noqa: E402


# ── the bank ───────────────────────────────────────────────────────────────────


def original_level_gain(current_rms: float, target_rms: float) -> float:
    """Undo bank peak-normalisation using the level measured before cutting."""
    return target_rms / current_rms if current_rms > 0 else 1.0


class Bank:
    """The grains, and the index that lets one be chosen without hearing it."""

    def __init__(self, root: Path):
        index = root / "bank.json"
        audit = audit_bank(index)
        if not audit.valid:
            sys.exit(f"invalid grain bank at {index}: {audit.summary()}")
        data = json.loads(index.read_text())
        self.root = root
        self.fingerprint = data.get("fingerprint", "")
        self.sources = data.get("sources", [])
        self.grains = data["grains"]
        self.by_kind: dict[str, list[dict]] = {}
        for g in self.grains:
            self.by_kind.setdefault(g["kind"], []).append(g)
        for pool in self.by_kind.values():
            pool.sort(key=lambda g: g["id"])  # order must not depend on the filesystem
        self._audio: dict[str, np.ndarray] = {}

    def audio(self, grain: dict) -> np.ndarray:
        got = self._audio.get(grain["id"])
        if got is None:
            _, got = wavfile.read(self.root / f"{grain['id']}.wav")
            got = got.astype(np.float64)
            current_rms = float(np.sqrt(np.mean(got**2))) if len(got) else 0.0
            got *= original_level_gain(current_rms, float(grain["rms"]))
            self._audio[grain["id"]] = got
        return got

    def choose(self, kind: str, *words: int, toward: tuple[str, float] | None = None) -> dict | None:
        """One grain, deterministically.

        `toward` biases the draw along an index axis — ("centroid", 300) prefers
        dark material. This is the same shape as `corpus.choose(candidates, ...)`
        on the picture side: weight the pool, then draw from it with the seed.
        """
        pool = self.by_kind.get(kind, [])
        if not pool:
            return None
        if toward is None:
            return pick(pool, *words)
        axis, target = toward
        values = np.array([g[axis] for g in pool], dtype=float)
        scale = float(np.std(values)) or 1.0
        weights = np.exp(-(((values - target) / scale) ** 2))
        total = float(weights.sum())
        if total <= 0:
            return pick(pool, *words)
        r = rand(*words) * total
        idx = int(np.searchsorted(np.cumsum(weights), r))
        return pool[min(idx, len(pool) - 1)]


# ── shaping ────────────────────────────────────────────────────────────────────


def varispeed(x: np.ndarray, semitones: float) -> np.ndarray:
    """Pitch by resampling — tape, not a phase vocoder.

    Duration moves with pitch, which is wanted: a plane hanging further away is
    both lower and longer, the way a slowed recording is. A phase vocoder would
    hold the duration and add the smearing that always comes with it, and there
    is no musical reason here to pay that.
    """
    ratio = 2 ** (semitones / 12.0)
    if abs(ratio - 1.0) < 1e-3 or len(x) < 8:
        return x
    up, down = 1000, max(1, int(round(1000 * ratio)))
    return resample_poly(x, up, down)


def darken(x: np.ndarray, cutoff: float) -> np.ndarray:
    """One-pole lowpass — air absorbing treble over distance."""
    if cutoff >= SR / 2.5 or len(x) < 4:
        return x
    a = math.exp(-2.0 * math.pi * cutoff / SR)
    return lfilter([1.0 - a], [1.0, -a], x)


def fades(n: int, head: float, tail: float) -> np.ndarray:
    win = np.ones(n)
    a, b = min(int(head * SR), n // 2), min(int(tail * SR), n // 2)
    if a:
        win[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if b:
        win[n - b :] = np.sin(np.linspace(np.pi / 2, 0, b)) ** 2
    return win


def place(buf: np.ndarray, mono: np.ndarray, at: int, gain: float, pan: float) -> None:
    """Sum one mono signal into the stereo buffer at a sample offset.

    Equal-power pan, and a width that closes as the plane recedes: `pan` is
    already scaled by the caller, so a far plane arrives near the centre.
    """
    if at >= buf.shape[1] or len(mono) == 0:
        return
    n = min(len(mono), buf.shape[1] - at)
    theta = (np.clip(pan, -1.0, 1.0) + 1.0) * (math.pi / 4)
    buf[0, at : at + n] += mono[:n] * gain * math.cos(theta)
    buf[1, at : at + n] += mono[:n] * gain * math.sin(theta)


def db_to_gain(x: float) -> float:
    return 10 ** (x / 20.0)


# ── loudness, per BS.1770 ──────────────────────────────────────────────────────


def k_weight(x: np.ndarray) -> np.ndarray:
    """The two-stage K filter: a shelf, then a high-pass. Coefficients at 48 kHz."""
    shelf_b = [1.53512485958697, -2.69169618940638, 1.19839281085285]
    shelf_a = [1.0, -1.69065929318241, 0.73248077421585]
    hp_b = [1.0, -2.0, 1.0]
    hp_a = [1.0, -1.99004745483398, 0.99007225036621]
    return lfilter(hp_b, hp_a, lfilter(shelf_b, shelf_a, x))


def lufs(stereo: np.ndarray) -> float:
    """Integrated loudness with the -10 LU relative gate."""
    weighted = np.array([k_weight(ch) for ch in stereo])
    block, step = int(0.400 * SR), int(0.100 * SR)
    if weighted.shape[1] < block:
        return -math.inf
    starts = range(0, weighted.shape[1] - block + 1, step)
    power = np.array([float((weighted[:, s : s + block] ** 2).mean(axis=1).sum()) for s in starts])
    loud = -0.691 + 10 * np.log10(np.maximum(power, 1e-12))

    keep = loud > -70.0  # absolute gate
    if not keep.any():
        return -math.inf
    relative = -0.691 + 10 * np.log10(power[keep].mean()) - 10.0
    keep &= loud > relative  # relative gate
    if not keep.any():
        return -math.inf
    return float(-0.691 + 10 * np.log10(power[keep].mean()))


def true_peak_db(stereo: np.ndarray) -> float:
    """4x oversampled peak — what a downstream converter will actually see."""
    up = np.array([resample_poly(ch, 4, 1) for ch in stereo])
    return 20 * math.log10(max(float(np.abs(up).max()), 1e-12))


# ── the score ──────────────────────────────────────────────────────────────────


def depth_to(z: float, lo: float, hi: float) -> float:
    """Map a plane's signed depth into a range. Negative z is farther."""
    u = float(np.clip((z + 1.1) / 2.2, 0.0, 1.0))
    return lo + (hi - lo) * u


def local_time(control: dict, absolute_time: float) -> float:
    """Rebase an absolute river timestamp into the finite capture buffer."""
    return max(0.0, float(absolute_time) - float(control.get("t0", 0.0)))


def music_event_plan(control: dict) -> list[dict]:
    """The compiled MIDI note/cue starts the JS engine mapped onto this capture."""
    music = control.get("music") if isinstance(control.get("music"), dict) else {}
    events = music.get("events") if isinstance(music.get("events"), list) else []
    return sorted(events, key=lambda event: (float(event["at"]), event["type"], int(event["index"])))


def render(control: dict, bank: Bank, quiet: bool = False) -> np.ndarray:
    seed = int(control["seed"])
    rate = float(control["rate"])
    frames = control["frames"]
    total = int(round(control["duration"] * SR))
    buf = np.zeros((2, total), dtype=np.float64)
    if control.get("music"):
        stems = control["music"].get("stems") or []
        missing = [stem["id"] for stem in stems if not stem.get("audio_source_sha256")]
        if missing:
            raise ValueError(
                "score-driven audio is not renderable until Anthony selects repertoire and every stem/sample "
                f"has cleared source bytes; missing {', '.join(missing)}"
            )
        raise ValueError("score-driven stem rendering is gated until the selected stem-to-bank mapping is registered")
    counts = {"bed": 0, "voice": 0, "event": 0, "swell": 0}

    # ── bed ────────────────────────────────────────────────────────────────────
    # Crossfaded end to end for the whole window. The one continuous thing.
    bed_pool = bank.by_kind.get("bed", [])
    if bed_pool:
        overlap = int(1.2 * SR)
        at = 0
        i = 0
        while at < total:
            g = pick(bed_pool, seed, i, 601)
            a = bank.audio(g)
            a = a * fades(len(a), 1.2, 1.2)
            # Alternate the pan so successive grains open the image rather than
            # stacking dead centre.
            place(buf, a, at, db_to_gain(BED_DB), (rand(seed, i, 602) - 0.5) * 0.5)
            at += max(len(a) - overlap, SR)
            i += 1
            counts["bed"] += 1

    # ── voices ─────────────────────────────────────────────────────────────────
    # Each slot holds a grain for a while, then re-picks. It takes the depth at
    # the moment it picks, so a voice is a photograph of the arrangement, held.
    n_voices = int(control.get("voices", 8))
    for slot in range(n_voices):
        t = 0.0
        take = 0
        while t < control["duration"]:
            fi = min(len(frames) - 1, int(t * rate))
            frame = frames[fi]
            span = VOICE_SECONDS[0] + (VOICE_SECONDS[1] - VOICE_SECONDS[0]) * rand(seed, slot, take, 701)
            voices = frame["v"]
            if not voices or frame["cut"] == "black":
                t += span
                take += 1
                continue
            z, opacity, area, x = voices[min(slot, len(voices) - 1)]

            semis = depth_to(z, SEMITONES_AT_FAR, SEMITONES_AT_NEAR)
            cutoff = depth_to(z, CUTOFF_AT_FAR, CUTOFF_AT_NEAR)
            # Far planes are dark, so ask the index for dark material and then
            # darken it further — selection first, filtering second.
            g = bank.choose("sustained", seed, slot, take, 702, toward=("centroid", depth_to(z, 350.0, 1800.0)))
            if g is None:
                break
            a = darken(varispeed(bank.audio(g), semis), cutoff)
            a = a * fades(len(a), 0.35, 0.9)

            # A flat arrangement (spread 0) is the 2017 composite: one picture
            # plane, no depth to voice. The chord closes to a single centred note.
            width = 0.15 + 0.85 * min(1.0, frame["sp"] * 1.6)
            gain = db_to_gain(VOICE_DB) * (0.45 + 0.55 * opacity) * (0.6 + 0.4 * min(1.0, area * 40))
            place(buf, a, int(t * SR), gain, x * width)
            counts["voice"] += 1
            t += span
            take += 1

    # ── events ─────────────────────────────────────────────────────────────────
    kept_in_second: dict[int, int] = {}
    index = 0
    for frame in frames:
        for z, area, x in frame["e"]:
            index += 1
            if rand(seed, index, 801) >= EVENT_KEEP:
                continue
            at_seconds = local_time(control, frame["t"])
            second = int(at_seconds)
            if kept_in_second.get(second, 0) >= EVENT_MAX_PER_SEC:
                continue
            kept_in_second[second] = kept_in_second.get(second, 0) + 1

            g = bank.choose("transient", seed, index, 802, toward=("centroid", depth_to(z, 500.0, 2200.0)))
            if g is None:
                break
            a = darken(varispeed(bank.audio(g), depth_to(z, -7.0, 3.0)), depth_to(z, 1400.0, 14_000.0))
            a = a * fades(len(a), 0.001, 0.03)
            width = 0.2 + 0.8 * min(1.0, frame["sp"] * 1.6)
            gain = db_to_gain(EVENT_DB) * (0.5 + 0.5 * min(1.0, area * 60))
            place(buf, a, int(at_seconds * SR), gain, x * width)
            counts["event"] += 1

    # ── reseed ─────────────────────────────────────────────────────────────────
    # The room at quarter speed, swelling, then cut. Not a synthesised sub — the
    # same air, two octaves down.
    turns = []
    last_epoch = frames[0]["ep"] if frames else 0
    for frame in frames:
        if frame["ep"] != last_epoch:
            turns.append(local_time(control, frame["t"]))
            last_epoch = frame["ep"]
    for k, when in enumerate(turns):
        g = bank.choose("bed", seed, k, 901)
        if g is None:
            break
        a = varispeed(bank.audio(g), -24.0)
        # Shortening across the accelerating reseeds — the film compresses, and
        # the sound has to compress with it or it reads as a mistake.
        seconds = max(1.6, 6.0 * (0.72**k))
        a = a[: int(seconds * SR)]
        a = a * np.linspace(0.0, 1.0, len(a)) ** 2 * fades(len(a), 0.05, 0.25)
        place(buf, a, max(0, int((when - seconds * 0.82) * SR)), db_to_gain(SWELL_DB), 0.0)
        counts["swell"] += 1

    if not quiet:
        print(
            f"  {counts['bed']} bed · {counts['voice']} voice notes · "
            f"{counts['event']}/{index} events kept · {counts['swell']} reseed swells"
        )
    return buf


def limit(stereo: np.ndarray, ceiling_db: float, lookahead: float = 0.005, release: float = 0.20) -> np.ndarray:
    """Look-ahead peak limiter, channels linked so the image does not wander.

    A plain attenuation cannot satisfy both delivery numbers at once: pulling the
    whole file down to clear -1 dBTP took the first render from -16 to -18 LUFS,
    and turning it back up puts the peaks straight back. The peaks have to come
    down WITHOUT the body coming with them, which is what a limiter is.

    Gain reduction is computed per sample, taken as a running minimum across the
    look-ahead window (so the reduction is already in place before the transient
    arrives — that is the attack), then released with a one-pole so it recovers
    smoothly rather than pumping on every hit.
    """
    from scipy.ndimage import minimum_filter1d

    ceiling = db_to_gain(ceiling_db)
    peak = np.abs(stereo).max(axis=0)
    if peak.max() <= ceiling:
        return stereo

    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    w = max(1, int(lookahead * SR))
    need = minimum_filter1d(need, size=2 * w + 1, mode="nearest")

    # Release, without a per-sample Python loop over 19 million samples.
    #
    # A one-pole lowpass alone is unusable here: where `need` dips it lags, so
    # the applied gain stays ABOVE what the peak required and the ceiling is
    # breached exactly where it matters. Taking the elementwise minimum of the
    # raw requirement and its smoothed version fixes that by construction — the
    # result is never greater than `need`, so attack stays immediate, while after
    # a dip the smoothed branch is what recovers, slowly. That is a release.
    a = math.exp(-1.0 / (release * SR))
    smooth = lfilter([1.0 - a], [1.0, -a], need, zi=np.array([a * need[0]]))[0]
    return stereo * np.minimum(need, smooth)


def normalise(raw: np.ndarray, quiet: bool = False) -> np.ndarray:
    """To -16 LUFS with the true peak under -1 dBTP — both, not one of them.

    Two knobs, each moved only by measurement, and every pass starts from the
    unprocessed render so limiting never compounds on itself:

      gain   what it takes to hit the loudness target. Limiting costs a little
             loudness, so this is re-derived from the limited result.
      guard  how far BELOW the ceiling the limiter is actually set. The limiter
             works on sample peaks and the spec is on inter-sample peaks, which
             are strictly higher — the first master limited to -1.0 and delivered
             -0.69 dBTP. Rather than guess the overshoot, measure it and open the
             guard by exactly what was over.
    """
    measured = lufs(raw)
    if not math.isfinite(measured):
        return raw
    gain_db = TARGET_LUFS - measured
    guard = 0.3
    out = raw
    for _ in range(8):
        out = limit(raw * db_to_gain(gain_db), TRUE_PEAK_DBTP - guard)
        tp, loud = true_peak_db(out), lufs(out)
        if tp > TRUE_PEAK_DBTP:
            guard += (tp - TRUE_PEAK_DBTP) + 0.05
            continue
        if abs(loud - TARGET_LUFS) < 0.15:
            break
        gain_db += TARGET_LUFS - loud
    loud, tp = lufs(out), true_peak_db(out)
    if not quiet:
        flag = "" if abs(loud - TARGET_LUFS) < 0.5 and tp <= TRUE_PEAK_DBTP + 0.05 else "   <-- OFF SPEC"
        print(f"  {loud:.2f} LUFS · true peak {tp:.2f} dBTP{flag}")
    return out


def control_track(
    window: str,
    seed: int | None,
    rate: int,
    start: float = 0.0,
    stream: int = 0,
    music_score: Path | None = None,
) -> dict:
    """Read the picture's absolute control span for the requested capture."""
    cmd = [
        "node",
        str(CONTROL),
        "--window",
        window,
        "--rate",
        str(rate),
        "--from",
        str(start),
        "--stream",
        str(stream),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if music_score is not None:
        cmd += ["--score", str(music_score)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"control track failed:\n{done.stderr.strip()}")
    return json.loads(done.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", default="passage", help="any capture declared in render/program.json")
    ap.add_argument("--seed", help="override the program seed; accepts 0x notation")
    ap.add_argument("--stream", type=int, default=0, help="optional 32-bit passage-stream discriminator")
    ap.add_argument("--rate", type=int, default=30, help="control-track sampling rate in Hz")
    ap.add_argument(
        "--from",
        "--start",
        dest="start",
        type=float,
        default=0.0,
        help="absolute seconds into the river where recording begins",
    )
    ap.add_argument("--bank", type=Path, default=BANK)
    ap.add_argument(
        "--music-score",
        type=Path,
        default=None,
        help="opt into a compiled score contract; fixture audio fails closed until cleared stems are registered",
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.start < 0:
        ap.error("--from/--start must be non-negative")
    if not 0 <= args.stream <= 0xFFFFFFFF:
        ap.error("--stream must be a 32-bit unsigned integer")
    if DEPENDENCY_ERROR is not None:
        ap.error(
            f"sound dependencies unavailable ({DEPENDENCY_ERROR}); run this command in the Danse media environment"
        )

    seed = int(args.seed, 0) if args.seed else None
    bank = Bank(args.bank)
    control = control_track(args.window, seed, args.rate, args.start, args.stream, args.music_score)

    print(
        f"{control['title']} · {control['capture']} · seed 0x{control['seed']:X} · "
        f"{control['duration']:.1f}s from {control['t0']:.1f}s"
    )
    print(f"  bank {bank.fingerprint} · {len(bank.grains)} grains from {len(bank.sources)} recordings")

    stereo = normalise(render(control, bank))

    out = args.out or (OUT / f"{control['capture']}-0x{control['seed']:X}.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(out, SR, stereo.T.astype(np.float32))
    print(f"  {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

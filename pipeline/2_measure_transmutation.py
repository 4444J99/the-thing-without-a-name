#!/usr/bin/env python3
"""danse — stage 2: measure the 2017 transmutation.

The hand-made 2017 composite is not a mood board, it is a *specification*. It is the
composition seen from the one viewpoint at which every plane lines up — which is
exactly the state the engine calls `projK = 1`. Measuring its seam geometry turns it
into an attractor the generator can converge onto and dissolve back out of.

What it measures:

  columns   x positions of the vertical tile seams
  rows      y positions of the horizontal band seams
  lines     the room's own architecture (wall/poster-top/poster-bottom/carpet), which
            is what the bands are aligned to and what makes the composite read as one
            continuous room rather than a collage

Seams are found as ridges in the column/row-summed gradient magnitude: a tile boundary
is a vertical line along which many rows change abruptly at once, which is rare in a
photograph of a wall and common at a composite edge.

Usage: 2_measure_transmutation.py <image> [<image> ...] -o <out.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from corpus_contract import missing_measurement_inputs

# A seam must stand this many robust deviations above the local background of the
# gradient profile. Tuned to accept the composite's real edges while rejecting the
# high-contrast content edges inside a poster.
SEAM_SIGMA = 2.4
# Seams closer together than this fraction of the axis are the same seam found twice.
SEAM_MIN_GAP = 0.018


def _profile_peaks(profile: np.ndarray, sigma: float, min_gap_frac: float) -> list[int]:
    """Return indices where `profile` ridges above a robust local baseline."""
    n = len(profile)
    # Median/MAD rather than mean/std: a handful of very strong seams would otherwise
    # inflate the threshold enough to hide the weaker ones.
    med = float(np.median(profile))
    mad = float(np.median(np.abs(profile - med))) or 1e-9
    thresh = med + sigma * 1.4826 * mad

    candidates = [
        i
        for i in range(1, n - 1)
        if profile[i] >= thresh and profile[i] >= profile[i - 1] and profile[i] >= profile[i + 1]
    ]

    # Non-maximum suppression by strength.
    candidates.sort(key=lambda i: -profile[i])
    min_gap = max(2, int(n * min_gap_frac))
    kept: list[int] = []
    for i in candidates:
        if all(abs(i - k) >= min_gap for k in kept):
            kept.append(i)
    return sorted(kept)


def _gradient_profiles(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Seam-likelihood profiles along x and y.

    Summing gradient magnitude finds the wrong thing: one high-contrast limb against a
    white wall out-scores a real tile boundary. What distinguishes a composite seam is
    *agreement* — a tile edge is a straight discontinuity that most rows cross at the
    same x, whereas a limb edge is confined to a few dozen rows.

    So score each column by the FRACTION of rows whose horizontal gradient there is
    locally exceptional, not by the magnitude sum. Symmetrically for rows.
    """
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))

    def agreement(d: np.ndarray, axis: int) -> np.ndarray:
        # Per-line robust threshold, so a dark poster row and a bright wall row each
        # get judged against their own contrast, not a global one.
        med = np.median(d, axis=axis, keepdims=True)
        mad = np.median(np.abs(d - med), axis=axis, keepdims=True) + 1e-6
        exceptional = d > (med + 3.0 * 1.4826 * mad)
        return exceptional.mean(axis=axis).astype(np.float32)

    return agreement(dx, 0), agreement(dy, 1)


def room_lines(path: Path) -> dict:
    """Locate the room's horizontal architecture from a CLEAN room frame.

    This must be measured on a frame with no dancer in it (the shoot's IMG_1570), never
    on the composite: a composite is mostly limbs, and its row-mean luminance tracks
    skin and fabric rather than the wall. Measuring the architecture on the architecture
    is the whole point — an earlier version read the composite and returned nonsense.

    The clean frame is a textured white wall above a row of dark-framed posters standing
    on carpet, so row-mean luminance has an unmistakable shape: bright plateau (wall),
    sharp drop (poster tops), dark plateau (posters), rise (carpet). Those transitions
    are the lines every plane in the engine must agree on.
    """
    gray = np.asarray(Image.open(path).convert("L"), dtype=np.float32) / 255.0
    h = gray.shape[0]
    rows = gray.mean(axis=1)
    smooth = np.convolve(rows, np.ones(9) / 9, mode="same")
    d = np.diff(smooth)

    # Ignore the outer margins, where vignetting and the frame edge dominate.
    lo = int(h * 0.08)
    hi = min(int(h * 0.97), len(d))
    if hi <= lo:
        return {}
    seg = d[lo:hi]

    drop = lo + int(np.argmin(seg))  # wall → poster tops (steepest darkening)
    rise = lo + int(np.argmax(seg))  # posters → carpet (steepest brightening)

    return {
        "source": path.name,
        "poster_top": round(drop / h, 4),
        "poster_bottom": round(rise / h, 4),
        "wall_luma": round(float(rows[:drop].mean()), 4) if drop > 0 else None,
        "floor_luma": round(float(rows[rise:].mean()), 4) if rise < h - 1 else None,
    }


def measure(path: Path) -> dict:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0

    col, row = _gradient_profiles(gray)
    xs = _profile_peaks(col, SEAM_SIGMA, SEAM_MIN_GAP)
    ys = _profile_peaks(row, SEAM_SIGMA, SEAM_MIN_GAP)

    # Per-tile-column saturation: the 2017 piece desaturates some tiles and not others,
    # and that treatment is one of the grammar's variables. Sampling it per column band
    # tells the engine how far to push the effect.
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32) / 255.0
    sat = hsv[:, :, 1]
    bounds = [0] + [x + 1 for x in xs] + [w]
    bands = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 4:
            continue
        bands.append(
            {
                "x0": round(a / w, 4),
                "x1": round(b / w, 4),
                "saturation": round(float(sat[:, a:b].mean()), 4),
                "luma": round(float(gray[:, a:b].mean()), 4),
            }
        )

    return {
        "file": path.name,
        "w": w,
        "h": h,
        "aspect": round(w / h, 4),
        "columns": [round((x + 1) / w, 4) for x in xs],
        "rows": [round((y + 1) / h, 4) for y in ys],
        "column_count": len(xs) + 1,
        "row_count": len(ys) + 1,
        "bands": bands,
        "saturation_mean": round(float(sat.mean()), 4),
        "saturation_spread": round(float(sat.std()), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--room-frame", type=Path, help="a dancer-free frame to measure the room architecture from")
    args = ap.parse_args()

    missing = missing_measurement_inputs(args.images, args.room_frame)
    if missing:
        for path in missing:
            print(f"missing: {path}", file=sys.stderr)
        return 1

    results = []
    for p in args.images:
        m = measure(p)
        results.append(m)
        print(
            f"{p.name}: {m['column_count']} columns × {m['row_count']} rows, "
            f"aspect {m['aspect']}, sat spread {m['saturation_spread']}"
        )
        print(f"  columns {m['columns']}")
        print(f"  rows    {m['rows']}")

    lines = {}
    if args.room_frame:
        lines = room_lines(args.room_frame)
        print(f"room lines from {args.room_frame.name}: {lines}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"schema": "danse.transmutation.v1", "lines": lines, "transmutations": results},
            indent=2,
            sort_keys=True,
        )
    )
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

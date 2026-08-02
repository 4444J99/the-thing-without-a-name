#!/usr/bin/env python3
"""danse — stage 3: solve the 2017 transmutation back into a score.

Measuring the composite's seams (stage 2) told us where the cuts are. It did not tell us
*what is inside them* — and that is the part worth having. The 2017 piece was cut by
feeling from a pile of frames; if we can recover which frame each region came from and
what treatment was applied to it, the hand-made still stops being a reference image and
becomes an executable score. Render the score and you get the 2017 piece back. Perturb the
score's seed and you get everything adjacent to it.

The corpus makes this tractable. The camera was locked off, the frames are 3264x2448, and
the composite is 1024x768 — the *same 4:3*. So the dominant hypothesis is that each tile
sits at its source position, and the unknown is *which frame*, not where.

The model, per rectangle:

    C  =  gain * S  +  lift          per colour channel, least squares

which is not merely tolerating noise. Normal-blending a photograph at opacity a over a
light ground is exactly `gain = a, lift = (1-a) * ground`, and desaturating is exactly a
per-channel spread in gain. So the two 3-vectors this recovers ARE the 2017 treatment, in
the form a shader wants them.

Three stages:

  MOMENTS    Reduce every block of every frame to five sums, and summed-area-tabulate
             them. After this, fitting ANY block-aligned rectangle against all 162 frames
             costs a few array lookups instead of a pass over its pixels — which is what
             makes an error-driven search affordable at all.

  PARTITION  Greedily split the rectangle with the largest total squared error, choosing
             the cut that most reduces it. The leaf budget is a rate/distortion knob, and
             sweeping it answers the real question: how many rectangles IS this piece?

  REFINE     Slide every cut to the exact pixel that minimises seam error, then refit each
             final rectangle against the whole corpus. Block-quantised edges are otherwise
             the dominant error term — interiors solve cleanly, boundaries do not.

Usage:
    3_reconstruct.py --target T-2017-full.png --frames .work/raw -o score.json
    3_reconstruct.py ... --sweep 8,16,32,64,128,256      # rate/distortion curve
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

# Block edge in composite pixels. This is the granularity of the cut SEARCH; it is not the
# final edge precision, because REFINE moves every chosen cut to the exact pixel.
BLOCK = 16

# A recovered gain outside this range is not a plausible compositing treatment, it is the
# fit exploiting a flat region. Clamp and re-score so those frames lose honestly.
GAIN_MIN, GAIN_MAX = 0.0, 4.0

# How far REFINE may slide a cut from its block-quantised position, in blocks.
REFINE_SPAN = 1.0


# ---------------------------------------------------------------- corpus


def load_stack(frames_dir: Path, size: tuple[int, int], cache: Path) -> tuple[np.ndarray, list[str]]:
    """Every source frame, downscaled to composite space, as uint8 (n, h, w, 3).

    Cached to disk: decoding 162 eight-megapixel JPEGs is the slowest step, and this script
    is meant to be re-run while tuning, so paying it once matters.
    """
    names = sorted(p.name for p in frames_dir.iterdir() if p.suffix.upper() in {".JPG", ".JPEG", ".PNG"})
    if cache.exists():
        blob = np.load(cache, allow_pickle=False)
        cached = list(np.load(cache.with_suffix(".names.npy"), allow_pickle=True))
        if cached == names and blob.shape[1:3] == (size[1], size[0]):
            return blob, names

    w, h = size
    out = np.empty((len(names), h, w, 3), dtype=np.uint8)
    t0 = time.time()
    for i, n in enumerate(names):
        im = Image.open(frames_dir / n).convert("RGB")
        if im.size != size:
            im = im.resize(size, Image.LANCZOS)
        out[i] = np.asarray(im)
        if (i + 1) % 40 == 0:
            print(f"  decoded {i + 1}/{len(names)}  ({time.time() - t0:.0f}s)", file=sys.stderr)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, out)
    np.save(cache.with_suffix(".names.npy"), np.array(names, dtype=object))
    return out, names


# ---------------------------------------------------------------- the fit


def fit_sums(n, sx, sxx, sxy, sy, syy):
    """Least-squares `y = gain*x + lift` from raw moments; broadcasts on a trailing (..., 3).

    A degenerate region — a source patch of flat white wall, say — has no slope to recover.
    Rather than let the division blow up and hand that frame a spuriously perfect score,
    the fit collapses to `gain=0, lift=mean(y)`, whose residual is the region's own
    variance. That is the truthful statement: this frame explains nothing here beyond a
    constant.

    Returns (gain, lift, sse) with sse the summed squared error over the region's samples.
    """
    denom = n * sxx - sx * sx
    flat = denom < 1e-9
    gain = np.where(flat, 0.0, (n * sxy - sx * sy) / np.where(flat, 1.0, denom))
    gain = np.clip(gain, GAIN_MIN, GAIN_MAX)
    lift = (sy - gain * sx) / n
    # SSE for arbitrary (gain, lift): the closed-form shortcut holds only at the
    # least-squares optimum, and the clamp above may have moved us off it.
    sse = syy + gain * gain * sxx + n * lift * lift - 2 * gain * sxy - 2 * lift * sy + 2 * gain * lift * sx
    return gain, lift, np.maximum(sse, 0.0)


def fit_rect(C, stack, rect, frames=None):
    """Fit candidate frames to one exact-pixel rectangle. Returns (index, gain, lift, sse)."""
    x0, y0, x1, y1 = rect
    y = C[y0:y1, x0:x1].reshape(1, -1, 3)
    sel = np.arange(stack.shape[0]) if frames is None else np.asarray(frames)
    x = stack[sel, y0:y1, x0:x1].reshape(len(sel), -1, 3).astype(np.float32) / 255.0
    yr = np.repeat(y, len(sel), axis=0)
    n = x.shape[1]
    g, li, sse = fit_sums(
        n,
        x.sum(1),
        np.einsum("bnc,bnc->bc", x, x),
        np.einsum("bnc,bnc->bc", x, yr),
        y.sum(1),
        np.einsum("bnc,bnc->bc", y, y),
    )
    total = sse.sum(axis=1)
    k = int(total.argmin())
    return int(sel[k]), g[k], li[k], float(total[k])


def clipped_sse(C, stack, rect, layers, lift):
    """Squared error of what would actually be DRAWN, after clipping to [0, 1].

    The least-squares fits minimise unclipped error, but the render clips. A two-layer
    pair with large cancelling gains can therefore win on paper and lose on screen — which
    is exactly what made the rate/distortion curve turn back on itself above 256
    rectangles. Scoring the clipped result closes that gap: a model is accepted only if the
    picture it paints is better.
    """
    x0, y0, x1, y1 = rect
    acc = np.array(lift, dtype=np.float32)[None, None, :] + np.zeros((y1 - y0, x1 - x0, 3), np.float32)
    for idx, gain in layers:
        acc += stack[idx, y0:y1, x0:x1].astype(np.float32) / 255.0 * np.asarray(gain, dtype=np.float32)
    r = np.clip(acc, 0.0, 1.0) - C[y0:y1, x0:x1]
    return float((r * r).sum())


def rank_rect(C, stack, rect, k):
    """The k frames that best explain this rectangle alone, best first."""
    x0, y0, x1, y1 = rect
    y = C[y0:y1, x0:x1].reshape(1, -1, 3)
    x = stack[:, y0:y1, x0:x1].reshape(stack.shape[0], -1, 3).astype(np.float32) / 255.0
    yr = np.repeat(y, x.shape[0], axis=0)
    n = x.shape[1]
    sse = fit_sums(
        n,
        x.sum(1),
        np.einsum("bnc,bnc->bc", x, x),
        np.einsum("bnc,bnc->bc", x, yr),
        y.sum(1),
        np.einsum("bnc,bnc->bc", y, y),
    )[2].sum(axis=1)
    return [int(i) for i in np.argsort(sse)[:k]]


def fit_rect_pair(C, stack, rect, cands):
    """Fit `C = g1*S1 + g2*S2 + lift` over one rectangle, best pair from `cands`.

    Where a translucent limb from one frame crosses another frame beneath it, the
    single-source model has to call the underlayer a constant — which is true over flat
    wall and false everywhere interesting. Two sources plus a lift is what the region
    actually is, and it stays a linear least-squares problem, so it stays closed-form.

    Candidates are the frames that already scored well alone, on the reasoning that a
    layer contributing enough to matter contributes enough to be noticed. That turns
    13,041 pairs into 66.

    Returns (i, j, gain_i, gain_j, lift, sse) or None if no pair beats the single fit.
    """
    x0, y0, x1, y1 = rect
    y = C[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
    n = y.shape[0]
    src = {c: stack[c, y0:y1, x0:x1].reshape(-1, 3).astype(np.float64) / 255.0 for c in cands}

    best = None
    for a_i in range(len(cands)):
        for b_i in range(a_i + 1, len(cands)):
            i, j = cands[a_i], cands[b_i]
            sa, sb = src[i], src[j]
            sse = 0.0
            gi = np.empty(3)
            gj = np.empty(3)
            lf = np.empty(3)
            for c in range(3):
                A = np.stack([sa[:, c], sb[:, c], np.ones(n)], axis=1)
                # Normal equations with a whisper of ridge: two frames a fifth of a second
                # apart are very nearly collinear, and without it the solve happily returns
                # +40/-40 gains that cancel.
                G = A.T @ A + 1e-6 * np.eye(3)
                coef = np.linalg.solve(G, A.T @ y[:, c])
                r = y[:, c] - A @ coef
                sse += float(r @ r)
                gi[c], gj[c], lf[c] = coef
            if not (np.all(np.abs(gi) < 4) and np.all(np.abs(gj) < 4)):
                continue
            if best is None or sse < best[-1]:
                best = (i, j, gi, gj, lf, sse)
    return best


class Moments:
    """Block-level summed-area tables — O(1) least-squares fit for any block-aligned rect.

    The five moments (Sx, Sxx, Sxy, Sy, Syy) are additive over disjoint regions, so a
    rectangle's moments are four corner lookups in a cumulative table. That collapses the
    cost of "fit this rectangle against all 162 frames" from proportional-to-its-area down
    to a constant, which is the difference between an error-driven cut search taking ten
    billion operations and taking about a million.

    At BLOCK=16 over 1024x768 the tables are 49x65 per frame: ~37 MB for the whole corpus.
    """

    def __init__(self, C: np.ndarray, stack: np.ndarray, block: int):
        h, w, _ = C.shape
        self.by, self.bx = h // block, w // block
        self.block = block
        self.npx = block * block
        nf = stack.shape[0]

        def blocks(img):
            v = img[: self.by * block, : self.bx * block].reshape(self.by, block, self.bx, block, 3)
            return v.transpose(0, 2, 1, 3, 4).reshape(self.by, self.bx, -1, 3)

        cb = blocks(C)
        sy = cb.sum(2)
        syy = np.einsum("yxnc,yxnc->yxc", cb, cb)

        sx = np.empty((nf, self.by, self.bx, 3), dtype=np.float64)
        sxx = np.empty_like(sx)
        sxy = np.empty_like(sx)
        for f in range(nf):
            fb = blocks(stack[f].astype(np.float32) / 255.0)
            sx[f] = fb.sum(2)
            sxx[f] = np.einsum("yxnc,yxnc->yxc", fb, fb)
            sxy[f] = np.einsum("yxnc,yxnc->yxc", fb, cb)

        def sat(a):
            c = a.cumsum(axis=-3).cumsum(axis=-2)
            pad = [(0, 0)] * c.ndim
            pad[-3] = pad[-2] = (1, 0)
            return np.pad(c, pad)

        self.sx, self.sxx, self.sxy = sat(sx), sat(sxx), sat(sxy)
        self.sy, self.syy = sat(sy), sat(syy)

    def _q(self, t, x0, y0, x1, y1):
        return t[..., y1, x1, :] - t[..., y0, x1, :] - t[..., y1, x0, :] + t[..., y0, x0, :]

    def best(self, x0, y0, x1, y1):
        """(frame, gain, lift, sse) for the frame that best explains this block rect."""
        n = (x1 - x0) * (y1 - y0) * self.npx
        g, li, sse = fit_sums(
            n,
            self._q(self.sx, x0, y0, x1, y1),
            self._q(self.sxx, x0, y0, x1, y1),
            self._q(self.sxy, x0, y0, x1, y1),
            self._q(self.sy, x0, y0, x1, y1),
            self._q(self.syy, x0, y0, x1, y1),
        )
        total = sse.sum(axis=-1)
        k = int(total.argmin())
        return k, g[k], li[k], float(total[k])

    def scan(self, x0, y0, x1, y1, axis):
        """Best-frame SSE for every prefix and suffix along `axis`, in one vectorised pass.

        Returns (positions, sse_low, sse_high): for each interior cut position, the error of
        the region before it and the region after it, each already minimised over all 162
        frames independently. The two sides are free to choose different frames — which is
        the entire point of cutting there.

        The suffix moments are just `whole - prefix`, since moments are additive over
        disjoint regions. That identity also makes the two moment families broadcast
        uniformly: Sx/Sxx/Sxy carry a leading frame axis and Sy/Syy do not, but both obey
        `whole[..., None, :] - prefix`.
        """
        tables = (self.sx, self.sxx, self.sxy, self.sy, self.syy)
        if axis == 1:  # vertical cut, sweeping x
            pos = np.arange(x0 + 1, x1)
            if pos.size == 0:
                return pos, None, None
            prefix = [t[..., y1, x0 + 1 : x1, :] - t[..., y0, x0 + 1 : x1, :]
                      - t[..., y1, x0 : x0 + 1, :] + t[..., y0, x0 : x0 + 1, :] for t in tables]
            span = (y1 - y0) * self.npx
            n_lo, n_hi = (pos - x0) * span, (x1 - pos) * span
        else:  # horizontal cut, sweeping y
            pos = np.arange(y0 + 1, y1)
            if pos.size == 0:
                return pos, None, None
            prefix = [t[..., y0 + 1 : y1, x1, :] - t[..., y0 + 1 : y1, x0, :]
                      - t[..., y0 : y0 + 1, x1, :] + t[..., y0 : y0 + 1, x0, :] for t in tables]
            span = (x1 - x0) * self.npx
            n_lo, n_hi = (pos - y0) * span, (y1 - pos) * span

        whole = [self._q(t, x0, y0, x1, y1)[..., None, :] for t in tables]
        suffix = [w - p for w, p in zip(whole, prefix)]

        # Every sse here is (frames, positions, 3) because Sx is frame-dependent, so the
        # per-position minimum over frames is always along axis 0.
        sse_lo = fit_sums(n_lo.reshape(-1, 1), *prefix)[2].sum(axis=-1).min(axis=0)
        sse_hi = fit_sums(n_hi.reshape(-1, 1), *suffix)[2].sum(axis=-1).min(axis=0)
        return pos, sse_lo, sse_hi


# ---------------------------------------------------------------- partition


class Node:
    __slots__ = ("rect", "axis", "pos", "lo", "hi", "frame", "gain", "lift", "sse")

    def __init__(self, rect):
        self.rect = rect  # (x0, y0, x1, y1); block units until REFINE, pixels after
        self.axis = self.pos = self.lo = self.hi = None
        self.frame = self.gain = self.lift = None
        self.sse = 0.0


def partition(mom: Moments, budget: int) -> Node:
    """Greedily split the rectangle with the largest total squared error.

    Splitting worst-first rather than uniformly is what makes the leaf budget meaningful:
    at any budget, rectangles are spent where the picture actually fails to be explained,
    so the PSNR-vs-leaves curve measures the piece's real complexity rather than an
    arbitrary subdivision depth.

    An earlier version split on *assignment purity* — where the per-block frame winners
    disagreed — and saturated well short of the naive baseline. Purity is the wrong
    signal: a region can be uniformly assigned to one frame while that frame explains it
    badly, and those regions are exactly the ones that need cutting.
    """
    root = Node((0, 0, mom.bx, mom.by))
    root.frame, root.gain, root.lift, root.sse = mom.best(*root.rect)
    heap, tick, leaves = [(-root.sse, 0, root)], 1, 1

    while heap and leaves < budget:
        _, _, node = heapq.heappop(heap)
        x0, y0, x1, y1 = node.rect
        best = None
        for axis in (0, 1):
            pos, lo, hi = mom.scan(x0, y0, x1, y1, axis)
            if lo is None:
                continue
            total = lo + hi
            k = int(total.argmin())
            if best is None or total[k] < best[0]:
                best = (float(total[k]), axis, int(pos[k]))
        # Stop subdividing where a cut buys nothing: the region is as explained as one
        # photograph can explain it, and more rectangles would only be overfitting.
        if best is None or best[0] >= node.sse - 1e-9:
            continue

        _, axis, p = best
        node.axis, node.pos = axis, p
        node.lo = Node((x0, y0, x1, p) if axis == 0 else (x0, y0, p, y1))
        node.hi = Node((x0, p, x1, y1) if axis == 0 else (p, y0, x1, y1))
        for child in (node.lo, node.hi):
            child.frame, child.gain, child.lift, child.sse = mom.best(*child.rect)
            heapq.heappush(heap, (-child.sse, tick, child))
            tick += 1
        leaves += 1
    return root


def walk(node: Node):
    if node.lo is None:
        yield node
    else:
        yield from walk(node.lo)
        yield from walk(node.hi)


def dominant(node: Node) -> int:
    """The frame owning the most area under `node` — its stand-in during REFINE."""
    if node.lo is None:
        return node.frame
    best, area = None, -1
    for leaf in walk(node):
        x0, y0, x1, y1 = leaf.rect
        a = (x1 - x0) * (y1 - y0)
        if a > area:
            best, area = leaf.frame, a
    return best


# ---------------------------------------------------------------- refine


def refine(node: Node, C, stack, block: int, span: int, px=None):
    """Slide every cut to the exact pixel that minimises seam error.

    Block-quantised edges are the dominant remaining error — with interiors solved, what is
    left is a `block`-wide strip along each boundary in which two photographs are averaged
    together. Recovering those edges is worth 3-4 dB, more than any other single step here.

    The cut is scored on a NARROW STRIP either side of it, against candidate frames drawn
    from that strip alone. An earlier version scored whole child rectangles against "the
    frame owning the most area beneath this node", which quietly broke as the tree deepened:
    that stand-in gets less representative with every split, so deep trees refined their
    upper cuts against a worse and worse proxy and the rate/distortion curve turned back on
    itself above 256 rectangles. Local evidence has no such depth dependence.

    Converts the tree in place from block rects to pixel rects.
    """
    if px is None:
        x0, y0, x1, y1 = node.rect
        px = (x0 * block, y0 * block, x1 * block, y1 * block)
    node.rect = px
    if node.lo is None:
        return

    x0, y0, x1, y1 = px
    p0 = node.pos * block
    axis = node.axis
    lo_edge, hi_edge = (y0, y1) if axis == 0 else (x0, x1)

    # A strip twice the search window, so every candidate position leaves `span` pixels of
    # evidence on each side.
    s0, s1 = max(lo_edge, p0 - 2 * span), min(hi_edge, p0 + 2 * span)

    def cut(a, b):
        return (x0, a, x1, b) if axis == 0 else (a, y0, b, y1)

    best_p = p0
    if s1 - s0 >= 4:
        mid = (s0 + s1) // 2
        cand_lo = rank_rect(C, stack, cut(s0, mid), 4)
        cand_hi = rank_rect(C, stack, cut(mid, s1), 4)
        best_e = None
        for p in range(max(s0 + 1, p0 - span), min(s1 - 1, p0 + span) + 1):
            e = fit_rect(C, stack, cut(s0, p), cand_lo)[3] + fit_rect(C, stack, cut(p, s1), cand_hi)[3]
            if best_e is None or e < best_e:
                best_p, best_e = p, e

    node.pos = best_p
    refine(node.lo, C, stack, block, span, cut(y0, best_p) if axis == 0 else cut(x0, best_p))
    refine(node.hi, C, stack, block, span, cut(best_p, y1) if axis == 0 else cut(best_p, x1))


# ---------------------------------------------------------------- solve


def solve(target: Path, C, stack, names, leaves: int, block: int,
          depth: int = 1, topk: int = 12, pair_margin: float = 0.15) -> dict:
    h, w, _ = C.shape
    mom = Moments(C, stack, block)
    root = partition(mom, leaves)
    refine(root, C, stack, block, int(block * REFINE_SPAN))

    # Final answer: refit every exact rectangle against the WHOLE corpus. The block-level
    # winner guided the search, but the rectangle a leaf ended up owning is not the region
    # it won, so the frame choice is re-decided on the evidence that actually applies.
    tiles = []
    for i, leaf in enumerate(walk(root)):
        x0, y0, x1, y1 = leaf.rect
        if x1 <= x0 or y1 <= y0:
            continue
        f, g, li, _ = fit_rect(C, stack, leaf.rect)
        n = (x1 - x0) * (y1 - y0)
        sse = clipped_sse(C, stack, leaf.rect, [(f, g)], li)
        layers = [{"src": names[f], "src_index": f, "gain": [round(float(v), 4) for v in g]}]
        lift = [round(float(v), 4) for v in li]

        if depth > 1 and n >= 64:
            # Rank every frame alone, then look for a second layer among the best few.
            pair = fit_rect_pair(C, stack, leaf.rect, rank_rect(C, stack, leaf.rect, topk))
            # Demand a real margin, measured on the clipped render. Two layers can always
            # shave a little off by fitting noise, and a score that claims a second
            # photograph is present should be claiming it because it is.
            if pair:
                a, b, ga, gb, lf, _ = pair
                pair_sse = clipped_sse(C, stack, leaf.rect, [(a, ga), (b, gb)], lf)
                if pair_sse < sse * (1.0 - pair_margin):
                    sse = pair_sse
                    layers = [
                        {"src": names[a], "src_index": a, "gain": [round(float(v), 4) for v in ga]},
                        {"src": names[b], "src_index": b, "gain": [round(float(v), 4) for v in gb]},
                    ]
                    lift = [round(float(v), 4) for v in lf]

        tiles.append(
            {
                "id": i,
                "px": [x0, y0, x1, y1],
                "rect": [round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5)],
                "layers": layers,
                "lift": lift,
                "rmse": round(float(np.sqrt(sse / (n * 3))), 4),
                "area": round(n / (w * h), 5),
            }
        )
    tiles.sort(key=lambda t: -t["area"])
    return {
        "schema": "danse.score.v1",
        "target": {"file": target.name, "w": w, "h": h},
        "corpus": {"dir": "raw", "frames": len(names)},
        "solver": {"leaves": leaves, "block": block, "refine_span": REFINE_SPAN},
        "tiles": tiles,
    }


def render(score: dict, stack: np.ndarray) -> np.ndarray:
    """Paint the score. Layers sum: the composite IS a sum of gained photographs."""
    w, h = score["target"]["w"], score["target"]["h"]
    out = np.zeros((h, w, 3), dtype=np.float32)
    for t in score["tiles"]:
        x0, y0, x1, y1 = t["px"]
        acc = np.array(t["lift"], dtype=np.float32)[None, None, :].repeat(y1 - y0, 0).repeat(x1 - x0, 1)
        for layer in t["layers"]:
            src = stack[layer["src_index"], y0:y1, x0:x1].astype(np.float32) / 255.0
            acc = acc + src * np.array(layer["gain"], dtype=np.float32)
        out[y0:y1, x0:x1] = acc
    return np.clip(out, 0.0, 1.0)


def falsecolour(score: dict) -> np.ndarray:
    """The provenance map: one hue per source frame, so the score is legible at a glance.

    This is the piece's genome made visible — which instant of that afternoon each region
    was drawn from. It is also the fastest way to tell a real solve from a plausible one:
    real tiles read as flat contiguous plates, a failed solve reads as noise.
    """
    w, h = score["target"]["w"], score["target"]["h"]
    n = score["corpus"]["frames"]
    hue = (np.arange(n) * 0.61803398875) % 1.0
    k = (hue * 6.0).astype(int)
    f = hue * 6.0 - k
    q, t_, one, zero = 1.0 - f, f, np.ones(n), np.zeros(n)
    lut = np.stack(
        [
            np.choose(k, [one, q, zero, zero, t_, one]),
            np.choose(k, [t_, one, one, q, zero, zero]),
            np.choose(k, [zero, zero, t_, one, one, q]),
        ],
        axis=1,
    )
    out = np.zeros((h, w, 3), dtype=np.float32)
    for tile in score["tiles"]:
        x0, y0, x1, y1 = tile["px"]
        out[y0:y1, x0:x1] = lut[tile["layers"][0]["src_index"]]
        out[y0 : y0 + 1, x0:x1] = out[y1 - 1 : y1, x0:x1] = 0.0
        out[y0:y1, x0 : x0 + 1] = out[y0:y1, x1 - 1 : x1] = 0.0
    return out


def residual(recon, C, score) -> dict:
    err = np.abs(recon - C)
    mse = float((err**2).mean())
    return {
        "mae": round(float(err.mean()), 5),
        "rmse": round(float(np.sqrt(mse)), 5),
        "psnr": round(float(10 * np.log10(1.0 / max(mse, 1e-12))), 2),
        "p95": round(float(np.percentile(err, 95)), 5),
        "frames_used": len({lay["src"] for t in score["tiles"] for lay in t["layers"]}),
        "two_layer_tiles": sum(1 for t in score["tiles"] if len(t["layers"]) > 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=Path(".work/cache/stack.npy"))
    ap.add_argument("--block", type=int, default=BLOCK)
    ap.add_argument("--refine-span", type=float, default=REFINE_SPAN)
    ap.add_argument("--leaves", type=int, default=64)
    ap.add_argument("--depth", type=int, default=1, choices=(1, 2), help="max layers per rectangle")
    ap.add_argument("--topk", type=int, default=12, help="single-fit candidates considered for layer 2")
    ap.add_argument("--pair-margin", type=float, default=0.15, help="SSE reduction a 2nd layer must earn")
    ap.add_argument("--sweep", help="comma-separated leaf budgets; prints a rate/distortion curve")
    args = ap.parse_args()

    globals()["REFINE_SPAN"] = args.refine_span
    C = np.asarray(Image.open(args.target).convert("RGB"), dtype=np.float32) / 255.0
    h, w, _ = C.shape
    stack, names = load_stack(args.frames, (w, h), args.cache)
    print(f"target {args.target.name} {w}x{h} · corpus {len(names)} frames", file=sys.stderr)

    budgets = [int(v) for v in args.sweep.split(",")] if args.sweep else [args.leaves]
    curve, best = [], None
    for nleaf in budgets:
        t0 = time.time()
        s = solve(args.target, C, stack, names, nleaf, args.block, args.depth, args.topk, args.pair_margin)
        s["residual"] = residual(render(s, stack), C, s)
        r = s["residual"]
        curve.append({"leaves": len(s["tiles"]), **r})
        print(
            f"  {len(s['tiles']):4d} rect  {r['frames_used']:3d} frames  "
            f"psnr {r['psnr']:6.2f} dB  mae {r['mae']:.5f}  ({time.time() - t0:.0f}s)"
        )
        best = s
    if len(budgets) > 1:
        best["rate_distortion"] = curve

    stem = args.out.with_suffix("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    recon = render(best, stack)
    Image.fromarray((recon * 255).astype(np.uint8)).save(f"{stem}.recon.png")
    Image.fromarray((falsecolour(best) * 255).astype(np.uint8)).save(f"{stem}.provenance.png")
    Image.fromarray((np.clip(np.abs(recon - C).mean(axis=2) * 4, 0, 1) * 255).astype(np.uint8)).save(
        f"{stem}.error.png"
    )
    args.out.write_text(json.dumps(best, indent=2))

    r = best["residual"]
    print(
        f"\n{len(best['tiles'])} rectangles from {r['frames_used']} distinct frames\n"
        f"reconstruction  mae {r['mae']}  rmse {r['rmse']}  psnr {r['psnr']} dB  p95 {r['p95']}\n"
        f"→ {args.out}  (+ .recon.png .provenance.png .error.png)"
    )
    print(f"{r['two_layer_tiles']} of {len(best['tiles'])} rectangles need two layers")
    for t in best["tiles"][:10]:
        srcs = " + ".join(lay["src"].replace(".JPG", "") for lay in t["layers"])
        print(f"  {t['area'] * 100:5.1f}%  {srcs:26s}  lift {t['lift']}  rmse {t['rmse']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

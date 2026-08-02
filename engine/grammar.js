/** How the picture is cut, and what goes in each piece.
 *
 * The 2017 works are three cut-geometries over one operation. Analytic cubism's
 * actual move is not angular shapes, it is SIMULTANEITY — several viewpoints of
 * one subject coexisting in one picture plane. danse cuts a rectangular grid
 * aligned to the room's architecture; noonlight cuts polygonal shards with white
 * kerf; the b/w remix cuts staggered bands keyed to anatomy. Different scissors,
 * same cut.
 *
 * So this module is a VOCABULARY, not a grid. A cut returns cells; the seed picks
 * which scissors. What every cut shares:
 *
 *   - cells are rects in [0,1] with y measured DOWN, the score's convention
 *   - each cell names up to two frames and a weight between them, because the
 *     2017 piece needed two layers for 77 of its 256 tiles and the same machinery
 *     carries the cross-fade in time
 *   - `gain`/`lift` are the recovered hand-treatment: C = gain·S + lift, which is
 *     exactly normal-blend-at-opacity when gain + lift sums to the ground
 *
 * `score` is not a generated cut. It is the 2017 composite itself, solved, and it
 * is what makes the flat state a reproduction rather than a homage.
 */

import { turnover } from "./clock.js";
import { POSTER_BOTTOM, POSTER_TOP } from "./room.js";
import { hash, pick, rand, range } from "./rng.js";

/** The whole vocabulary. `score` is the 2017 composite; `solo` and `figure` are
 *  the film's two ends — one undivided photograph, and one body assembled out of
 *  many; `black` renders nothing and exists so the closing signature is part of
 *  the declared partition rather than a special case in the renderer. */
export const CUTS = ["score", "solo", "grid", "bands", "figure", "black"];

/** What the free-running piece roams between when no program names a cut. */
const ROAMING = ["grid", "bands"];

/** The room's own horizontals. The 2017 cuts landed on these to within 0.4% of
 *  frame height, so a generated cut derives its bands from the room rather than
 *  inventing a rhythm. The recovered room plate put the lower rail at 0.7988
 *  against 0.802 measured and 0.799 solved — three methods, one architecture. */
const ARCHITECTURE = [0, POSTER_TOP, POSTER_BOTTOM, 1];

/** The skeleton, as the corpus actually knows it. Pairs are only used when BOTH
 *  ends were detected, so this list can name more than any one frame carries.
 *
 *  Vision found her legs: knees, ankles and hips appear in 57–65 of 162 frames,
 *  elbows and wrists in 25–43, shoulders and head in 3–7 — the camera is low and
 *  the frame is cropped to the body. The lower body is therefore what `figure`
 *  can actually assemble, which for a dancer is also where the line is. */
const SEGMENTS = [
  ["left_hip", "left_knee"],
  ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"],
  ["right_knee", "right_ankle"],
  ["left_hip", "right_hip"],
  ["root", "left_hip"],
  ["root", "right_hip"],
  ["left_shoulder", "left_elbow"],
  ["left_elbow", "left_wrist"],
  ["right_shoulder", "right_elbow"],
  ["right_elbow", "right_wrist"],
  ["neck", "root"],
];

/** Treatment recovered from the 2017 solve: several tiles came back at
 *  gain ≈ 0.64, lift ≈ 0.36 — pairs summing to 1.0. The solver was never told
 *  about opacity; it fitted a line and the line was the hand-treatment. */
function treatment(seed, id) {
  const a = range(0.52, 0.86, seed, id, 301); // the opacity he was working at
  const ground = range(0.92, 1.0, seed, id, 302); // over a light wall
  const desat = range(0, 0.14, seed, id, 303); // per-channel spread = desaturation
  const lift = (1 - a) * ground;
  return {
    gain: [a + desat, a, a - desat * 0.6],
    lift: [lift, lift, lift * 1.02],
  };
}

/** Fill a cut's cells with photographs, and cross-fade them over time.
 *
 * The choice is made from the manifest index — who was standing in this part of
 * the room — so nothing is downloaded to decide anything.
 */
function cast(cells, corpus, seed, t, rate = 1) {
  return cells.map((cell) => {
    const cands = corpus.candidates(cell.rect);
    const { epoch, next, mix } = turnover(cell.id, seed, t, rate);

    // Falling back to the whole corpus keeps a cell that she never entered from
    // going empty — it shows the room, which is the correct answer for a cell
    // containing only wall.
    const pool = cands.length ? cands : corpus.usable().map((f) => ({ id: f.id, weight: 1 }));
    const a = corpus.choose(pool, seed, cell.id, epoch, 401);
    const b = mix > 0 ? corpus.choose(pool, seed, cell.id, next, 401) : null;

    return {
      ...cell,
      layers: b && b !== a
        ? [{ frame: a, weight: 1 - mix }, { frame: b, weight: mix }]
        : [{ frame: a, weight: 1 }],
      ...treatment(seed, cell.id),
    };
  });
}

// ── the cuts ───────────────────────────────────────────────────────────────────

/** The 2017 composite, verbatim. Every cell is a solved rectangle: which frame it
 *  was cut from and the two numbers that reproduce the treatment. */
function score(corpus) {
  if (!corpus.score) return [];
  return corpus.score.tiles.map((tile) => ({
    id: tile.id,
    rect: tile.rect,
    layers: tile.layers.map((l, i) => ({
      frame: l.src.replace(/\.[^.]+$/, ""),
      weight: 1,
      gain: l.gain,
      solved: true,
      order: i,
    })),
    lift: tile.lift,
    solved: true,
  }));
}

/** One plane, one photograph, whole, untreated.
 *
 * The film opens here, and the point is what is NOT done: no cut, no gain, no
 * lift, no crossfade. Before the machine touches anything the viewer sees what
 * was actually photographed on 20 June 2017. Provenance is an argument you make
 * by showing the source first, not by captioning it afterwards.
 */
function solo(corpus, seed, t) {
  const pool = corpus
    .usable()
    .filter((f) => (f.figure?.coverage ?? 0) > 0.08)
    .map((f) => ({ id: f.id, weight: f.figure.coverage }));
  const frame = corpus.choose(pool.length ? pool : corpus.usable().map((f) => ({ id: f.id, weight: 1 })), seed, 801);
  if (!frame) return [];
  return [
    {
      id: 0,
      rect: [0, 0, 1, 1],
      layers: [{ frame, weight: 1 }],
      gain: [1, 1, 1],
      lift: [0, 0, 0],
      untreated: true,
    },
  ];
}

/** A body assembled out of many photographs — the cut that answers the skeptic.
 *
 * A host frame supplies the POSE; every limb is then re-cast from a different
 * photograph whose same joint lands in the same place. It does not crop for
 * composition, it reaches for a forearm. What holds still at 3:45 is a body that
 * never existed, standing in a room that never existed.
 *
 * The corpus knows legs. Knees, ankles and hips are detected in 57–65 of 162
 * frames; shoulders and head in 3–7, because the camera is low and the frame is
 * cropped to the body. So this cut builds what the material actually supports
 * rather than reaching for a face that was never in evidence.
 */
function figure(corpus, seed, t, { minConfidence = 0.1, rate = 1 } = {}) {
  const articulate = (n) => corpus.usable().filter((f) => Object.keys(f.joints ?? {}).length >= n);
  const hosts = articulate(8).length ? articulate(8) : articulate(6);
  if (!hosts.length) return [];
  const hostId = corpus.choose(
    hosts.map((f) => ({ id: f.id, weight: Object.keys(f.joints).length })),
    seed,
    Math.floor(t / 23),
    901,
  );
  const host = corpus.byId.get(hostId);

  const joints = Object.entries(host.joints).filter(([, j]) => j[2] >= minConfidence);
  if (!joints.length) return [];
  const at = new Map(joints);

  // A plate per anchor: every joint the host has, plus the SEGMENT between each
  // connected pair. Both matter, and the segments matter more — a body is not a
  // scatter of joints, it is the bone between two of them, and "it reaches for a
  // forearm" means elbow-to-wrist, not elbow.
  const anchors = joints.map(([name, j]) => ({ key: name, parts: [name], box: [j[0], j[1], j[0], j[1]] }));
  for (const [a, b] of SEGMENTS) {
    const ja = at.get(a);
    const jb = at.get(b);
    if (!ja || !jb) continue;
    anchors.push({
      key: `${a}~${b}`,
      parts: [a, b],
      box: [Math.min(ja[0], jb[0]), Math.min(ja[1], jb[1]), Math.max(ja[0], jb[0]), Math.max(ja[1], jb[1])],
    });
  }

  const cells = [];
  anchors.forEach((anchor, i) => {
    // Plate size follows the body: a margin around whatever the anchor spans, so
    // an ankle gets an ankle-sized plate and a thigh gets a thigh-sized one.
    const span = Math.max(anchor.box[2] - anchor.box[0], anchor.box[3] - anchor.box[1]);
    const pad = Math.min(0.1, Math.max(0.035, span * 0.34));
    const rect = [
      Math.max(0, anchor.box[0] - pad),
      Math.max(0, anchor.box[1] - pad),
      Math.min(1, anchor.box[2] + pad),
      Math.min(1, anchor.box[3] + pad),
    ];
    const tol = Math.max(0.08, span + pad);

    // The reach: frames whose SAME anatomy sits near the host's, so what arrives
    // is another photograph's version of this limb — not another photograph.
    const reach = [];
    for (const f of corpus.usable()) {
      let d = 0;
      let conf = 1;
      let ok = true;
      for (const part of anchor.parts) {
        const k = f.joints?.[part];
        if (!k || k[2] < minConfidence) {
          ok = false;
          break;
        }
        d = Math.max(d, Math.hypot(k[0] - at.get(part)[0], k[1] - at.get(part)[1]));
        conf = Math.min(conf, k[2]);
      }
      if (!ok || d > tol) continue;
      reach.push({ id: f.id, weight: (1 - d / tol) * conf });
    }
    if (!reach.length) return;

    const { epoch, next, mix } = turnover(i, seed, t, rate);
    const a = corpus.choose(reach, seed, i, epoch, 902);
    const b = mix > 0 ? corpus.choose(reach, seed, i, next, 902) : null;
    cells.push({
      id: i,
      rect,
      anatomy: anchor.key,
      sources: reach.length,
      layers: b && b !== a ? [{ frame: a, weight: 1 - mix }, { frame: b, weight: mix }] : [{ frame: a, weight: 1 }],
      ...treatment(seed, i),
    });
  });
  return cells;
}

/** danse's own scissors, generalised: a kd-subdivision rooted on the room.
 *
 * Not a lattice. The 2017 composite was RECOVERED as a kd-partition — recursive
 * splits, worst cell first — so a generated cut is built the same way rather than
 * as a regular grid the solver would never have produced. The root split is the
 * room's own architecture; everything below it is seeded.
 *
 * `target` sits near the ~146 rectangles the 2017 piece actually spends on
 * composition (its other 110 leaves are 1px solver tail carrying 0.5% of the
 * picture).
 */
function grid(corpus, seed, t, { target = 130, minSide = 0.028, rate = 1 } = {}) {
  let cells = [];
  for (let b = 0; b < ARCHITECTURE.length - 1; b++) {
    cells.push([0, ARCHITECTURE[b], 1, ARCHITECTURE[b + 1]]);
  }

  for (let round = 0; cells.length < target && round < 14; round++) {
    const next = [];
    for (let i = 0; i < cells.length; i++) {
      const [x0, y0, x1, y1] = cells[i];
      const w = x1 - x0;
      const h = y1 - y0;
      // Leaving some cells whole is what keeps the picture from reading as a
      // uniform mesh — the 2017 cut has large plates next to small ones.
      if (Math.max(w, h) < minSide * 2 || rand(seed, round, i, 510) < 0.16) {
        next.push(cells[i]);
        continue;
      }
      // Split the long axis, so cells stay roughly plate-shaped rather than
      // degenerating into the slivers the solver's tail produced.
      const vertical = w > h * 1.15 ? true : h > w * 1.15 ? false : rand(seed, round, i, 511) < 0.5;
      const at = range(0.34, 0.66, seed, round, i, 512);
      if (vertical) {
        const xm = x0 + w * at;
        next.push([x0, y0, xm, y1], [xm, y0, x1, y1]);
      } else {
        const ym = y0 + h * at;
        next.push([x0, y0, x1, ym], [x0, ym, x1, y1]);
      }
    }
    cells = next;
  }
  return cast(cells.map((rect, id) => ({ id, rect })), corpus, seed, t, rate);
}

/** Staggered horizontal bands keyed to anatomy — the b/w remix's scissors. Band
 *  edges are placed at the joint heights the corpus actually found, which for
 *  this shoot means knees, ankles and hips: the cut lands on the body, not on a
 *  ruler. */
function bands(corpus, seed, t, { count = 14, rate = 1 } = {}) {
  // Joint heights across the whole corpus, as a distribution to cut against.
  const heights = [];
  for (const f of corpus.usable()) {
    for (const j of Object.values(f.joints ?? {})) heights.push(j[1]);
  }
  heights.sort((a, b) => a - b);

  const edges = [0];
  for (let i = 1; i < count; i++) {
    const q = i / count;
    const at = heights.length
      ? heights[Math.min(heights.length - 1, Math.floor(q * heights.length))]
      : q;
    // Jitter within the band so successive seeds do not cut identically.
    edges.push(Math.min(0.995, Math.max(0.005, at + range(-0.02, 0.02, seed, i, 601))));
  }
  edges.push(1);
  edges.sort((a, b) => a - b);

  const cells = [];
  let id = 0;
  for (let i = 0; i < edges.length - 1; i++) {
    const y0 = edges[i];
    const y1 = edges[i + 1];
    if (y1 - y0 < 0.01) continue;
    // The stagger: each band is offset horizontally, which is what makes the
    // figure read as displaced rather than merely sliced.
    const shift = range(-0.16, 0.16, seed, i, 602);
    const splits = Math.max(2, Math.round(range(2, 6.4, seed, i, 603)));
    for (let s = 0; s < splits; s++) {
      const x0 = Math.max(0, s / splits + shift);
      const x1 = Math.min(1, (s + 1) / splits + shift);
      if (x1 - x0 > 0.02) cells.push({ id: id++, rect: [x0, y0, x1, y1] });
    }
  }
  return cast(cells, corpus, seed, t, rate);
}

// ── selection ──────────────────────────────────────────────────────────────────

/** The cut in force at (seed, t).
 *
 * `flat` is the 2017 state — at the bottom of every reveal cycle the piece has to
 * BE the composite, not a generated approximation of it, so the score is served
 * whenever the room is folded shut.
 */
export function cells(corpus, seed, t, { reveal = 0, cut = null, rate = 1 } = {}) {
  const chosen = cut ?? cutAt(seed, t, reveal);
  if (chosen === "black") return [];
  if (chosen === "score") {
    const solved = score(corpus);
    // A corpus shipped without the solved score still has to render something,
    // so fall through to the generated cut rather than showing an empty room.
    if (solved.length) return solved;
    return grid(corpus, seed, t, { rate });
  }
  if (chosen === "solo") return solo(corpus, seed, t);
  if (chosen === "figure") return figure(corpus, seed, t, { rate });
  if (chosen === "bands") return bands(corpus, seed, t, { rate });
  return grid(corpus, seed, t, { rate });
}

/** Which cut `cells()` would serve, without building it — for the UI and the HUD. */
export function cutAt(seed, t, reveal) {
  if (reveal < 0.02) return "score";
  return pick(ROAMING, seed, Math.floor(t / 137), 701);
}

export { hash };

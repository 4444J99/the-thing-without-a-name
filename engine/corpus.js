/** That afternoon, indexed — and delivered progressively.
 *
 * The engine has to choose a photograph before it has downloaded one. So the
 * manifest carries geometry rather than pixels: each frame's matte coverage, the
 * bounding box and centroid of the figure, and whichever joints Vision could find.
 * `candidates()` answers "who is standing in this part of the room?" out of that
 * index alone, and the picture is fetched only once the choice is made.
 *
 * Two tiers exist because 162 photographs at screen resolution is 22 MB and a
 * gallery machine should not stare at a blank wall for 22 MB. `browse` (512px) is
 * the whole corpus, small enough to ship eagerly; `screen` (1024px) arrives per
 * frame, on demand, and swaps in mid-render. `plate()` therefore never blocks and
 * never returns nothing once the eager tier has landed — it returns the best
 * thing that exists right now and quietly asks for better.
 */

import { texture } from "./gl.js";
import { hash } from "./rng.js";

/** Because every frame came from one locked-off camera, all 162 photographs are
 *  already registered to each other and to the room. That is what lets a plane
 *  showing IMG_1611 and a plane showing IMG_1588 sample through one projector
 *  matrix and still line up: the registration was done in 2017, by not moving. */
/** The tiers the web bundle ships. The manifest is authoritative and may declare
 *  more — the offline renderer builds a `film` tier at full camera resolution
 *  (~250 MB, local-only, never in git), and asks for it by name. */
export const TIERS = ["browse", "screen"];

/** A tile whose short side is one pixel is solver tail-noise, not composition.
 *  The kd-partition spent its last splits on slivers: of the 2017 score's 256
 *  leaves, 100 are 1px wide and 110 have a short side under 4px — and all 110
 *  together carry 0.48% of the picture. 116 tiles have both sides ≥17px.
 *
 *  That is an independent confirmation of what the rate/distortion curve said
 *  from the other direction: the piece is about a hundred rectangles. The tail
 *  is the solver buying a fifth of a decibel, not the artist making a cut.
 *
 *  They are COUNTED, not dropped. "Recreate it exactly" is the standing
 *  instruction, and 256 draw calls costs nothing; a consumer that wants the
 *  composition rather than the reproduction can filter on `sliver`. */
export const SLIVER_PX = 4;

export async function load(base = "corpus/") {
  const manifest = await fetch(`${base}manifest.json`).then((r) => {
    if (!r.ok) throw new Error(`corpus manifest ${r.status} at ${base}manifest.json`);
    return r.json();
  });
  // Tiers built locally and never committed — the 245 MB `film` tier the 4K
  // master needs. Absent on every fresh checkout, and that is correct: a shipped
  // manifest advertising plates that are not in the repo would send every visitor
  // looking for 404s. Present only on the machine that built them.
  const local = await fetch(`${base}manifest.local.json`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  if (local?.tiers) Object.assign(manifest.tiers, local.tiers);

  const score = manifest.score
    ? await fetch(`${base}${manifest.score}`).then((r) => (r.ok ? r.json() : null))
    : null;
  return fromData(base, manifest, score);
}

/** The same corpus, from data already in hand.
 *
 * `load()` is the browser's path and needs `fetch`. Everything the GRAMMAR asks
 * of a corpus is pure index — `usable`, `candidates`, `choose`, `byId`, `score` —
 * so node can build one from disk and run the real engine without a browser or a
 * GL context. That is what lets the sound derive its control track from the same
 * `step()` the film renders: one implementation, so the score cannot drift out of
 * sync with the picture by being a second guess at what the picture is doing.
 */
export function fromData(base, manifest, score = null) {
  return new Corpus(base, manifest, score);
}

class Corpus {
  constructor(base, manifest, score) {
    this.base = base;
    this.mipmap = true;   // measurement turns this off; see gl.texture
    this.manifest = manifest;
    // Ordered small → large, from the manifest rather than from this module, so
    // a corpus built with a `film` tier is usable without an engine change.
    this.tiers = Object.entries(manifest.tiers)
      .sort((a, b) => a[1].width - b[1].width)
      .map(([name]) => name);
    this.frames = manifest.frames;
    this.byId = new Map(this.frames.map((f) => [f.id, f]));
    this.index = new Map(this.frames.map((f, i) => [f.id, i]));

    // Marked once, here, so no consumer has to re-derive it.
    if (score) {
      const tiles = score.tiles.map((t) => {
        const [x0, y0, x1, y1] = t.px;
        return { ...t, sliver: Math.min(x1 - x0, y1 - y0) < SLIVER_PX };
      });
      this.score = { ...score, tiles, slivers: tiles.filter((t) => t.sliver).length };
    } else {
      this.score = null;
    }

    this.images = new Map(); // `${kind}/${tier}/${id}` → HTMLImageElement
    this.textures = new Map(); // same key → WebGLTexture
    this.pending = new Set();
    this.room = null;
  }

  url(kind, tier, id) {
    return `${this.base}${kind}/${tier}/${id}.webp`;
  }

  /** Fetch the whole eager tier plus the room. Everything else is on demand. */
  async prime(gl, { onProgress } = {}) {
    this.room = texture(gl, await image(`${this.base}${this.manifest.room.file}`), { mipmap: this.mipmap });

    const eager = this.tiers.filter((t) => this.manifest.tiers[t]?.eager);
    const jobs = [];
    for (const tier of eager) {
      for (const f of this.frames) {
        for (const kind of ["plates", "mattes"]) {
          jobs.push([kind, tier, f.id]);
        }
      }
    }
    let done = 0;
    // Bounded concurrency: 162 frames × 2 kinds is 324 requests, and a browser
    // that opens all of them at once starves the first paint of the ones it
    // actually needs first.
    const lanes = 12;
    await Promise.all(
      Array.from({ length: lanes }, async (_, lane) => {
        for (let i = lane; i < jobs.length; i += lanes) {
          const [kind, tier, id] = jobs[i];
          const key = `${kind}/${tier}/${id}`;
          try {
            this.images.set(key, await image(this.url(kind, tier, id)));
          } catch {
            /* one missing plate must not sink the load */
          }
          onProgress?.(++done / jobs.length);
        }
      }),
    );
  }

  /** The best texture that exists RIGHT NOW, upgrading in the background.
   *  Returns null only before the eager tier has landed for this frame. */
  get(gl, kind, id, want = "screen") {
    const at = this.tiers.indexOf(want);
    const order = this.tiers.slice(0, at < 0 ? this.tiers.length : at + 1).reverse();
    for (const tier of order) {
      const key = `${kind}/${tier}/${id}`;
      const cached = this.textures.get(key);
      if (cached) return cached;
      const img = this.images.get(key);
      if (img) {
        const tex = texture(gl, img, { mipmap: this.mipmap });
        this.textures.set(key, tex);
        return tex;
      }
    }
    this.request(kind, want, id);
    return null;
  }

  plate(gl, id, want) {
    return this.get(gl, "plates", id, want);
  }

  matte(gl, id, want) {
    return this.get(gl, "mattes", id, want);
  }

  /** Whether an exact requested-tier image decoded successfully. */
  has(kind, tier, id) {
    return this.images.has(`${kind}/${tier}/${id}`);
  }

  /** Drop every uploaded texture so the next draw re-uploads under current
   *  settings. Only a measurement needs this; the live page never changes them. */
  invalidate() {
    for (const t of this.textures.values()) this.gl?.deleteTexture?.(t);
    this.textures.clear();
  }

  /** Block until these frames exist at this tier.
   *
   * The live page never needs this — it draws whatever has arrived and upgrades
   * in place. A measurement does: comparing against the 2017 composite while the
   * lazy tier is still in flight measures the 512px stand-ins, not the engine.
   */
  async ensure(kind, tier, ids) {
    await Promise.all(
      [...new Set(ids)].map(async (id) => {
        const key = `${kind}/${tier}/${id}`;
        if (this.images.has(key)) return;
        try {
          this.images.set(key, await image(this.url(kind, tier, id)));
        } catch {
          /* leave it missing; the caller counts what did not arrive */
        }
        this.textures.delete(key);
      }),
    );
  }

  /** Ask for a tier we do not have. Idempotent, fire-and-forget. */
  request(kind, tier, id) {
    const key = `${kind}/${tier}/${id}`;
    if (this.images.has(key) || this.pending.has(key)) return;
    this.pending.add(key);
    image(this.url(kind, tier, id))
      .then((img) => this.images.set(key, img))
      .catch(() => {})
      .finally(() => this.pending.delete(key));
  }

  /** Frames whose figure occupies this part of the room, best first.
   *
   * This is the whole point of the index. "Select different parts of the
   * ballerina" is a question about where she was standing in each exposure, and
   * that is answerable from bounding boxes without touching a photograph.
   *
   * `rect` is [x0, y0, x1, y1] in [0,1], y down — the score's convention.
   */
  /** Frames a GENERATED cut may draw on.
   *
   * Projective texturing addresses every fragment through one shared matrix, and
   * that matrix is the camera that stood in the room on 20 June 2017. A frame
   * that did not come from it is not registered to anything, so it may appear in
   * the solved score — the 2017 cut genuinely used one — but the engine must
   * never reach for it on its own.
   */
  usable() {
    return this.frames.filter((f) => f.registered !== false);
  }

  candidates(rect, { minOverlap = 0.02, weightScore = 0.35 } = {}) {
    const out = [];
    for (const f of this.usable()) {
      const box = f.figure?.bbox;
      if (!box) continue;
      const w = Math.min(rect[2], box[2]) - Math.max(rect[0], box[0]);
      const h = Math.min(rect[3], box[3]) - Math.max(rect[1], box[1]);
      if (w <= 0 || h <= 0) continue;

      const cellArea = (rect[2] - rect[0]) * (rect[3] - rect[1]) || 1e-9;
      const share = (w * h) / cellArea; // how much of the CELL she fills
      if (share < minOverlap) continue;

      // Frames the 2017 solve drew on are weighted up but never made exclusive —
      // the piece is supposed to reach past the three frames he cut by hand.
      out.push({ id: f.id, weight: share * (1 + weightScore * Math.min(1, f.score_area * 5)) });
    }
    out.sort((a, b) => b.weight - a.weight);
    return out;
  }

  /** Deterministic weighted draw. Same words in, same frame out, forever. */
  choose(candidates, ...words) {
    if (!candidates.length) return null;
    const total = candidates.reduce((s, c) => s + c.weight, 0);
    let r = (hash(...words) / 4294967296) * total;
    for (const c of candidates) {
      r -= c.weight;
      if (r <= 0) return c.id;
    }
    return candidates[candidates.length - 1].id;
  }
}

function image(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load ${url}`));
    img.src = url;
  });
}

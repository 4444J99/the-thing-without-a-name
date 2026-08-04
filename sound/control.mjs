#!/usr/bin/env node
/** What the picture is doing, sampled — so the score can follow it exactly.
 *
 * The score is not a second guess at the film. It runs the SAME `step()` the
 * renderer runs, over the same program and the same seed, in node with no
 * browser and no GL context, and emits what it finds: how deep each plane is
 * hanging at time t, and which cells changed the photograph they were showing.
 *
 * That is the whole reason `corpus.fromData` exists. A Python reimplementation
 * of the grammar would drift from the JavaScript one the first time either
 * changed, and the drift would be inaudible until the sound stopped landing on
 * the picture. Here there is nothing to drift: one grammar, queried twice.
 *
 * Depth comes from `scatter()` in room.js — the same function that places the
 * plane the renderer draws — so "far" in the score means the plane the viewer
 * sees further away, not an approximation of it.
 *
 *     sound/control.mjs                    # passage control to stdout
 *     sound/control.mjs --window trailer   # any declared window
 *     sound/control.mjs --rate 60 --out c.json
 */

import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { fromData } from "../engine/corpus.js";
import { step } from "../engine/engine.js";
import { captureOf, passageAt, validate } from "../engine/program.js";
import { camera, scatter, viewDepth } from "../engine/room.js";
import { compileRoomBus, validateRoomLayouts } from "../engine/room-events.js";
import { eventsBetween, validate as validateScore } from "../engine/score.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DANSE = path.join(HERE, "..");
const DANSE_REAL = fs.realpathSync(DANSE);

function inside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}

function portableRelative(root, candidate) {
  return path.relative(root, candidate).split(path.sep).join("/");
}

/** How many planes the score actually voices.
 *
 * A cut can put 256 rectangles on screen and voicing all of them would be a wash
 * of noise, not a chord — the same reason the transients get decimated. Eight is
 * a chord you can hear the parts of. They are sampled evenly through the
 * depth-sorted cast, so the selection spans the arrangement front to back
 * instead of clustering wherever the cell ids happen to fall.
 */
const VOICES = 8;

function readJSON(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function args(argv) {
  const out = {
    window: "passage",
    rate: 30,
    seed: null,
    stream: 0,
    out: null,
    from: 0,
    score: null,
    room: "sound/room-layout.json",
  };
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i].replace(/^--/, "");
    if (!(key in out)) throw new Error(`unknown option ${argv[i]}`);
    out[key] = argv[i + 1];
  }
  out.rate = Number(out.rate);
  out.from = Number(out.from);
  out.stream = Number(out.stream);
  return out;
}

const opt = args(process.argv.slice(2));
if (!Number.isFinite(opt.rate) || opt.rate < 0) throw new Error("--rate must be a non-negative number");
if (!Number.isFinite(opt.from) || opt.from < 0) throw new Error("--from must be a non-negative number");
if (!Number.isInteger(opt.stream) || opt.stream < 0 || opt.stream > 0xffffffff) {
  throw new Error("--stream must be a 32-bit unsigned integer");
}

const program = readJSON(path.join(DANSE, "render/program.json"));
validate(program);

let musicalScore = null;
let musicalScorePath = null;
let musicalScoreFileSha256 = null;
let roomLayouts = null;
let roomLayoutsPath = null;
if (opt.score) {
  const lexicalPath = path.resolve(DANSE, opt.score);
  if (!inside(DANSE, lexicalPath)) {
    throw new Error("--score must stay inside the Danse repository");
  }
  const scoreStat = fs.lstatSync(lexicalPath);
  if (scoreStat.isSymbolicLink() || !scoreStat.isFile()) {
    throw new Error("--score must be a regular file, not a symlink");
  }
  musicalScorePath = fs.realpathSync(lexicalPath);
  if (!inside(DANSE_REAL, musicalScorePath)) {
    throw new Error("--score resolves outside the Danse repository");
  }
  const bytes = fs.readFileSync(musicalScorePath);
  musicalScore = validateScore(JSON.parse(bytes.toString("utf8")));
  musicalScoreFileSha256 = crypto.createHash("sha256").update(bytes).digest("hex");

  const lexicalRoomPath = path.resolve(DANSE, opt.room);
  if (!inside(DANSE, lexicalRoomPath)) throw new Error("--room must stay inside the Danse repository");
  const roomStat = fs.lstatSync(lexicalRoomPath);
  if (roomStat.isSymbolicLink() || !roomStat.isFile()) throw new Error("--room must be a regular file, not a symlink");
  roomLayoutsPath = fs.realpathSync(lexicalRoomPath);
  if (!inside(DANSE_REAL, roomLayoutsPath)) throw new Error("--room resolves outside the Danse repository");
  roomLayouts = validateRoomLayouts(readJSON(roomLayoutsPath));
}


const corpusDir = path.join(DANSE, "corpus");
const manifest = readJSON(path.join(corpusDir, "manifest.json"));
const solved = manifest.score ? readJSON(path.join(corpusDir, manifest.score)) : null;
const corpus = fromData(`${corpusDir}/`, manifest, solved);

const seed = opt.seed === null ? (program.seed ?? 0) : Number(opt.seed);

// The same span resolution film.html uses: a `seconds` capture starts exactly
// where it is told, a `passages` capture snaps to a passage boundary so the
// sound begins where the phrase begins.
const cap = captureOf(program, opt.window);
const from = opt.from;
let t0, t1;
if (cap.seconds > 0) {
  t0 = from;
  t1 = from + cap.seconds;
} else {
  let at = passageAt(program, seed, from, opt.stream);
  t0 = at.t0;
  t1 = at.t0;
  for (let k = 0; k < cap.passages; k++) {
    at = passageAt(program, seed, t1 + 1e-6, opt.stream);
    t1 = at.t0 + at.seconds;
  }
}
const dt = opt.rate > 0 ? 1 / opt.rate : null;

// Provenance for an uncropped passage recording: at its boundary the ONE
// movement is exactly one untouched source photograph. Metadata-only callers
// use rate 0, avoiding a full 9,000-step control render just to resolve a span.
const opening = step(corpus, seed, t0, program, { quantise: 0, stream: opt.stream, score: musicalScore }).cast;
const origin = opening.length === 1 ? opening[0].layers?.[0]?.frame ?? null : null;
const caughtPassage = passageAt(program, seed, t0, opt.stream);

const frames = [];
let previous = null; // cell id -> frame id, for spotting a re-cast
let previousCut = null;

for (let i = 0; dt !== null && t0 + i * dt < t1; i++) {
  const t = t0 + i * dt;
  const { state, cast } = step(corpus, seed, t, program, {
    quantise: 0,
    stream: opt.stream,
    score: musicalScore,
  });
  const view = camera(state.divergence, state.azimuth, state.elevation);

  // Every plane, placed exactly where the renderer will place it.
  const placed = cast.map((cell) => {
    const p = scatter(cell.rect, cell.id, seed, state.spread);
    const [x0, y0, x1, y1] = cell.rect;
    return {
      id: cell.id,
      frame: cell.layers?.[0]?.frame ?? null,
      z: viewDepth(view.view, p.position),
      opacity: p.opacity,
      area: (x1 - x0) * (y1 - y0),
      // Where the plane sits across the frame, -1 to +1. The score pans on this,
      // so the stereo image IS the arrangement rather than a decoration of it.
      x: (x0 + x1) - 1,
    };
  });
  placed.sort((a, b) => a.z - b.z); // far to near

  // Evenly through the depth order, so the chord spans the arrangement.
  const voices = [];
  if (placed.length) {
    const stride = placed.length / Math.min(VOICES, placed.length);
    for (let k = 0; k < Math.min(VOICES, placed.length); k++) {
      const p = placed[Math.min(placed.length - 1, Math.floor(k * stride))];
      voices.push([round(p.z, 4), round(p.opacity, 3), round(p.area, 5), round(p.x, 4)]);
    }
  }

  // A re-cast is a cell that swapped which photograph it shows. Cell ids only
  // mean the same thing within one cut, so a cut change is not 256 events.
  const now = new Map(placed.map((p) => [p.id, p.frame]));
  const events = [];
  if (previous && state.cut === previousCut) {
    for (const [id, frame] of now) {
      if (previous.has(id) && previous.get(id) !== frame) {
        const p = placed.find((q) => q.id === id);
        events.push([round(p.z, 4), round(p.area, 5), round(p.x, 4)]);
      }
    }
  }
  previous = now;
  previousCut = state.cut;

  frames.push({
    t: round(t, 4),
    mv: state.movement,
    cut: state.cut,
    ep: state.epoch,
    sp: round(state.spread, 4),
    dv: round(state.divergence, 4),
    n: placed.length,
    v: voices,
    e: events,
    ...(state.music
      ? {
          ms: {
            source: round(state.music.source_second, 4),
            beat: state.music.beat.index,
            downbeat: state.music.beat.downbeat,
            phrase: state.music.phrase.id,
            movement: state.music.movement.id,
            dynamic: state.music.dynamic.level,
            cues: state.music.cues.map((cue) => cue.id),
            recast: state.music.visual.recast,
          },
        }
      : {}),
  });
}

function round(x, places) {
  const f = 10 ** places;
  return Math.round(x * f) / f;
}

const musicEvents = [];
const roomBuses = [];
if (musicalScore) {
  let cursor = t0;
  while (cursor < t1) {
    const passage = passageAt(program, seed, cursor, opt.stream);
    const end = Math.min(t1, passage.t0 + passage.seconds);
    musicEvents.push(
      ...eventsBetween(musicalScore, cursor, end, { t0: passage.t0, seconds: passage.seconds }),
    );
    roomBuses.push(compileRoomBus(musicalScore, {
      river_seed: seed >>> 0,
      stream: opt.stream,
      index: passage.index,
      seed: passage.seed,
      t0: passage.t0,
      seconds: passage.seconds,
    }));
    if (!(end > cursor)) throw new Error("score passage traversal did not advance");
    cursor = end;
  }
}

const payload = {
  schema: "danse.sound.control.v1",
  title: program.title,
  capture: cap.name,
  seed,
  stream: opt.stream,
  passage: caughtPassage.index,
  passageSeed: caughtPassage.seed,
  rate: opt.rate,
  t0,
  t1,
  duration: round(t1 - t0, 4),
  origin,
  voices: VOICES,
  layout: { v: ["z", "opacity", "area", "x"], e: ["z", "area", "x"] },
  ...(musicalScore
    ? {
        music: {
          score_path: portableRelative(DANSE_REAL, musicalScorePath),
          score_file_sha256: musicalScoreFileSha256,
          identity: musicalScore.identity,
          provenance: musicalScore.provenance,
          stems: musicalScore.orchestration,
          events: musicEvents,
        },
        room: {
          schema: "danse.room.control.v1",
          semantics: "authored-start-events",
          layout_registry_path: portableRelative(DANSE_REAL, roomLayoutsPath),
          layout_identity: roomLayouts.identity,
          buses: roomBuses,
        },
      }
    : {}),
  frames,
};

const text = JSON.stringify(payload);
if (opt.out) {
  fs.writeFileSync(opt.out, text + "\n");
  const events = frames.reduce((s, f) => s + f.e.length, 0);
  process.stderr.write(
    `${frames.length} control frames · ${round(t1 - t0, 1)}s @ ${opt.rate}Hz · ${events} re-casts · ${opt.out}\n`,
  );
} else {
  process.stdout.write(text);
}

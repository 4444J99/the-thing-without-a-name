/** Reproducible visitor input around the pure Danse engine.
 *
 * Camera frames and raw landmarks end at the source adapter. This module accepts
 * only small, anonymous features in river time, validates a bounded receipt,
 * replays it without a clock, and applies bounded changes to the room state that
 * the engine already produced. With no input it returns the original objects.
 */

export const RECEIPT_SCHEMA = "danse.interaction.v1";
export const MAX_VISITORS = 4;
export const MAX_SECONDS = 10 * 60;
export const MAX_SAMPLES = 7200;
export const SOURCES = Object.freeze(["camera", "keyboard-touch", "fixture"]);
export const STATUSES = Object.freeze([
  "requesting",
  "loading",
  "active",
  "no-person",
  "denied",
  "unavailable",
  "dropout",
  "reconnecting",
  "stopped",
  "error",
]);
export const PRIVACY = Object.freeze({
  cameraFrames: "memory-only",
  rawLandmarks: "discarded-after-feature-extraction",
  derivedFeatures: "receipt-only-on-explicit-save",
  retainedByDefault: false,
  transmitted: false,
});

const STATUS_SET = new Set(STATUSES);
const SOURCE_SET = new Set(SOURCES);
const clamp = (value, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, value));
const round = (value, places = 6) => Number(value.toFixed(places));
const uint32 = (value) => Number.isInteger(value) && value >= 0 && value <= 0xffffffff;

function objectWithKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new TypeError(`${label} keys must be exactly ${expected.join(", ")}`);
  }
  return value;
}

function finite(value, label, lo = -Infinity, hi = Infinity) {
  if (!Number.isFinite(value) || value < lo || value > hi) {
    throw new RangeError(`${label} must be finite in [${lo}, ${hi}]`);
  }
  return value;
}

function pair(value, label) {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new TypeError(`${label} must be [x, y]`);
  }
  return [
    round(finite(value[0], `${label}.x`, 0, 1)),
    round(finite(value[1], `${label}.y`, 0, 1)),
  ];
}

export function normalizeVisitor(value, index = 0) {
  objectWithKeys(value, ["slot", "confidence", "center", "openness", "reach"], `visitor ${index}`);
  if (!Number.isInteger(value.slot) || value.slot < 0 || value.slot >= MAX_VISITORS) {
    throw new RangeError(`visitor ${index}.slot must be an integer in [0, ${MAX_VISITORS - 1}]`);
  }
  return {
    slot: value.slot,
    confidence: round(finite(value.confidence, `visitor ${index}.confidence`, 0, 1)),
    center: pair(value.center, `visitor ${index}.center`),
    openness: round(finite(value.openness, `visitor ${index}.openness`, 0, 1)),
    reach: round(finite(value.reach, `visitor ${index}.reach`, 0, 1)),
  };
}

export function normalizeSample(value, index = 0) {
  objectWithKeys(value, ["at", "status", "source", "visitors"], `sample ${index}`);
  const at = round(finite(value.at, `sample ${index}.at`, 0, 100 * 365 * 86400), 3);
  if (!STATUS_SET.has(value.status)) throw new TypeError(`sample ${index} has unknown status ${value.status}`);
  if (!SOURCE_SET.has(value.source)) throw new TypeError(`sample ${index} has unknown source ${value.source}`);
  if (!Array.isArray(value.visitors) || value.visitors.length > MAX_VISITORS) {
    throw new RangeError(`sample ${index}.visitors must contain at most ${MAX_VISITORS} visitors`);
  }
  const visitors = value.visitors
    .map((visitor, visitorIndex) => normalizeVisitor(visitor, visitorIndex))
    .sort((a, b) => a.slot - b.slot);
  if (new Set(visitors.map((visitor) => visitor.slot)).size !== visitors.length) {
    throw new TypeError(`sample ${index} contains duplicate visitor slots`);
  }
  if (value.status === "active" && visitors.length === 0) {
    throw new TypeError(`sample ${index} cannot be active without a visitor`);
  }
  if (value.status !== "active" && visitors.length !== 0) {
    throw new TypeError(`sample ${index} may retain visitors only while active`);
  }
  return { at, status: value.status, source: value.source, visitors };
}

export function createReceipt({ seed, stream = 0 }) {
  if (!uint32(seed) || !uint32(stream)) throw new RangeError("receipt river seed and stream must be uint32");
  return {
    schema: RECEIPT_SCHEMA,
    river: { seed: seed >>> 0, stream: stream >>> 0 },
    privacy: { ...PRIVACY },
    window: { startedAt: null, endedAt: null },
    samples: [],
  };
}

export function validateReceipt(value, expectedRiver = null) {
  objectWithKeys(value, ["schema", "river", "privacy", "window", "samples"], "receipt");
  if (value.schema !== RECEIPT_SCHEMA) throw new TypeError(`unknown receipt schema ${value.schema}`);
  objectWithKeys(value.river, ["seed", "stream"], "receipt.river");
  if (!uint32(value.river.seed) || !uint32(value.river.stream)) {
    throw new RangeError("receipt river seed and stream must be uint32");
  }
  if (expectedRiver && (value.river.seed !== (expectedRiver.seed >>> 0) || value.river.stream !== (expectedRiver.stream >>> 0))) {
    throw new TypeError("interaction receipt belongs to a different river");
  }
  objectWithKeys(value.privacy, Object.keys(PRIVACY), "receipt.privacy");
  for (const [key, expected] of Object.entries(PRIVACY)) {
    if (value.privacy[key] !== expected) throw new TypeError(`receipt.privacy.${key} is not the canonical privacy contract`);
  }
  objectWithKeys(value.window, ["startedAt", "endedAt"], "receipt.window");
  if (!Array.isArray(value.samples) || value.samples.length > MAX_SAMPLES) {
    throw new RangeError(`receipt samples must contain at most ${MAX_SAMPLES} entries`);
  }

  const samples = value.samples.map(normalizeSample);
  for (let index = 1; index < samples.length; index++) {
    if (samples[index].at <= samples[index - 1].at) {
      throw new RangeError("receipt sample times must be strictly increasing");
    }
  }
  const startedAt = value.window.startedAt;
  const endedAt = value.window.endedAt;
  if (samples.length === 0) {
    if (startedAt !== null || endedAt !== null) throw new TypeError("empty receipt window must be null");
  } else {
    finite(startedAt, "receipt.window.startedAt", 0);
    finite(endedAt, "receipt.window.endedAt", startedAt);
    if (round(startedAt, 3) !== samples[0].at || round(endedAt, 3) !== samples.at(-1).at) {
      throw new TypeError("receipt window must match its first and last sample");
    }
    if (endedAt - startedAt > MAX_SECONDS) throw new RangeError(`receipt exceeds ${MAX_SECONDS} seconds`);
  }
  return {
    schema: RECEIPT_SCHEMA,
    river: { seed: value.river.seed >>> 0, stream: value.river.stream >>> 0 },
    privacy: { ...PRIVACY },
    window: { startedAt: samples[0]?.at ?? null, endedAt: samples.at(-1)?.at ?? null },
    samples,
  };
}

function visibleLandmark(landmark) {
  if (!landmark || !Number.isFinite(landmark.x) || !Number.isFinite(landmark.y)) return null;
  const visibility = Number.isFinite(landmark.visibility) ? landmark.visibility : 1;
  const presence = Number.isFinite(landmark.presence) ? landmark.presence : visibility;
  const confidence = clamp(Math.min(visibility, presence));
  if (confidence < 0.15) return null;
  return { x: clamp(landmark.x), y: clamp(landmark.y), confidence };
}

const midpoint = (a, b) => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
  confidence: (a.confidence + b.confidence) / 2,
});
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

/** Reduce MediaPipe's 33 landmarks to anonymous room controls, then discard them. */
export function featuresFromLandmarks(landmarks, { slot = 0, mirror = true } = {}) {
  if (!Array.isArray(landmarks)) return null;
  const marks = landmarks.map(visibleLandmark);
  const visible = marks.filter(Boolean);
  if (visible.length < 4) return null;

  const shoulders = marks[11] && marks[12] ? midpoint(marks[11], marks[12]) : null;
  const hips = marks[23] && marks[24] ? midpoint(marks[23], marks[24]) : null;
  const torso = shoulders && hips ? midpoint(shoulders, hips) : null;
  const centerMarks = [marks[11], marks[12], marks[23], marks[24]].filter(Boolean);
  const center = torso ?? {
    x: centerMarks.length ? centerMarks.reduce((sum, mark) => sum + mark.x, 0) / centerMarks.length : visible.reduce((sum, mark) => sum + mark.x, 0) / visible.length,
    y: centerMarks.length ? centerMarks.reduce((sum, mark) => sum + mark.y, 0) / centerMarks.length : visible.reduce((sum, mark) => sum + mark.y, 0) / visible.length,
  };

  const bodyMarks = [marks[11], marks[12], marks[15], marks[16], marks[23], marks[24], marks[27], marks[28]].filter(Boolean);
  const xs = bodyMarks.map((mark) => mark.x);
  const ys = bodyMarks.map((mark) => mark.y);
  const spanX = xs.length ? Math.max(...xs) - Math.min(...xs) : 0;
  const spanY = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
  const openness = clamp((spanX + spanY - 0.35) / 1.15);

  const torsoScale = shoulders && hips ? Math.max(0.08, distance(shoulders, hips)) : 0.25;
  let reach = 0;
  for (const [wristIndex, shoulderIndex] of [[15, 11], [16, 12]]) {
    const wrist = marks[wristIndex];
    const shoulder = marks[shoulderIndex];
    if (!wrist || !shoulder) continue;
    const extension = clamp((distance(wrist, shoulder) / torsoScale - 0.45) / 1.8);
    const raised = clamp((shoulder.y - wrist.y) / (torsoScale * 1.5));
    reach = Math.max(reach, extension, raised);
  }

  const confidence = visible.reduce((sum, mark) => sum + mark.confidence, 0) / visible.length;
  return normalizeVisitor({
    slot,
    confidence,
    center: [mirror ? 1 - center.x : center.x, center.y],
    openness,
    reach,
  });
}

export function visitorsFromPoseResult(result, options = {}) {
  const poses = Array.isArray(result?.landmarks) ? result.landmarks.slice(0, MAX_VISITORS) : [];
  return poses
    .map((landmarks) => featuresFromLandmarks(landmarks, { ...options, slot: 0 }))
    .filter(Boolean)
    .sort((a, b) => a.center[0] - b.center[0])
    .map((visitor, slot) => ({ ...visitor, slot }));
}

export function aggregateVisitors(visitors) {
  if (!visitors.length) {
    return { count: 0, confidence: 0, center: [0.5, 0.5], openness: 0, reach: 0 };
  }
  const ordered = [...visitors].sort((a, b) => a.slot - b.slot);
  const total = ordered.reduce((sum, visitor) => sum + visitor.confidence, 0) || 1;
  const weighted = (read) => ordered.reduce((sum, visitor) => sum + read(visitor) * visitor.confidence, 0) / total;
  return {
    count: ordered.length,
    confidence: round(1 - ordered.reduce((product, visitor) => product * (1 - visitor.confidence), 1)),
    center: [round(weighted((visitor) => visitor.center[0])), round(weighted((visitor) => visitor.center[1]))],
    openness: round(weighted((visitor) => visitor.openness)),
    reach: round(weighted((visitor) => visitor.reach)),
  };
}

export const NEUTRAL_INPUT = Object.freeze({
  status: "stopped",
  source: null,
  count: 0,
  confidence: 0,
  center: Object.freeze([0.5, 0.5]),
  openness: 0,
  reach: 0,
  dwell: 0,
  freshness: 0,
  strength: 0,
});

/** Replay the most recent derived input at absolute river time t. */
export function inputAt(receipt, t) {
  if (!receipt?.samples?.length || !Number.isFinite(t)) return NEUTRAL_INPUT;
  const samples = receipt.samples;
  let lo = 0;
  let hi = samples.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].at <= t) lo = mid + 1;
    else hi = mid;
  }
  const index = lo - 1;
  if (index < 0) return NEUTRAL_INPUT;
  const latest = samples[index];

  let activeIndex = latest.status === "active" ? index : -1;
  if (activeIndex < 0 && (latest.status === "no-person" || latest.status === "dropout")) {
    for (let cursor = index - 1; cursor >= 0; cursor--) {
      if (samples[cursor].status === "active") {
        activeIndex = cursor;
        break;
      }
      if (!["no-person", "dropout", "reconnecting"].includes(samples[cursor].status)) break;
    }
  }
  if (activeIndex < 0) return { ...NEUTRAL_INPUT, status: latest.status, source: latest.source };

  const active = samples[activeIndex];
  let freshness;
  if (latest.status === "active") {
    const age = Math.max(0, t - active.at);
    freshness = age <= 0.25 ? 1 : 1 - (age - 0.25) / 0.75;
  } else {
    const fadeSeconds = latest.status === "dropout" ? 1.25 : 0.7;
    // Camera polling may emit the same absence status repeatedly. Carry the
    // last active pose from the first absence transition, never from the most
    // recent duplicate, so an empty room always reaches neutral on schedule.
    const absenceAt = samples[activeIndex + 1]?.at ?? latest.at;
    freshness = 1 - Math.max(0, t - absenceAt) / fadeSeconds;
  }
  freshness = clamp(freshness);
  if (freshness <= 0) return { ...NEUTRAL_INPUT, status: latest.status, source: latest.source };

  let start = active.at;
  for (let cursor = activeIndex - 1; cursor >= 0; cursor--) {
    const prior = samples[cursor];
    if (prior.status !== "active" || prior.source !== active.source) break;
    start = prior.at;
  }
  const dwell = Math.max(0, t - start);
  const crowd = aggregateVisitors(active.visitors);
  const strength = clamp(crowd.confidence * freshness * (0.45 + 0.55 * clamp(dwell / 3)));
  return {
    status: latest.status,
    source: active.source,
    ...crowd,
    dwell: round(dwell, 3),
    freshness: round(freshness),
    strength: round(strength),
  };
}

/** Apply embodied influence to the room grammar, never to the engine clock. */
export function modulateFrame(state, draw, input, { enabled = true, reducedMotion = false } = {}) {
  if (!enabled || reducedMotion || !input || input.strength <= 0 || state.cut === "black") {
    return { changed: false, state, draw, interaction: input ?? NEUTRAL_INPUT };
  }
  const strength = clamp(input.strength);
  const horizontal = (input.center[0] - 0.5) * 2;
  const vertical = (0.5 - input.center[1]) * 2;
  const crowd = clamp((input.count - 1) / (MAX_VISITORS - 1));
  const dwell = clamp(input.dwell / 4);
  const nextState = {
    ...state,
    divergence: clamp(state.divergence + strength * (0.035 + 0.08 * input.openness + 0.04 * crowd), 0, 1.15),
    azimuth: clamp(state.azimuth + horizontal * strength * 0.32, -1.35, 1.35),
    elevation: clamp(state.elevation + vertical * strength * 0.16, -0.7, 0.7),
    spread: clamp(state.spread + strength * (0.08 + 0.18 * input.openness), 0, 1),
    projK: clamp(state.projK + strength * input.reach * 0.18, 0, 1),
  };
  const matteK = Math.max(draw.matteK ?? 0, strength * input.reach * (0.35 + 0.3 * dwell));
  return {
    changed: true,
    state: nextState,
    draw: { ...draw, matteK: clamp(matteK) },
    interaction: {
      ...input,
      effect: {
        camera: round(nextState.divergence - state.divergence),
        azimuth: round(nextState.azimuth - state.azimuth),
        elevation: round(nextState.elevation - state.elevation),
        spread: round(nextState.spread - state.spread),
        carriedPicture: round(nextState.projK - state.projK),
        figureMatte: round(clamp(matteK)),
      },
    },
  };
}

/** Renderer facade: frameAt remains the canonical engine path. */
export class InteractionRenderer {
  constructor(renderer, replay, options = {}) {
    this.renderer = renderer;
    this.replay = replay;
    this.options = options;
    this.last = { ...NEUTRAL_INPUT };
  }

  get gl() { return this.renderer.gl; }
  get canvas() { return this.renderer.canvas; }
  get corpus() { return this.renderer.corpus; }

  draw(cast, state, draw = {}) {
    const input = this.replay(state.t);
    const result = modulateFrame(state, draw, input, {
      enabled: this.options.enabled?.() ?? true,
      reducedMotion: this.options.reducedMotion?.() ?? false,
    });
    this.last = result.interaction;
    const stats = result.changed
      ? this.renderer.draw(cast, result.state, result.draw)
      : this.renderer.draw(cast, state, draw);
    return { ...stats, interaction: this.last };
  }
}

/** Immutable musical-score queries on absolute time.
 *
 * A score is compiled once. Query cost depends only on the bounded contents of
 * one pre-indexed one-second bucket, never on how long the river has run. A
 * passage maps the nominal score onto its own absolute [t0, t1) span, so live,
 * offline, segmented, and restarted consumers ask the same pure question.
 */

const SCHEMA = "danse.music.score.v1";

const clamp01 = (value) => (value < 0 ? 0 : value > 1 ? 1 : value);

const SHA256_K = Uint32Array.from([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotateRight = (value, bits) => (value >>> bits) | (value << (32 - bits));

function sha256Hex(text) {
  const message = new TextEncoder().encode(text);
  const byteLength = Math.ceil((message.length + 9) / 64) * 64;
  const padded = new Uint8Array(byteLength);
  padded.set(message);
  padded[message.length] = 0x80;
  const view = new DataView(padded.buffer);
  const bitLength = message.length * 8;
  view.setUint32(byteLength - 8, Math.floor(bitLength / 0x100000000), false);
  view.setUint32(byteLength - 4, bitLength >>> 0, false);

  const hash = Uint32Array.from([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const words = new Uint32Array(64);
  for (let offset = 0; offset < byteLength; offset += 64) {
    for (let index = 0; index < 16; index++) words[index] = view.getUint32(offset + index * 4, false);
    for (let index = 16; index < 64; index++) {
      const a = words[index - 15];
      const b = words[index - 2];
      const s0 = rotateRight(a, 7) ^ rotateRight(a, 18) ^ (a >>> 3);
      const s1 = rotateRight(b, 17) ^ rotateRight(b, 19) ^ (b >>> 10);
      words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index++) {
      const s1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choose = (e & f) ^ (~e & g);
      const first = (h + s1 + choose + SHA256_K[index] + words[index]) >>> 0;
      const s0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const second = (s0 + majority) >>> 0;
      [h, g, f, e, d, c, b, a] = [g, f, e, (d + first) >>> 0, c, b, a, (first + second) >>> 0];
    }
    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }
  return Array.from(hash, (word) => word.toString(16).padStart(8, "0")).join("");
}

function unicodeCompare(left, right) {
  const a = Array.from(left, (character) => character.codePointAt(0));
  const b = Array.from(right, (character) => character.codePointAt(0));
  for (let index = 0; index < Math.min(a.length, b.length); index++) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return a.length - b.length;
}

function canonicalTree(value) {
  if (value === null) return ["null"];
  if (typeof value === "boolean") return ["boolean", value];
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError(`non-finite canonical number ${value}`);
    const bits = new DataView(new ArrayBuffer(8));
    bits.setFloat64(0, value, false);
    const hex = Array.from(new Uint8Array(bits.buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return ["number", hex];
  }
  if (typeof value === "string") return ["string", value];
  if (Array.isArray(value)) return ["array", value.map(canonicalTree)];
  if (value && typeof value === "object") {
    return [
      "object",
      Object.keys(value).sort(unicodeCompare).map((key) => [key, canonicalTree(value[key])]),
    ];
  }
  throw new TypeError(`unsupported canonical value ${typeof value}`);
}

/** SHA-256 over the score with its self-identifying digest omitted. */
export function contractSha256(score) {
  const identity = score?.identity;
  if (!identity || typeof identity !== "object" || Array.isArray(identity)) {
    throw new TypeError("music score identity must be an object");
  }
  const { contract_sha256: _declared, ...identitySource } = identity;
  const source = { ...score, identity: identitySource };
  return sha256Hex(JSON.stringify(canonicalTree(source)));
}

export async function load(url = "music/score.json") {
  const score = await fetch(url).then((response) => {
    if (!response.ok) throw new Error(`music score ${response.status} at ${url}`);
    return response.json();
  });
  return validate(score);
}

/** Best-effort loader for the live artwork; deterministic capture uses load(). */
export async function loadOptional(url, onError = () => {}) {
  try {
    return await load(url);
  } catch (error) {
    onError(error);
    return null;
  }
}

export function validate(score) {
  const bad = (message) => {
    throw new Error(`music score: ${message}`);
  };
  if (!score || typeof score !== "object" || Array.isArray(score)) bad("root must be an object");
  if (score.schema !== SCHEMA) bad(`unknown schema ${score.schema}`);
  const duration = score.time?.duration_seconds;
  if (!Number.isFinite(duration) || !(duration > 0)) bad("time.duration_seconds must be finite and positive");
  for (const name of ["tempo", "meter", "beats", "phrases", "dynamics", "movements"]) {
    if (!Array.isArray(score[name]) || !score[name].length) bad(`${name} must be non-empty`);
  }
  if (!Array.isArray(score.cues) || !Array.isArray(score.notes) || !Array.isArray(score.orchestration)) {
    bad("cues, notes, and orchestration must be arrays");
  }
  const lookup = score.lookup;
  const buckets = lookup?.buckets;
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)
      || lookup.quantum_seconds !== 1 || !Array.isArray(buckets)
      || buckets.length !== Math.ceil(duration)) {
    bad("lookup must contain one immutable bucket per source second");
  }
  const stateRows = {
    tempo: score.tempo,
    meter: score.meter,
    beat: score.beats,
    phrase: score.phrases,
    dynamic: score.dynamics,
    movement: score.movements,
  };
  const validIndex = (value, rows) => Number.isInteger(value) && value >= 0 && value < rows.length;
  for (const [bucketIndex, bucket] of buckets.entries()) {
    if (!bucket || typeof bucket !== "object" || Array.isArray(bucket)) bad(`lookup bucket ${bucketIndex} must be an object`);
    for (const [name, rows] of Object.entries(stateRows)) {
      if (!validIndex(bucket[name], rows)) bad(`lookup bucket ${bucketIndex}.${name} is out of range`);
    }
    if (!Array.isArray(bucket.active_cues)
        || bucket.active_cues.some((index) => !validIndex(index, score.cues))) {
      bad(`lookup bucket ${bucketIndex}.active_cues is malformed`);
    }
    const noteStart = bucket.note_start;
    if (!Array.isArray(noteStart) || noteStart.length !== 2
        || !noteStart.every(Number.isInteger)
        || noteStart[0] < 0 || noteStart[0] > noteStart[1] || noteStart[1] > score.notes.length) {
      bad(`lookup bucket ${bucketIndex}.note_start is malformed`);
    }
    if (!Number.isInteger(bucket.recast) || bucket.recast < 0) bad(`lookup bucket ${bucketIndex}.recast is malformed`);
  }
  if (score.movements[0].start_second !== 0 || score.movements.at(-1).end_second !== duration) {
    bad("movement bindings must tile the nominal score");
  }
  let cursor = 0;
  for (const movement of score.movements) {
    if (Math.abs(movement.start_second - cursor) > 1e-6 || !(movement.end_second > movement.start_second)) {
      bad(`movement ${movement.id} breaks the score partition`);
    }
    cursor = movement.end_second;
  }
  const declared = score.identity?.contract_sha256;
  let actual;
  try {
    actual = contractSha256(score);
  } catch (error) {
    bad(`cannot verify identity.contract_sha256: ${error.message}`);
  }
  if (!/^[0-9a-f]{64}$/.test(declared ?? "") || actual !== declared) {
    bad("identity.contract_sha256 does not match the score content");
  }
  return score;
}

function mappedTime(score, absoluteSecond, window) {
  if (!Number.isFinite(absoluteSecond)) throw new RangeError(`score time is not finite: ${absoluteSecond}`);
  const duration = score.time.duration_seconds;
  const lastSourceSecond = duration * (1 - Number.EPSILON);
  if (window) {
    if (!Number.isFinite(window.t0) || !(window.seconds > 0)) throw new RangeError("score window must have t0 and positive seconds");
    const phase = clamp01((absoluteSecond - window.t0) / window.seconds);
    return {
      sourceSecond: Math.min(lastSourceSecond, phase * duration),
      cycle: 0,
      scale: window.seconds / duration,
    };
  }
  const cycle = Math.floor(Math.max(0, absoluteSecond) / duration);
  const sourceSecond = Math.max(0, absoluteSecond) - cycle * duration;
  return { sourceSecond: Math.min(lastSourceSecond, sourceSecond), cycle, scale: 1 };
}

function advance(rows, index, sourceSecond, key = "second") {
  let at = index;
  while (at + 1 < rows.length && rows[at + 1][key] <= sourceSecond) at++;
  return at;
}

/** Musical state at an absolute river time. */
export function scoreAt(score, absoluteSecond, window = null) {
  const mapped = mappedTime(score, absoluteSecond, window);
  const bucket = score.lookup.buckets[Math.floor(mapped.sourceSecond)];
  const tempo = score.tempo[advance(score.tempo, bucket.tempo, mapped.sourceSecond)];
  const meter = score.meter[advance(score.meter, bucket.meter, mapped.sourceSecond)];
  const beat = score.beats[advance(score.beats, bucket.beat, mapped.sourceSecond)];
  const phrase = score.phrases[advance(score.phrases, bucket.phrase, mapped.sourceSecond, "start_second")];
  const dynamic = score.dynamics[advance(score.dynamics, bucket.dynamic, mapped.sourceSecond)];
  const movement = score.movements[advance(score.movements, bucket.movement, mapped.sourceSecond, "start_second")];
  const bucketCues = bucket.active_cues.map((index) => score.cues[index]);
  const cues = bucketCues.filter(
    (cue) => cue.second <= mapped.sourceSecond && mapped.sourceSecond < cue.end_second,
  );
  const offsets = {};
  let hold = false;
  let recast = bucket.recast;
  // A recast beginning between bucket boundaries remains cumulative after its
  // short cue window ends. active_cues is a bucket-wide candidate index, so it
  // can advance the snapshot without scanning the complete cue array.
  for (const cue of bucketCues) {
    if (cue.visual.recast && cue.second <= mapped.sourceSecond) {
      recast = Math.max(recast, cue.visual.recast_index);
    }
  }
  for (const cue of cues) {
    hold ||= cue.visual.hold;
    for (const [channel, value] of Object.entries(cue.visual.channel_offsets)) {
      offsets[channel] = (offsets[channel] ?? 0) + value * cue.strength;
    }
  }
  const beatSpan = score.beats[beat.index + 1]?.second - beat.second || 60 / tempo.bpm;
  return {
    identity: score.identity.contract_sha256,
    absolute_second: absoluteSecond,
    source_second: mapped.sourceSecond,
    cycle: mapped.cycle,
    scale: mapped.scale,
    tempo: { ...tempo, effective_bpm: tempo.bpm / mapped.scale },
    meter,
    beat: { ...beat, phase: clamp01((mapped.sourceSecond - beat.second) / beatSpan) },
    phrase,
    dynamic,
    movement: {
      ...movement,
      u: clamp01((mapped.sourceSecond - movement.start_second) / (movement.end_second - movement.start_second)),
    },
    cues,
    visual: { hold, recast, channel_offsets: offsets },
  };
}

function mapEvent(event, cycleStart, scale) {
  const sourceStart = event.start_second ?? event.second;
  const sourceEnd = event.end_second ?? sourceStart;
  return {
    ...event,
    at: cycleStart + sourceStart * scale,
    end: cycleStart + sourceEnd * scale,
  };
}

/**
 * Candidate authored starts for a clipped source interval.
 *
 * One adjacent bucket on each side makes the lookup conservative under affine
 * floating-point roundoff. The final absolute [start, end) test remains the
 * authority. A cue can be active in several buckets, hence the per-window sets.
 */
function indexedEvents(score, sourceStart, sourceEnd) {
  const buckets = score.lookup.buckets;
  const first = Math.max(0, Math.floor(sourceStart) - 1);
  const lastExclusive = Math.min(buckets.length, Math.ceil(sourceEnd) + 1);
  const cueIndices = new Set();
  const noteIndices = new Set();
  for (let bucketIndex = first; bucketIndex < lastExclusive; bucketIndex++) {
    const bucket = buckets[bucketIndex];
    for (const index of bucket.active_cues) cueIndices.add(index);
    for (let index = bucket.note_start[0]; index < bucket.note_start[1]; index++) noteIndices.add(index);
  }
  return { cueIndices, noteIndices };
}

/** Authored note/cue starts in [start, end), mapped to absolute playback time. */
export function eventsBetween(score, start, end, window = null) {
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) throw new RangeError("invalid score event interval");
  if (end === start) return [];
  const duration = score.time.duration_seconds;
  const windows = [];
  if (window) {
    if (!Number.isFinite(window.t0) || !Number.isFinite(window.seconds) || !(window.seconds > 0)) {
      throw new RangeError("score window must have finite t0 and positive seconds");
    }
    windows.push({ t0: window.t0, seconds: window.seconds, scale: window.seconds / duration });
  } else {
    const first = Math.floor(Math.max(0, start) / duration);
    const last = Math.ceil(Math.max(0, end) / duration) - 1;
    if (last - first + 1 > 10_000) throw new RangeError("score event interval exceeds 10,000 cycles");
    for (let cycle = first; cycle <= last; cycle++) windows.push({ t0: cycle * duration, seconds: duration, scale: 1 });
  }
  const events = [];
  for (const mapped of windows) {
    const windowEnd = mapped.t0 + mapped.seconds;
    if (windowEnd <= start || mapped.t0 >= end) continue;
    const clippedStart = Math.max(start, mapped.t0);
    const clippedEnd = Math.min(end, windowEnd);
    if (!(clippedEnd > clippedStart)) continue;
    const sourceStart = (clippedStart - mapped.t0) / mapped.scale;
    const sourceEnd = (clippedEnd - mapped.t0) / mapped.scale;
    const { cueIndices, noteIndices } = indexedEvents(score, sourceStart, sourceEnd);
    for (const index of cueIndices) {
      const event = mapEvent(score.cues[index], mapped.t0, mapped.scale);
      if (event.at >= start && event.at < end) events.push({ type: "cue", ...event });
    }
    for (const index of noteIndices) {
      const event = mapEvent(score.notes[index], mapped.t0, mapped.scale);
      if (event.at >= start && event.at < end) events.push({ type: "note", ...event });
    }
  }
  return events.sort((a, b) => a.at - b.at || a.type.localeCompare(b.type) || a.index - b.index);
}

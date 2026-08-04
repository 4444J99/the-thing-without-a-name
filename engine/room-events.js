/** Pure, immutable room-event contracts and speaker routing.
 *
 * A bus is compiled once for one passage from the shared musical score. Its
 * one-second lookup buckets make a seek depend on the queried interval and its
 * authored START events, never on elapsed river history. No clock, mutable
 * voice allocation, previous-frame map, or hidden random state enters here.
 */

import { rand } from "./rng.js";
import { canonicalSha256, validate as validateScore } from "./score.js";

export const ROOM_BUS_SCHEMA = "danse.room.events.v1";
export const ROOM_LAYOUT_SCHEMA = "danse.room.layouts.v1";
export const ROOM_PLAN_SCHEMA = "danse.room.render-plan.v1";

const SHA256 = /^[0-9a-f]{64}$/;
const UINT32_MAX = 0xffffffff;
const EVENT_TYPES = new Set([
  "passage.start",
  "movement.start",
  "plane.assembly",
  "score.cue",
  "plane.recast",
  "score.note",
  "calibration.impulse",
]);
const EVENT_ORDER = new Map([
  ["passage.start", 0],
  ["movement.start", 1],
  ["plane.assembly", 2],
  ["score.cue", 3],
  ["plane.recast", 4],
  ["score.note", 5],
  ["calibration.impulse", 6],
]);
const EVENT_TAG = new Map([
  ["passage.start", 0x710],
  ["movement.start", 0x720],
  ["plane.assembly", 0x730],
  ["score.cue", 0x740],
  ["plane.recast", 0x750],
  ["score.note", 0x760],
  ["calibration.impulse", 0x770],
]);

const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));
const rounded = (value, places = 9) => {
  const scale = 10 ** places;
  const result = Math.round(value * scale) / scale;
  return result === 0 ? 0 : result;
};
const isObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const uint32 = (value) => Number.isInteger(value) && value >= 0 && value <= UINT32_MAX;

function withoutDeclaredDigest(contract) {
  if (!isObject(contract?.identity)) throw new TypeError("room contract identity must be an object");
  const { contract_sha256: _declared, ...identity } = contract.identity;
  return { ...contract, identity };
}

export function roomContractSha256(contract) {
  return canonicalSha256(withoutDeclaredDigest(contract));
}

export function layoutContractSha256(registry) {
  return canonicalSha256(withoutDeclaredDigest(registry));
}

function validatePassage(passage, label = "passage") {
  if (!isObject(passage)) throw new TypeError(`${label} must be an object`);
  if (!uint32(passage.river_seed) || !uint32(passage.stream) || !uint32(passage.seed)) {
    throw new RangeError(`${label} seed and stream fields must be uint32 values`);
  }
  if (!Number.isInteger(passage.index) || passage.index < 0) throw new RangeError(`${label}.index must be non-negative`);
  if (!finite(passage.t0) || passage.t0 < 0 || !finite(passage.seconds) || !(passage.seconds > 0)) {
    throw new RangeError(`${label} must have finite non-negative t0 and positive seconds`);
  }
  return passage;
}

function positionFor(passage, type, sourceIndex) {
  const tag = EVENT_TAG.get(type);
  const axis = (word) => rounded(rand(passage.seed, passage.index, sourceIndex, tag, word) * 2 - 1, 6);
  return { x: axis(1), y: axis(2), z: axis(3) };
}

function eventBase(passage, type, sourceIndex, sourceSecond, intensity, audio, source, position = null) {
  const scale = passage.seconds;
  const point = position ?? positionFor(passage, type, sourceIndex);
  return {
    index: -1,
    id: `${passage.index}:${type}:${sourceIndex}`,
    type,
    at: passage.t0 + sourceSecond * scale,
    source_second: rounded(sourceSecond, 9),
    position: {
      x: rounded(clamp(point.x, -1, 1), 6),
      y: rounded(clamp(point.y, -1, 1), 6),
      z: rounded(clamp(point.z, -1, 1), 6),
    },
    depth: rounded((clamp(point.z, -1, 1) + 1) / 2, 6),
    intensity: rounded(clamp(intensity, 0, 1), 6),
    passage: { ...passage },
    audio: { ...audio, pitch: audio.pitch ?? null },
    source,
  };
}

function lowerBound(events, at) {
  let lo = 0;
  let hi = events.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid].at < at) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function lookupRows(events, t0, seconds) {
  const buckets = [];
  for (let second = 0; second < Math.ceil(seconds); second++) {
    const start = lowerBound(events, t0 + second);
    const end = lowerBound(events, Math.min(t0 + seconds, t0 + second + 1));
    buckets.push({ event_start: [start, end] });
  }
  return {
    quantum_seconds: 1,
    buckets,
    maxima: {
      event_starts_per_bucket: Math.max(0, ...buckets.map((bucket) => bucket.event_start[1] - bucket.event_start[0])),
    },
  };
}

function finishBus(bus) {
  bus.events.sort(
    (left, right) => left.at - right.at
      || EVENT_ORDER.get(left.type) - EVENT_ORDER.get(right.type)
      || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0),
  );
  bus.events.forEach((event, index) => { event.index = index; });
  bus.lookup = lookupRows(bus.events, bus.time.t0, bus.time.seconds);
  bus.identity.contract_sha256 = roomContractSha256(bus);
  return validateRoomBus(bus);
}

/** Compile one complete passage. Score note/cue events retain START semantics. */
export function compileRoomBus(score, declaredPassage) {
  score = validateScore(score);
  const passage = { ...validatePassage(declaredPassage) };
  const nominalSeconds = Number(score.time.duration_seconds);
  const absoluteScale = passage.seconds / nominalSeconds;
  const sourceScale = 1 / nominalSeconds;
  const stems = new Map(score.orchestration.map((stem) => [stem.id, stem]));
  const dynamicsAt = (sourceSecond) => {
    let lo = 0;
    let hi = score.dynamics.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (Number(score.dynamics[mid].second) <= sourceSecond) lo = mid + 1;
      else hi = mid;
    }
    return Number(score.dynamics[Math.max(0, lo - 1)].midi_expression) / 127;
  };
  const events = [];

  events.push(eventBase(
    passage,
    "passage.start",
    0,
    0,
    dynamicsAt(0),
    { role: null, source_sha256: null },
    { kind: "passage", index: passage.index },
    { x: 0, y: 0, z: 0 },
  ));

  for (const movement of score.movements) {
    const sourceSecond = Number(movement.start_second);
    const normalizedSecond = sourceSecond * sourceScale;
    const movementEvent = eventBase(
      passage,
      "movement.start",
      Number(movement.index),
      normalizedSecond,
      dynamicsAt(sourceSecond),
      { role: null, source_sha256: null },
      { kind: "score-movement", index: movement.index, id: movement.id },
    );
    movementEvent.at = passage.t0 + sourceSecond * absoluteScale;
    movementEvent.source_second = rounded(sourceSecond, 9);
    events.push(movementEvent);
    if (movement.id === "ASSEMBLY") {
      const assembly = eventBase(
        passage,
        "plane.assembly",
        Number(movement.index),
        normalizedSecond,
        dynamicsAt(sourceSecond),
        { role: "room-assembly", source_sha256: null },
        { kind: "score-movement", index: movement.index, id: movement.id },
        movementEvent.position,
      );
      assembly.at = movementEvent.at;
      assembly.source_second = movementEvent.source_second;
      events.push(assembly);
    }
  }

  for (const cue of score.cues) {
    const sourceSecond = Number(cue.second);
    const normalizedSecond = sourceSecond * sourceScale;
    const cueEvent = eventBase(
      passage,
      "score.cue",
      Number(cue.index),
      normalizedSecond,
      Number(cue.strength),
      { role: `room-cue:${cue.id}`, source_sha256: null },
      { kind: "score-cue", index: cue.index, id: cue.id },
    );
    cueEvent.at = passage.t0 + sourceSecond * absoluteScale;
    cueEvent.end = passage.t0 + Number(cue.end_second) * absoluteScale;
    cueEvent.source_second = rounded(sourceSecond, 9);
    events.push(cueEvent);
    if (cue.visual?.recast) {
      const recast = eventBase(
        passage,
        "plane.recast",
        Number(cue.index),
        normalizedSecond,
        Number(cue.strength),
        { role: "room-transient", source_sha256: null },
        { kind: "score-cue", index: cue.index, id: cue.id, recast_index: cue.visual.recast_index },
      );
      recast.at = cueEvent.at;
      recast.source_second = cueEvent.source_second;
      events.push(recast);
    }
  }

  for (const note of score.notes) {
    const sourceSecond = Number(note.start_second);
    const normalizedSecond = sourceSecond * sourceScale;
    const stem = stems.get(note.stem);
    const x = Number(note.pitch) / 127 * 2 - 1;
    const y = Number(note.velocity) / 127 * 2 - 1;
    const z = positionFor(passage, "score.note", Number(note.index)).z;
    const noteEvent = eventBase(
      passage,
      "score.note",
      Number(note.index),
      normalizedSecond,
      Number(note.velocity) / 127,
      { role: note.stem, source_sha256: stem?.audio_source_sha256 ?? null, pitch: note.pitch },
      {
        kind: "score-note",
        index: note.index,
        stem: note.stem,
        pitch: note.pitch,
        velocity: note.velocity,
        midi_sha256: score.identity.midi_sha256,
      },
      { x, y, z },
    );
    noteEvent.at = passage.t0 + sourceSecond * absoluteScale;
    noteEvent.end = passage.t0 + Number(note.end_second) * absoluteScale;
    noteEvent.source_second = rounded(sourceSecond, 9);
    events.push(noteEvent);
  }

  return finishBus({
    schema: ROOM_BUS_SCHEMA,
    semantics: "authored-start-events",
    release_status: score.release_status,
    identity: {
      score_contract_sha256: score.identity.contract_sha256,
      midi_sha256: score.identity.midi_sha256,
      passage: { ...passage },
    },
    time: {
      basis: "absolute-river-seconds",
      t0: passage.t0,
      t1: passage.t0 + passage.seconds,
      seconds: passage.seconds,
    },
    provenance: {
      policy: "declared-source-bytes-only",
      score_work_id: score.identity.work_id,
      repertoire_entry_sha256: score.identity.repertoire_entry_sha256,
      layout_contract_sha256: null,
    },
    events,
  });
}

export function validateRoomBus(bus) {
  const bad = (message) => { throw new TypeError(`room events: ${message}`); };
  if (!isObject(bus) || bus.schema !== ROOM_BUS_SCHEMA) bad(`unknown schema ${bus?.schema}`);
  if (bus.semantics !== "authored-start-events") bad("semantics must be authored-start-events");
  if (!["fixture-only", "artistic-gate-required", "diagnostic-only"].includes(bus.release_status)) {
    bad("release_status is invalid");
  }
  const passage = bus.identity?.passage;
  try { validatePassage(passage, "identity.passage"); } catch (error) { bad(error.message); }
  const scoreDigest = bus.identity?.score_contract_sha256;
  const midiDigest = bus.identity?.midi_sha256;
  if (bus.release_status === "diagnostic-only") {
    if (scoreDigest !== null || midiDigest !== null) bad("diagnostic buses cannot claim score or MIDI provenance");
  } else if (!SHA256.test(scoreDigest ?? "") || !SHA256.test(midiDigest ?? "")) {
    bad("score and MIDI identities must be exact SHA-256 digests");
  }
  const provenance = bus.provenance;
  if (!isObject(provenance) || provenance.policy !== "declared-source-bytes-only"
      || !(provenance.score_work_id === null || (typeof provenance.score_work_id === "string" && provenance.score_work_id))
      || !([provenance.repertoire_entry_sha256, provenance.layout_contract_sha256]
        .every((digest) => digest === null || SHA256.test(digest)))) bad("provenance is invalid");
  if (!isObject(bus.time) || bus.time.basis !== "absolute-river-seconds"
      || !finite(bus.time.t0) || !finite(bus.time.t1) || !finite(bus.time.seconds)
      || bus.time.t0 !== passage.t0 || bus.time.seconds !== passage.seconds
      || Math.abs(bus.time.t1 - (bus.time.t0 + bus.time.seconds)) > 1e-6) {
    bad("time must match the declared passage partition");
  }
  if (!Array.isArray(bus.events) || !bus.events.length) bad("events must be non-empty");
  let previous = -Infinity;
  const ids = new Set();
  const passageIdentity = canonicalSha256(passage);
  for (let index = 0; index < bus.events.length; index++) {
    const event = bus.events[index];
    if (!isObject(event) || event.index !== index || !EVENT_TYPES.has(event.type)
        || typeof event.id !== "string" || !event.id || ids.has(event.id)) bad(`event ${index} is malformed`);
    ids.add(event.id);
    if (!finite(event.at) || event.at < bus.time.t0 || !(event.at < bus.time.t1) || event.at < previous) {
      bad(`event ${index}.at is outside or out of order`);
    }
    previous = event.at;
    if (!finite(event.source_second) || event.source_second < 0) bad(`event ${index}.source_second is invalid`);
    if (!isObject(event.position) || [event.position.x, event.position.y, event.position.z]
      .some((value) => !finite(value) || value < -1 || value > 1)) bad(`event ${index}.position is invalid`);
    if (!finite(event.depth) || event.depth < 0 || event.depth > 1
        || !finite(event.intensity) || event.intensity < 0 || event.intensity > 1) bad(`event ${index} bounds are invalid`);
    if (!isObject(event.audio) || Object.keys(event.audio).sort().join(",") !== "pitch,role,source_sha256"
        || !(event.audio.role === null || (typeof event.audio.role === "string" && event.audio.role))
        || !(event.audio.source_sha256 === null || SHA256.test(event.audio.source_sha256))
        || !(event.audio.pitch === null
          || (Number.isInteger(event.audio.pitch) && event.audio.pitch >= 0 && event.audio.pitch <= 127))) {
      bad(`event ${index}.audio is invalid`);
    }
    if (!isObject(event.source)) bad(`event ${index}.source is invalid`);
    if (!isObject(event.passage) || canonicalSha256(event.passage) !== passageIdentity) {
      bad(`event ${index}.passage does not match identity`);
    }
    if (event.end !== undefined && (!finite(event.end) || event.end < event.at || event.end > bus.time.t1 + 1e-6)) {
      bad(`event ${index}.end is invalid`);
    }
    if (event.target_speaker !== undefined && (typeof event.target_speaker !== "string" || !event.target_speaker)) {
      bad(`event ${index}.target_speaker is invalid`);
    }
  }
  const buckets = bus.lookup?.buckets;
  if (bus.lookup?.quantum_seconds !== 1 || !Array.isArray(buckets)
      || buckets.length !== Math.ceil(bus.time.seconds)) bad("lookup must contain one bucket per passage second");
  let maximum = 0;
  for (let index = 0; index < buckets.length; index++) {
    const range = buckets[index]?.event_start;
    const expected = [
      lowerBound(bus.events, bus.time.t0 + index),
      lowerBound(bus.events, Math.min(bus.time.t1, bus.time.t0 + index + 1)),
    ];
    if (!Array.isArray(range) || range.length !== 2 || range[0] !== expected[0] || range[1] !== expected[1]) {
      bad(`lookup bucket ${index}.event_start is stale`);
    }
    maximum = Math.max(maximum, range[1] - range[0]);
  }
  if (bus.lookup?.maxima?.event_starts_per_bucket !== maximum) bad("lookup maxima is stale");
  const declared = bus.identity?.contract_sha256;
  if (!SHA256.test(declared ?? "") || declared !== roomContractSha256(bus)) bad("identity.contract_sha256 is stale");
  return bus;
}

/** Authored room START events in absolute half-open [start, end). */
export function roomEventsBetween(bus, start, end) {
  if (!finite(start) || !finite(end) || end < start) throw new RangeError("invalid room event interval");
  if (start === end || end <= bus.time.t0 || start >= bus.time.t1) return [];
  const clippedStart = Math.max(start, bus.time.t0);
  const clippedEnd = Math.min(end, bus.time.t1);
  const first = Math.max(0, Math.floor(clippedStart - bus.time.t0) - 1);
  const last = Math.min(bus.lookup.buckets.length, Math.ceil(clippedEnd - bus.time.t0) + 1);
  const indices = new Set();
  for (let bucket = first; bucket < last; bucket++) {
    const [from, to] = bus.lookup.buckets[bucket].event_start;
    for (let index = from; index < to; index++) indices.add(index);
  }
  return [...indices]
    .sort((left, right) => left - right)
    .map((index) => bus.events[index])
    .filter((event) => event.at >= start && event.at < end);
}

export function validateRoomLayouts(registry) {
  const bad = (message) => { throw new TypeError(`room layouts: ${message}`); };
  if (!isObject(registry) || registry.schema !== ROOM_LAYOUT_SCHEMA) bad(`unknown schema ${registry?.schema}`);
  if (typeof registry.identity?.id !== "string" || !registry.identity.id
      || registry.identity.status !== "reference-simulation") bad("registry identity is invalid");
  const declared = registry.identity?.contract_sha256;
  if (!SHA256.test(declared ?? "") || declared !== layoutContractSha256(registry)) bad("identity.contract_sha256 is stale");
  const coordinate = registry.coordinate_system;
  if (!isObject(coordinate) || coordinate.units !== "normalized-room"
      || !isObject(coordinate.axes)
      || Object.keys(coordinate.axes).sort().join(",") !== "x,y,z"
      || coordinate.axes.x !== "left-to-right"
      || coordinate.axes.y !== "floor-to-ceiling"
      || coordinate.axes.z !== "far-to-near"
      || !Array.isArray(coordinate.listener) || coordinate.listener.length !== 3
      || coordinate.listener.some((value) => !finite(value) || value < -1 || value > 1)
      || !finite(coordinate.meters_per_unit) || !(coordinate.meters_per_unit > 0)) bad("coordinate system is invalid");
  const safety = registry.safety;
  if (!isObject(safety) || !finite(safety.max_event_gain) || !(safety.max_event_gain > 0 && safety.max_event_gain <= 1)
      || !finite(safety.limiter_ceiling_dbfs) || safety.limiter_ceiling_dbfs > 0
      || !finite(safety.latency_budget_ms) || safety.latency_budget_ms < 0 || safety.latency_budget_ms > 100
      || !finite(safety.speed_of_sound_mps) || safety.speed_of_sound_mps < 300 || safety.speed_of_sound_mps > 400) {
    bad("safety limits are invalid");
  }
  if (!Array.isArray(registry.layouts) || registry.layouts.length < 2) bad("at least stereo and multichannel layouts are required");
  const layoutIds = new Set();
  for (const layout of registry.layouts) {
    if (!isObject(layout) || typeof layout.id !== "string" || !layout.id || layoutIds.has(layout.id)) {
      bad("layout ids are missing or duplicated");
    }
    layoutIds.add(layout.id);
    if (!new Set(["portable-fallback", "reference-simulation"]).has(layout.status)) bad(`${layout.id}.status is invalid`);
    if (!Array.isArray(layout.speakers) || layout.speakers.length < 2) bad(`${layout.id}.speakers is invalid`);
    const speakerIds = new Set();
    const channels = [];
    for (const speaker of layout.speakers) {
      if (!isObject(speaker) || typeof speaker.id !== "string" || !speaker.id || speakerIds.has(speaker.id)) {
        bad(`${layout.id} speaker ids are invalid`);
      }
      speakerIds.add(speaker.id);
      channels.push(speaker.channel);
      if (!Number.isInteger(speaker.channel) || !Array.isArray(speaker.position) || speaker.position.length !== 3
          || speaker.position.some((value) => !finite(value) || value < -1 || value > 1)) bad(`${layout.id}.${speaker.id} is invalid`);
    }
    const sortedChannels = [...channels].sort((a, b) => a - b);
    if (new Set(channels).size !== channels.length || sortedChannels.some((channel, index) => channel !== index)) {
      bad(`${layout.id} channels must be unique and contiguous from zero`);
    }
    const matrix = layout.stereo_fold_down?.matrix;
    if (JSON.stringify(layout.stereo_fold_down?.outputs) !== JSON.stringify(["left", "right"])
        || !Array.isArray(matrix) || matrix.length !== layout.speakers.length
        || matrix.some((row) => !Array.isArray(row) || row.length !== 2
          || row.some((value) => !finite(value) || value < -1 || value > 1)
          || Math.hypot(...row) === 0 || Math.hypot(...row) > 1 + 1e-12)) {
      bad(`${layout.id} stereo fold-down is invalid`);
    }
  }
  if (!layoutIds.has(registry.default_layout)) bad("default_layout does not exist");
  return registry;
}

export async function loadRoomLayouts(url = "sound/room-layout.json") {
  const registry = await fetch(url).then((response) => {
    if (!response.ok) throw new Error(`room layouts ${response.status} at ${url}`);
    return response.json();
  });
  return validateRoomLayouts(registry);
}

export function roomLayout(registry, id = registry.default_layout) {
  const layout = registry.layouts.find((candidate) => candidate.id === id);
  if (!layout) throw new RangeError(`unknown room layout ${id}`);
  return layout;
}

function distance(event, speaker, registry) {
  // Event positions are listener-relative vectors; speaker positions are
  // absolute normalized-room coordinates, so the listener anchors the event.
  const listener = registry.coordinate_system.listener;
  const meters = registry.coordinate_system.meters_per_unit;
  const axes = [event.position.x, event.position.y, event.position.z];
  return Math.hypot(...axes.map((value, index) => (value - speaker.position[index] + listener[index]) * meters));
}

function routeEvent(event, registry, layout) {
  const safety = registry.safety;
  const direct = event.target_speaker ? layout.speakers.find((speaker) => speaker.id === event.target_speaker) : null;
  if (event.target_speaker && !direct) throw new RangeError(`event ${event.id} names unknown target speaker ${event.target_speaker}`);
  const distances = layout.speakers.map((speaker) => distance(event, speaker, registry));
  const nearest = Math.min(...distances);
  const weights = layout.speakers.map((speaker, index) => {
    if (direct) return speaker.id === direct.id ? 1 : 0;
    return 1 / Math.max(0.25, distances[index]);
  });
  const norm = Math.hypot(...weights) || 1;
  const eventGain = Math.min(event.intensity, safety.max_event_gain);
  const multichannel = [];
  for (let index = 0; index < layout.speakers.length; index++) {
    if (weights[index] === 0) continue;
    const delay = direct ? 0 : (distances[index] - nearest) / safety.speed_of_sound_mps * 1000;
    if (delay > safety.latency_budget_ms + 1e-9) {
      throw new RangeError(`event ${event.id} exceeds latency budget at ${layout.speakers[index].id}`);
    }
    multichannel.push({
      speaker: layout.speakers[index].id,
      channel: layout.speakers[index].channel,
      gain: rounded(eventGain * weights[index] / norm, 12),
      delay_ms: rounded(delay, 9),
    });
  }
  const stereo = [];
  for (const tap of multichannel) {
    const speakerIndex = layout.speakers.findIndex((speaker) => speaker.id === tap.speaker);
    const row = layout.stereo_fold_down.matrix[speakerIndex];
    for (let channel = 0; channel < 2; channel++) {
      const gain = tap.gain * row[channel];
      if (gain !== 0) {
        stereo.push({
          output: layout.stereo_fold_down.outputs[channel],
          channel,
          source_speaker: tap.speaker,
          gain: rounded(gain, 12),
          delay_ms: tap.delay_ms,
        });
      }
    }
  }
  return {
    event_index: event.index,
    id: event.id,
    type: event.type,
    at: event.at,
    ...(event.end === undefined ? {} : { end: event.end }),
    passage: event.passage,
    audio: event.audio,
    multichannel,
    stereo,
  };
}

/** One renderer-neutral plan consumed by live, stereo, offline, and room paths. */
export function planRoomRender(bus, registry, layoutId, start, end) {
  const layout = roomLayout(registry, layoutId);
  return {
    schema: ROOM_PLAN_SCHEMA,
    bus_contract_sha256: bus.identity.contract_sha256,
    layout_contract_sha256: registry.identity.contract_sha256,
    layout: layout.id,
    interval: { start, end },
    safety: { ...registry.safety },
    events: roomEventsBetween(bus, start, end).map((event) => routeEvent(event, registry, layout)),
  };
}

/** Diagnostic-only direct-to-speaker impulses; never part of the artwork. */
export function calibrationBus(registry, layoutId, { t0 = 0, spacing = 0.25 } = {}) {
  const layout = roomLayout(registry, layoutId);
  if (!finite(t0) || t0 < 0 || !finite(spacing) || !(spacing > 0)) throw new RangeError("invalid calibration timing");
  const seconds = Math.max(1, layout.speakers.length * spacing);
  const passage = { river_seed: 0, stream: 0, index: 0, seed: 0, t0, seconds };
  const events = layout.speakers.map((speaker, index) => {
    const event = eventBase(
      passage,
      "calibration.impulse",
      index,
      index * spacing / seconds,
      0.25,
      { role: "calibration-impulse", source_sha256: null },
      { kind: "diagnostic-only", layout: layout.id, speaker: speaker.id },
      { x: speaker.position[0], y: speaker.position[1], z: speaker.position[2] },
    );
    event.at = t0 + index * spacing;
    event.source_second = rounded(index * spacing, 9);
    event.target_speaker = speaker.id;
    return event;
  });
  return finishBus({
    schema: ROOM_BUS_SCHEMA,
    semantics: "authored-start-events",
    release_status: "diagnostic-only",
    identity: { score_contract_sha256: null, midi_sha256: null, passage },
    time: { basis: "absolute-river-seconds", t0, t1: t0 + seconds, seconds },
    provenance: {
      policy: "declared-source-bytes-only",
      score_work_id: null,
      repertoire_entry_sha256: null,
      layout_contract_sha256: registry.identity.contract_sha256,
    },
    events,
  });
}

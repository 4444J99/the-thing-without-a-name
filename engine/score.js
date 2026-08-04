/** Immutable musical-score queries on absolute time.
 *
 * A score is compiled once. Query cost depends only on the bounded contents of
 * one pre-indexed one-second bucket, never on how long the river has run. A
 * passage maps the nominal score onto its own absolute [t0, t1) span, so live,
 * offline, segmented, and restarted consumers ask the same pure question.
 */

const SCHEMA = "danse.music.score.v1";

const clamp01 = (value) => (value < 0 ? 0 : value > 1 ? 1 : value);

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

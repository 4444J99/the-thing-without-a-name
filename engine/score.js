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

export function validate(score) {
  const bad = (message) => {
    throw new Error(`music score: ${message}`);
  };
  if (score?.schema !== SCHEMA) bad(`unknown schema ${score?.schema}`);
  const duration = score.time?.duration_seconds;
  if (!(duration > 0)) bad("time.duration_seconds must be positive");
  const buckets = score.lookup?.buckets;
  if (score.lookup?.quantum_seconds !== 1 || !Array.isArray(buckets) || buckets.length !== Math.ceil(duration)) {
    bad("lookup must contain one immutable bucket per source second");
  }
  for (const name of ["tempo", "meter", "beats", "phrases", "dynamics", "movements"]) {
    if (!Array.isArray(score[name]) || !score[name].length) bad(`${name} must be non-empty`);
  }
  if (!Array.isArray(score.cues) || !Array.isArray(score.notes) || !Array.isArray(score.orchestration)) {
    bad("cues, notes, and orchestration must be arrays");
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
  const cues = bucket.active_cues
    .map((index) => score.cues[index])
    .filter((cue) => cue.second <= mapped.sourceSecond && mapped.sourceSecond < cue.end_second);
  const offsets = {};
  let hold = false;
  let recast = bucket.recast;
  for (const cue of cues) {
    hold ||= cue.visual.hold;
    if (cue.visual.recast) recast = Math.max(recast, cue.visual.recast_index);
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

function mapEvent(event, cycleStart, sourceDuration, scale) {
  const sourceStart = event.start_second ?? event.second;
  const sourceEnd = event.end_second ?? sourceStart;
  return {
    ...event,
    at: cycleStart + sourceStart * scale,
    end: cycleStart + sourceEnd * scale,
  };
}

/** Authored note/cue starts in [start, end), mapped to absolute playback time. */
export function eventsBetween(score, start, end, window = null) {
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) throw new RangeError("invalid score event interval");
  const duration = score.time.duration_seconds;
  const windows = [];
  if (window) {
    if (!(window.seconds > 0)) throw new RangeError("score window seconds must be positive");
    windows.push({ t0: window.t0, seconds: window.seconds, scale: window.seconds / duration });
  } else {
    const first = Math.floor(Math.max(0, start) / duration);
    const last = Math.floor(Math.max(0, Math.max(start, end - Number.EPSILON)) / duration);
    if (last - first > 10_000) throw new RangeError("score event interval exceeds 10,000 cycles");
    for (let cycle = first; cycle <= last; cycle++) windows.push({ t0: cycle * duration, seconds: duration, scale: 1 });
  }
  const events = [];
  for (const mapped of windows) {
    const windowEnd = mapped.t0 + mapped.seconds;
    if (windowEnd <= start || mapped.t0 >= end) continue;
    for (const cue of score.cues) {
      const event = mapEvent(cue, mapped.t0, duration, mapped.scale);
      if (event.at >= start && event.at < end) events.push({ type: "cue", ...event });
    }
    for (const note of score.notes) {
      const event = mapEvent(note, mapped.t0, duration, mapped.scale);
      if (event.at >= start && event.at < end) events.push({ type: "note", ...event });
    }
  }
  return events.sort((a, b) => a.at - b.at || a.type.localeCompare(b.type) || a.index - b.index);
}

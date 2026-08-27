/** Score-led photographic choreography, queried as a pure function of time.
 *
 * The contract authors ordered motifs. It does not infer chronology from file
 * names and it never asks each fragment to choose a photograph independently.
 * A phrase enters as one coherent pose; declared counterpoint then permits one
 * bounded anatomical cohort to hold a different authored moment.
 */

import { canonicalSha256 } from "./score.js";
import { scoreAt } from "./score.js";

const SCHEMA = "danse.choreography.v1";
const SHA256 = /^[0-9a-f]{64}$/;
const MOVEMENT_CUT = Object.freeze({
  ONE: "solo",
  ASSEMBLY: "score",
  DIVISION: "score",
  PHRASE: "grid",
  STILLNESS: "figure",
  RESEED: "bands",
  SIGNATURE: "black",
});

const clamp01 = (value) => (value < 0 ? 0 : value > 1 ? 1 : value);
const smooth = (value) => {
  const x = clamp01(value);
  return x * x * (3 - 2 * x);
};
const rounded = (value) => Number(value.toFixed(12));

/** SHA-256 over a choreography with its self-identifying digest omitted. */
export function contractSha256(choreography) {
  const identity = choreography?.identity;
  if (!identity || typeof identity !== "object" || Array.isArray(identity)) {
    throw new TypeError("choreography identity must be an object");
  }
  const { contract_sha256: _declared, ...identitySource } = identity;
  return canonicalSha256({ ...choreography, identity: identitySource });
}

export async function load(url = "render/choreography.json", { score = null, corpus = null } = {}) {
  const choreography = await fetch(url).then((response) => {
    if (!response.ok) throw new Error(`choreography ${response.status} at ${url}`);
    return response.json();
  });
  return validate(choreography, { score, corpus });
}

/** Validate authored motifs, score bindings, digests, and legibility limits. */
export function validate(choreography, { score = null, corpus = null } = {}) {
  const bad = (message) => {
    throw new Error(`choreography: ${message}`);
  };
  if (!choreography || typeof choreography !== "object" || Array.isArray(choreography)) bad("root must be an object");
  if (choreography.schema !== SCHEMA) bad(`unknown schema ${choreography.schema}`);
  const identity = choreography.identity;
  if (!identity || typeof identity !== "object" || Array.isArray(identity)) bad("identity must be an object");
  for (const name of [
    "score_contract_sha256",
    "score_file_sha256",
    "corpus_manifest_sha256",
    "corpus_score_sha256",
    "contract_sha256",
  ]) {
    if (!SHA256.test(identity[name] ?? "")) bad(`identity.${name} must be SHA-256`);
  }
  let actual;
  try {
    actual = contractSha256(choreography);
  } catch (error) {
    bad(`cannot verify identity.contract_sha256: ${error.message}`);
  }
  if (actual !== identity.contract_sha256) bad("identity.contract_sha256 does not match the choreography content");

  const limits = choreography.legibility;
  if (!limits || typeof limits !== "object" || Array.isArray(limits)) bad("legibility must be an object");
  if (!(limits.minimum_pose_dwell_bars >= 2)) bad("minimum_pose_dwell_bars must be at least two");
  if (!(limits.minimum_pose_transition_bars >= 1)) bad("minimum_pose_transition_bars must be at least one");
  if (!(limits.minimum_topology_transition_bars >= 1)) bad("minimum_topology_transition_bars must be at least one");
  if (!(limits.maximum_fragment_change_area_per_bar >= 0 && limits.maximum_fragment_change_area_per_bar <= 0.25)) {
    bad("maximum_fragment_change_area_per_bar must be in [0, 0.25]");
  }
  if (!(limits.fragment_counterpoint_fraction > 0 && limits.fragment_counterpoint_fraction <= 0.25)) {
    bad("fragment_counterpoint_fraction must be in (0, 0.25]");
  }
  if (limits.counterpoint_groups !== 4) bad("counterpoint_groups must be four anatomical cohorts");
  if (!(limits.counterpoint_pose_dwell_bars >= 2)) bad("counterpoint_pose_dwell_bars must be at least two");
  if (!(limits.counterpoint_transition_bars >= 1)) bad("counterpoint_transition_bars must be at least one");
  if (limits.hard_global_recast_before_signature !== false) bad("hard global recasts before SIGNATURE are forbidden");
  if (limits.frame_order_semantics !== "authored-motif-not-chronology") bad("frame order semantics are not explicit");

  const motifs = choreography.motifs;
  if (!Array.isArray(motifs) || !motifs.length) bad("motifs must be non-empty");
  const motifById = new Map();
  const frameById = corpus?.byId ?? null;
  for (const [index, motif] of motifs.entries()) {
    if (!motif || typeof motif !== "object" || Array.isArray(motif) || !motif.id) bad(`motif ${index} is malformed`);
    if (motifById.has(motif.id)) bad(`duplicate motif ${motif.id}`);
    if (!Array.isArray(motif.source_frame_ids) || !motif.source_frame_ids.length
        || motif.source_frame_ids.some((id) => typeof id !== "string" || !id)) {
      bad(`motif ${motif.id} must contain ordered source_frame_ids`);
    }
    if (new Set(motif.source_frame_ids).size !== motif.source_frame_ids.length) bad(`motif ${motif.id} repeats a source frame`);
    if (motif.geometry_frame_id !== null && motif.geometry_frame_id !== undefined
        && !motif.source_frame_ids.includes(motif.geometry_frame_id)) {
      bad(`motif ${motif.id}.geometry_frame_id must belong to the motif`);
    }
    if (frameById) {
      for (const id of motif.source_frame_ids) {
        const frame = frameById.get(id);
        if (!frame) bad(`motif ${motif.id} names absent source frame ${id}`);
        if (frame.registered === false) bad(`motif ${motif.id} names unregistered source frame ${id}`);
      }
    }
    motifById.set(motif.id, { ...motif, index });
  }

  const assignments = choreography.phrase_assignments;
  if (!Array.isArray(assignments) || !assignments.length) bad("phrase_assignments must be non-empty");
  const seenPhrases = new Set();
  for (const [index, assignment] of assignments.entries()) {
    if (!assignment || typeof assignment !== "object" || Array.isArray(assignment)) bad(`phrase assignment ${index} is malformed`);
    if (!assignment.phrase_id || seenPhrases.has(assignment.phrase_id)) bad(`duplicate or missing phrase assignment ${assignment.phrase_id}`);
    seenPhrases.add(assignment.phrase_id);
    if (!(assignment.movement_id in MOVEMENT_CUT)) bad(`${assignment.phrase_id} names unknown movement ${assignment.movement_id}`);
    if (assignment.cut_mode !== MOVEMENT_CUT[assignment.movement_id]) {
      bad(`${assignment.phrase_id} must use ${MOVEMENT_CUT[assignment.movement_id]} for ${assignment.movement_id}`);
    }
    const motif = assignment.motif_id === null ? null : motifById.get(assignment.motif_id);
    if (assignment.motif_id !== null && !motif) bad(`${assignment.phrase_id} names unknown motif ${assignment.motif_id}`);
    if (["ASSEMBLY", "SIGNATURE"].includes(assignment.movement_id) && assignment.motif_id !== null) {
      bad(`${assignment.movement_id} must not replace its authored ${assignment.cut_mode} material`);
    }
    if (!["ASSEMBLY", "SIGNATURE"].includes(assignment.movement_id) && !motif) {
      bad(`${assignment.movement_id} requires a photographic motif`);
    }
    if (!(assignment.pose_dwell_bars >= limits.minimum_pose_dwell_bars)) bad(`${assignment.phrase_id} pose dwell is too short`);
    if (!(assignment.pose_transition_bars >= limits.minimum_pose_transition_bars)) bad(`${assignment.phrase_id} pose transition is too short`);
    if (!(assignment.topology_transition_bars >= limits.minimum_topology_transition_bars)) {
      bad(`${assignment.phrase_id} topology transition is too short`);
    }
    if (typeof assignment.hold_complete_phrase !== "boolean") bad(`${assignment.phrase_id} hold_complete_phrase must be boolean`);
    if (["ONE", "STILLNESS"].includes(assignment.movement_id) && motif.source_frame_ids.length !== 1) {
      bad(`${assignment.movement_id} must name exactly one readable source frame`);
    }
  }

  for (const movement of ["ONE", "ASSEMBLY", "STILLNESS"]) {
    const owned = assignments.filter((assignment) => assignment.movement_id === movement);
    if (!owned.length || !owned.some((assignment) => assignment.hold_complete_phrase)) {
      bad(`${movement} must include at least one complete-phrase hold`);
    }
  }
  if (assignments.some((assignment) => assignment.movement_id === "SIGNATURE" && !assignment.hold_complete_phrase)) {
    bad("SIGNATURE must hold black through its complete phrase");
  }
  for (let index = 0; index + 1 < assignments.length; index++) {
    const current = assignments[index];
    const next = assignments[index + 1];
    const changes = current.movement_id !== next.movement_id
      || current.cut_mode !== next.cut_mode
      || current.motif_id !== next.motif_id;
    if (changes && current.hold_complete_phrase) {
      bad(`${current.phrase_id} cannot own an outgoing transition while holding its complete phrase`);
    }
  }

  if (score) {
    if (identity.score_contract_sha256 !== score.identity?.contract_sha256) bad("score contract digest does not match");
    if (score.fileSha256 && identity.score_file_sha256 !== score.fileSha256) bad("score file digest does not match");
    const phraseIds = score.phrases.map((phrase) => phrase.id);
    const assignmentIds = assignments.map((assignment) => assignment.phrase_id);
    if (JSON.stringify(assignmentIds) !== JSON.stringify(phraseIds)) bad("phrase assignments must match score phrase order exactly");
    if (score.time?.passage_mapping !== "native-tempo") bad("production choreography requires a native-tempo score");
    const last = assignments.at(-1);
    const lastPhrase = score.phrases.at(-1);
    if (last.movement_id !== "SIGNATURE" || last.cut_mode !== "black") bad("last phrase must be SIGNATURE black");
    if (Math.abs((lastPhrase.end_second - lastPhrase.start_second) - 4) > 1e-6) bad("SIGNATURE phrase must last exactly four seconds");
  }
  if (corpus?.identity) {
    if (identity.corpus_manifest_sha256 !== corpus.identity.manifest_sha256) bad("corpus manifest digest does not match");
    if (identity.corpus_score_sha256 !== corpus.identity.score_sha256) bad("corpus score digest does not match");
  }
  return choreography;
}

function barCoordinate(music) {
  return (music.beat.bar - 1) + ((music.beat.beat_in_bar - 1) + music.beat.phase) / music.meter.numerator;
}

function phraseEdgeCoordinate(score, phrase, edge) {
  const duration = score.time.duration_seconds;
  const second = edge === "start"
    ? phrase.start_second
    : Math.max(phrase.start_second, Math.min(duration, phrase.end_second) - 1e-8);
  let coordinate = barCoordinate(scoreAt(score, second));
  if (edge === "end") coordinate += 1e-8 * scoreAt(score, second).tempo.effective_bpm / 60 / scoreAt(score, second).meter.numerator;
  const nearest = Math.round(coordinate);
  return Math.abs(coordinate - nearest) < 1e-6 ? nearest : coordinate;
}

function motifPose(motif, assignment, barsIntoPhrase, usableBars = Infinity) {
  if (!motif) return { index: 0, current: null, next: null, blend: 0 };
  const frames = motif.source_frame_ids;
  if (assignment.hold_complete_phrase || frames.length === 1) {
    return { index: 0, current: frames[0], next: frames[0], blend: 0 };
  }
  const hold = assignment.pose_dwell_bars;
  const transition = assignment.pose_transition_bars;
  const span = hold + transition;
  // Do not begin a dissolve unless the arriving pose can then receive its full
  // authored dwell before the phrase's reserved boundary transition. This is
  // what turns the declared two-bar minimum into rendered timing rather than a
  // schema-only aspiration.
  const transitions = Number.isFinite(usableBars)
    ? Math.max(0, Math.floor((Math.max(0, usableBars) - hold + 1e-9) / span))
    : Infinity;
  const cycle = Math.max(0, Math.min(transitions, Math.floor(barsIntoPhrase / span)));
  const phase = Math.max(0, barsIntoPhrase - cycle * span);
  const current = frames[cycle % frames.length];
  const next = frames[(cycle + 1) % frames.length];
  const blend = cycle >= transitions || phase < hold || current === next
    ? 0
    : smooth((phase - hold) / transition);
  return { index: cycle, current, next, blend };
}

/** Four simultaneous, authored moments of one phrase.
 *
 * This is selection counterpoint, not transform modulation.  The cohorts are
 * spatial body strata (the grammar assigns the lowest stratum to the legs).
 * They begin as four held source moments; thereafter only one cohort dissolves
 * during a full bar, then it holds for two bars before the next cohort moves.
 */
function panelCounterpoint(motif, assignment, bars, limits) {
  if (!motif || assignment.hold_complete_phrase || motif.source_frame_ids.length < 2
      || !["PHRASE", "RESEED"].includes(assignment.movement_id)) return null;
  const frames = motif.source_frame_ids;
  const groups = limits.counterpoint_groups;
  const dwell = limits.counterpoint_pose_dwell_bars;
  const transition = limits.counterpoint_transition_bars;
  const start = dwell;
  const elapsed = Math.max(0, bars - start);
  const slotLength = dwell + transition;
  const slot = Math.floor(elapsed / slotLength);
  const slotPhase = elapsed - slot * slotLength;
  const activeGroup = bars < start || slotPhase >= transition ? null : slot % groups;
  return {
    groups: Array.from({ length: groups }, (_, group) => {
      const updates = slot < group ? 0 : Math.floor((slot - group) / groups) + 1;
      const completed = activeGroup === group ? Math.max(0, updates - 1) : updates;
      // Every phrase enters on its single coherent first frame.  Counterpoint
      // is then introduced one anatomical cohort at a time; it never appears as
      // an all-panel recast at a phrase boundary.
      const frameIndex = (count) => count === 0 ? 0 : group + 1 + groups * (count - 1);
      const currentIndex = frameIndex(completed);
      const nextIndex = frameIndex(completed + 1);
      const changing = activeGroup === group && slotPhase < transition;
      return {
        group,
        current_source_frame_id: frames[currentIndex % frames.length],
        next_source_frame_id: changing ? frames[nextIndex % frames.length] : frames[currentIndex % frames.length],
        blend: changing ? smooth(slotPhase / transition) : 0,
      };
    }),
    active_group: activeGroup,
    fragment_change_fraction: activeGroup === null ? 0 : limits.fragment_counterpoint_fraction,
  };
}

/**
 * Pure photographic pose at score time t.
 *
 * `window` exists only so the shared engine can place a native score at a
 * passage's absolute t0. Production validation rejects any duration change.
 */
export function poseAt(score, choreography, seed, t, window = null) {
  if (!Number.isInteger(seed) || seed < 0 || seed > 0xFFFFFFFF) throw new RangeError("choreography seed must be uint32");
  const music = scoreAt(score, t, window);
  const phraseIndex = music.phrase.index;
  const assignment = choreography.phrase_assignments[phraseIndex];
  if (!assignment || assignment.phrase_id !== music.phrase.id) {
    throw new Error(`choreography: no ordered assignment for score phrase ${music.phrase.id}`);
  }
  const motifById = new Map(choreography.motifs.map((motif) => [motif.id, motif]));
  const motif = assignment.motif_id === null ? null : motifById.get(assignment.motif_id);
  let movementFirst = phraseIndex;
  let movementLast = phraseIndex;
  while (movementFirst > 0
      && choreography.phrase_assignments[movementFirst - 1].movement_id === assignment.movement_id) movementFirst--;
  while (movementLast + 1 < choreography.phrase_assignments.length
      && choreography.phrase_assignments[movementLast + 1].movement_id === assignment.movement_id) movementLast++;
  const movementStart = score.phrases[movementFirst].start_second;
  const movementEnd = score.phrases[movementLast].end_second;
  const movementU = clamp01((music.source_second - movementStart) / (movementEnd - movementStart));
  const startBar = phraseEdgeCoordinate(score, music.phrase, "start");
  const endBar = phraseEdgeCoordinate(score, music.phrase, "end");
  const atBar = barCoordinate(music);
  const barsIntoPhrase = Math.max(0, atBar - startBar);
  const nextAssignment = choreography.phrase_assignments[phraseIndex + 1] ?? null;
  const topologyChanges = nextAssignment
    ? nextAssignment.cut_mode !== assignment.cut_mode || nextAssignment.movement_id !== assignment.movement_id
    : false;
  const outgoingTransitionBars = nextAssignment && assignment.movement_id !== "SIGNATURE" && !assignment.hold_complete_phrase
    ? Math.max(assignment.pose_transition_bars, topologyChanges ? assignment.topology_transition_bars : 0)
    : 0;
  const usableBars = Math.max(0, endBar - startBar - outgoingTransitionBars);
  let pose = motifPose(motif, assignment, barsIntoPhrase, usableBars);
  let currentCut = assignment.cut_mode;
  let nextCut = currentCut;
  let transitionKind = null;
  let transitionBars = assignment.pose_transition_bars;
  let transitionProgress = pose.blend;
  let nextMovementId = assignment.movement_id;
  let currentGeometry = motif?.geometry_frame_id ?? null;
  let nextGeometry = currentGeometry;
  const counterpoint = panelCounterpoint(motif, assignment, barsIntoPhrase, choreography.legibility);

  if (nextAssignment && assignment.movement_id !== "SIGNATURE" && !assignment.hold_complete_phrase) {
    const boundaryBars = outgoingTransitionBars;
    const remaining = Math.max(0, endBar - atBar);
    if (remaining <= boundaryBars + 1e-9) {
      const transitionStart = Math.max(0, endBar - boundaryBars - startBar);
      const frozen = motifPose(motif, assignment, transitionStart, usableBars);
      const nextMotif = nextAssignment.motif_id === null ? null : motifById.get(nextAssignment.motif_id);
      const arriving = motifPose(nextMotif, nextAssignment, 0);
      const progress = smooth((boundaryBars - remaining) / boundaryBars);
      pose = {
        index: frozen.index,
        current: frozen.blend >= 0.5 ? frozen.next : frozen.current,
        next: arriving.current,
        blend: progress,
      };
      currentCut = assignment.cut_mode;
      nextCut = nextAssignment.cut_mode;
      nextMovementId = nextAssignment.movement_id;
      nextGeometry = nextMotif?.geometry_frame_id ?? null;
      transitionKind = topologyChanges ? "topology" : "pose";
      transitionBars = boundaryBars;
      transitionProgress = progress;
    }
  }

  return {
    identity: choreography.identity.contract_sha256,
    seed: seed >>> 0,
    motif: motif?.id ?? null,
    pose_index: pose.index,
    current_source_frame_id: pose.current,
    next_source_frame_id: pose.next,
    blend: rounded(pose.blend),
    movement_id: assignment.movement_id,
    next_movement_id: nextMovementId,
    movement_start_second: movementStart,
    movement_end_second: movementEnd,
    movement_u: rounded(movementU),
    cut_mode: assignment.cut_mode,
    current_cut_mode: currentCut,
    next_cut_mode: nextCut,
    current_geometry_frame_id: currentGeometry,
    next_geometry_frame_id: nextGeometry,
    phrase: {
      id: music.phrase.id,
      index: phraseIndex,
      start_second: music.phrase.start_second,
      end_second: music.phrase.end_second,
      bars_elapsed: rounded(barsIntoPhrase),
    },
    beat: {
      index: music.beat.index,
      bar: music.beat.bar,
      beat_in_bar: music.beat.beat_in_bar,
      phase: rounded(music.beat.phase),
      downbeat: music.beat.downbeat,
    },
    transition: {
      active: transitionKind !== null || pose.blend > 0,
      kind: transitionKind ?? (pose.blend > 0 ? "pose" : null),
      bars: transitionBars,
      progress: rounded(transitionProgress),
      fragment_change_fraction: rounded(counterpoint?.fragment_change_fraction ?? 0),
    },
    panel_counterpoint: counterpoint && {
      active_group: counterpoint.active_group,
      fragment_change_fraction: rounded(counterpoint.fragment_change_fraction),
      groups: counterpoint.groups.map((group) => ({ ...group, blend: rounded(group.blend) })),
    },
  };
}

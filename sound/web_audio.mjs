/** Provenance-preserving WebAudio adapter for the compiled musical score.
 *
 * This module never synthesises. It schedules only caller-supplied AudioBuffers
 * keyed by declared orchestration stem and paired with their verified source
 * digest. The pure note-on plan is identical for a live AudioContext,
 * OfflineAudioContext, or audio-disabled caller; it does not invent sustained
 * voice state at an arbitrary seek boundary.
 */

import { eventsBetween } from "../engine/score.js";

export function planWebAudio(score, start, end, window = null) {
  const stems = new Map(score.orchestration.map((stem) => [stem.id, stem]));
  return eventsBetween(score, start, end, window)
    .filter((event) => event.type === "note")
    .map((event) => {
      const declared = stems.get(event.stem);
      return {
        identity: score.identity.contract_sha256,
        index: event.index,
        at: event.at,
        end: event.end,
        stem: event.stem,
        pitch: event.pitch,
        velocity: event.velocity,
        midi_source_sha256: score.identity.midi_sha256,
        audio_source_sha256: declared?.audio_source_sha256 ?? null,
      };
    });
}

function bufferFor(buffers, stem) {
  return typeof buffers?.get === "function" ? buffers.get(stem) : buffers?.[stem];
}

export function scheduleWebAudio(context, score, buffers, start, end, { window = null, when = context.currentTime } = {}) {
  const plan = planWebAudio(score, start, end, window);
  const scheduled = [];
  const missing = [];
  const blocked = [];
  for (const event of plan) {
    if (!/^[0-9a-f]{64}$/.test(event.audio_source_sha256 ?? "")) {
      blocked.push({ ...event, reason: "stem has no cleared audio-source identity" });
      continue;
    }
    const supplied = bufferFor(buffers, event.stem);
    if (!supplied) {
      missing.push(event);
      continue;
    }
    const wrapped = Object.prototype.hasOwnProperty.call(supplied, "buffer");
    const buffer = wrapped ? supplied.buffer : supplied;
    if (!buffer) {
      missing.push(event);
      continue;
    }
    if (supplied.audio_source_sha256 !== event.audio_source_sha256) {
      blocked.push({ ...event, reason: "supplied buffer identity does not match the cleared stem" });
      continue;
    }
    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    source.playbackRate.value = 2 ** ((event.pitch - 60) / 12);
    gain.gain.value = event.velocity / 127;
    source.connect(gain);
    gain.connect(context.destination);
    const at = when + (event.at - start);
    source.start(at);
    source.stop(at + Math.max(0, event.end - event.at));
    scheduled.push(event);
  }
  return { identity: score.identity.contract_sha256, plan, scheduled, missing, blocked };
}

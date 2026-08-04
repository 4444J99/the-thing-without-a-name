/** Provenance-preserving WebAudio adapter for the compiled musical score.
 *
 * This module never synthesises. It schedules only caller-supplied AudioBuffers
 * keyed by declared orchestration stem. The pure event plan is identical for a
 * live AudioContext, OfflineAudioContext, or audio-disabled caller.
 */

import { eventsBetween } from "../engine/score.js";

export function planWebAudio(score, start, end, window = null) {
  return eventsBetween(score, start, end, window)
    .filter((event) => event.type === "note")
    .map((event) => ({
      identity: score.identity.contract_sha256,
      index: event.index,
      at: event.at,
      end: event.end,
      stem: event.stem,
      pitch: event.pitch,
      velocity: event.velocity,
      midi_source_sha256: score.identity.midi_sha256,
    }));
}

function bufferFor(buffers, stem) {
  return typeof buffers?.get === "function" ? buffers.get(stem) : buffers?.[stem];
}

export function scheduleWebAudio(context, score, buffers, start, end, { window = null, when = context.currentTime } = {}) {
  const plan = planWebAudio(score, start, end, window);
  const scheduled = [];
  const missing = [];
  for (const event of plan) {
    const buffer = bufferFor(buffers, event.stem);
    if (!buffer) {
      missing.push(event);
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
  return { identity: score.identity.contract_sha256, plan, scheduled, missing };
}

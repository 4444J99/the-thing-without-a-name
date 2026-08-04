/** Provenance-preserving WebAudio adapter for the compiled musical score.
 *
 * This module never synthesises. It schedules only caller-supplied AudioBuffers
 * keyed by declared orchestration stem and paired with their verified source
 * digest. The pure note-on plan is identical for a live AudioContext,
 * OfflineAudioContext, or audio-disabled caller; it does not invent sustained
 * voice state at an arbitrary seek boundary.
 */

import { eventsBetween } from "../engine/score.js";
import { planRoomRender, roomLayout } from "../engine/room-events.js";

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

/** Renderer-neutral room plan used unchanged with or without an AudioContext. */
export function planRoomWebAudio(bus, registry, layoutId, start, end) {
  return planRoomRender(bus, registry, layoutId, start, end);
}

/** Schedule verified room-event sources into the declared speaker field.
 *
 * `enabled: false` is the accessibility/no-device path: it returns the exact
 * same plan without constructing or touching any WebAudio node. Stereo is the
 * registry's declared fold-down taps, including their source-speaker delays.
 */
export function scheduleRoomWebAudio(
  context,
  bus,
  registry,
  layoutId,
  buffers,
  start,
  end,
  {
    when = null,
    output = "stereo",
    enabled = true,
  } = {},
) {
  if (!new Set(["stereo", "multichannel"]).has(output)) throw new RangeError(`unknown room output ${output}`);
  const plan = planRoomWebAudio(bus, registry, layoutId, start, end);
  if (!enabled) {
    return {
      identity: bus.identity.contract_sha256,
      plan,
      scheduled: [],
      missing: [],
      blocked: [],
      silent: [],
      disabled: plan.events,
    };
  }
  if (!context) throw new TypeError("enabled room audio requires an AudioContext");
  const startWhen = when ?? context.currentTime;

  const layout = roomLayout(registry, layoutId);
  const outputChannels = output === "stereo" ? 2 : layout.speakers.length;
  const scheduled = [];
  const missing = [];
  const blocked = [];
  const silent = [];
  let merger = null;
  for (const event of plan.events) {
    if (!event.audio.role) {
      silent.push(event);
      continue;
    }
    if (!/^[0-9a-f]{64}$/.test(event.audio.source_sha256 ?? "")) {
      blocked.push({ ...event, reason: "room event has no cleared audio-source identity" });
      continue;
    }
    const supplied = bufferFor(buffers, event.audio.role);
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
    if (supplied.audio_source_sha256 !== event.audio.source_sha256) {
      blocked.push({ ...event, reason: "supplied room buffer identity does not match the declared source" });
      continue;
    }
    if (!merger) {
      merger = context.createChannelMerger(outputChannels);
      merger.connect(context.destination);
    }
    const source = context.createBufferSource();
    source.buffer = buffer;
    const taps = event[output];
    for (const tap of taps) {
      const delay = context.createDelay(registry.safety.latency_budget_ms / 1000);
      const gain = context.createGain();
      delay.delayTime.value = tap.delay_ms / 1000;
      gain.gain.value = tap.gain;
      source.connect(delay);
      delay.connect(gain);
      gain.connect(merger, 0, tap.channel);
    }
    const at = startWhen + (event.at - start);
    source.start(at);
    if (event.end !== undefined) source.stop(at + Math.max(0, event.end - event.at));
    scheduled.push(event);
  }
  return {
    identity: bus.identity.contract_sha256,
    plan,
    scheduled,
    missing,
    blocked,
    silent,
    disabled: [],
  };
}

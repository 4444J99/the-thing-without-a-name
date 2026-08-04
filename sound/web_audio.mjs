/** Provenance-preserving WebAudio adapter for the compiled musical score.
 *
 * This module never synthesises. It schedules only caller-supplied AudioBuffers
 * keyed by declared orchestration stem and paired with their verified source
 * digest. The pure note-on plan is identical for a live AudioContext,
 * OfflineAudioContext, or audio-disabled caller; it does not invent sustained
 * voice state at an arbitrary seek boundary.
 */

import { eventsBetween } from "../engine/score.js";
import {
  planRoomRender,
  roomLayout,
  validateRoomBus,
  validateRoomLayouts,
} from "../engine/room-events.js";

const DEFAULT_ROOM_LAYOUT_TIMEOUT_MS = 5000;
const MAX_ROOM_LAYOUT_TIMEOUT_MS = 30000;

/** Bounded network adapter kept outside pure engine/.
 *
 * The timeout owns the upper bound even when a custom fetch implementation
 * ignores AbortSignal. A caller signal can cancel the same request earlier.
 */
export async function loadRoomLayouts(
  url = "sound/room-layout.json",
  {
    timeoutMs = DEFAULT_ROOM_LAYOUT_TIMEOUT_MS,
    signal = null,
    fetchImpl = globalThis.fetch,
  } = {},
) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > MAX_ROOM_LAYOUT_TIMEOUT_MS) {
    throw new RangeError(`room layout timeout must be in (0, ${MAX_ROOM_LAYOUT_TIMEOUT_MS}] ms`);
  }
  if (typeof fetchImpl !== "function") throw new TypeError("room layout loading requires fetch");
  if (signal !== null && (typeof signal !== "object" || typeof signal.addEventListener !== "function")) {
    throw new TypeError("room layout signal must be an AbortSignal");
  }
  if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error("room layout load aborted");

  const controller = new AbortController();
  let callerAbort = null;
  let rejectCallerAbort = null;
  const callerAborted = new Promise((_, reject) => { rejectCallerAbort = reject; });
  if (signal) {
    callerAbort = () => {
      const error = signal.reason instanceof Error ? signal.reason : new Error("room layout load aborted");
      controller.abort(error);
      rejectCallerAbort(error);
    };
    signal.addEventListener("abort", callerAbort, { once: true });
  }

  let timeoutId = null;
  const timeoutError = new Error(`room layouts did not load within ${timeoutMs}ms at ${url}`);
  const timedOut = new Promise((_, reject) => {
    timeoutId = setTimeout(() => {
      controller.abort(timeoutError);
      reject(timeoutError);
    }, timeoutMs);
  });
  const request = Promise.resolve()
    .then(() => fetchImpl(url, { signal: controller.signal }))
    .then((response) => {
      if (!response?.ok) throw new Error(`room layouts ${response?.status ?? "invalid-response"} at ${url}`);
      return response.json();
    });
  try {
    const registry = await Promise.race(signal ? [request, timedOut, callerAborted] : [request, timedOut]);
    return validateRoomLayouts(registry);
  } finally {
    clearTimeout(timeoutId);
    if (signal && callerAbort) signal.removeEventListener("abort", callerAbort);
  }
}

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
  return planRoomRender(validateRoomBus(bus), validateRoomLayouts(registry), layoutId, start, end);
}

function hardLimiter(context, input, ceilingDbfs, destination, remember) {
  if (typeof context.createWaveShaper !== "function") {
    throw new TypeError("room audio requires a WaveShaper ceiling stage");
  }
  const ceiling = 10 ** (ceilingDbfs / 20);
  const curve = new Float32Array(4097);
  for (let index = 0; index < curve.length; index++) {
    const sample = index / (curve.length - 1) * 2 - 1;
    curve[index] = Math.max(-ceiling, Math.min(ceiling, sample));
  }
  const limiter = remember(context.createWaveShaper());
  limiter.curve = curve;
  limiter.oversample = "4x";
  input.connect(limiter);
  limiter.connect(destination);
  return {
    node: limiter,
    receipt: { ceiling_dbfs: ceilingDbfs, ceiling_linear: ceiling },
  };
}

function roomDestination(context, output, outputChannels) {
  const destination = context.destination;
  if (!destination) throw new TypeError("room audio requires an AudioDestinationNode");
  if (output !== "multichannel") return destination;
  const fixedOfflineChannels = destination.maxChannelCount === 0 && destination.channelCount === outputChannels;
  if (
    (!Number.isInteger(destination.maxChannelCount) || destination.maxChannelCount < outputChannels)
    && !fixedOfflineChannels
  ) {
    throw new RangeError(
      `multichannel room output requires ${outputChannels} destination channels; `
      + `only ${destination.maxChannelCount ?? 0} are available`,
    );
  }
  try {
    destination.channelCountMode = "explicit";
    destination.channelInterpretation = "discrete";
    if (destination.channelCount !== outputChannels) destination.channelCount = outputChannels;
  } catch (error) {
    throw new RangeError(`multichannel destination configuration failed: ${error.message}`, { cause: error });
  }
  if (
    destination.channelCount !== outputChannels
    || destination.channelCountMode !== "explicit"
    || destination.channelInterpretation !== "discrete"
  ) {
    throw new RangeError(`multichannel destination did not admit ${outputChannels} discrete channels`);
  }
  return destination;
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
      limiter: null,
      stop: () => false,
      disposed: true,
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
  const graphNodes = [];
  const sources = [];
  let destination = null;
  let merger = null;
  let limiter = null;
  let disposed = false;
  let remainingSources = 0;
  const remember = (node) => {
    graphNodes.push(node);
    return node;
  };
  const disconnectGraph = () => {
    if (disposed) return false;
    disposed = true;
    for (const node of [...graphNodes].reverse()) {
      try {
        if (typeof node.disconnect === "function") node.disconnect();
      } catch {
        // Best-effort teardown must continue across already-disconnected nodes.
      }
    }
    return true;
  };
  const stop = (at = context.currentTime) => {
    if (disposed) return false;
    if (typeof at !== "number" || !Number.isFinite(at) || at < 0) {
      throw new RangeError("room audio stop time must be finite and non-negative");
    }
    for (const source of sources) {
      try {
        source.stop(at);
      } catch {
        // A source that ended or was never admitted cannot block graph teardown.
      }
    }
    disconnectGraph();
    return true;
  };

  try {
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
        destination = roomDestination(context, output, outputChannels);
        merger = remember(context.createChannelMerger(outputChannels));
        const limited = hardLimiter(context, merger, registry.safety.limiter_ceiling_dbfs, destination, remember);
        limiter = limited.receipt;
      }
      const source = remember(context.createBufferSource());
      sources.push(source);
      remainingSources += 1;
      let ended = false;
      const onEnded = () => {
        if (ended) return;
        ended = true;
        remainingSources -= 1;
        if (remainingSources === 0) disconnectGraph();
      };
      if (typeof source.addEventListener === "function") source.addEventListener("ended", onEnded, { once: true });
      else source.onended = onEnded;
      source.buffer = buffer;
      source.playbackRate.value = event.audio.pitch === null ? 1 : 2 ** ((event.audio.pitch - 60) / 12);
      const taps = event[output];
      for (const tap of taps) {
        const gain = remember(context.createGain());
        gain.gain.value = tap.gain;
        if (tap.delay_ms > 0) {
          const delay = remember(context.createDelay(registry.safety.latency_budget_ms / 1000));
          delay.delayTime.value = tap.delay_ms / 1000;
          source.connect(delay);
          delay.connect(gain);
        } else {
          source.connect(gain);
        }
        gain.connect(merger, 0, tap.channel);
      }
      const at = startWhen + (event.at - start);
      source.start(at);
      if (event.end !== undefined) source.stop(at + Math.max(0, event.end - event.at));
      scheduled.push(event);
    }
  } catch (error) {
    stop();
    throw error;
  }
  if (sources.length === 0) disconnectGraph();
  return {
    identity: bus.identity.contract_sha256,
    plan,
    scheduled,
    missing,
    blocked,
    silent,
    disabled: [],
    limiter,
    stop,
    get disposed() { return disposed; },
  };
}

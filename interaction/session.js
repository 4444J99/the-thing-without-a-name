/** Bounded interaction recording and replay. No clock lives here: callers pass
 * the same absolute river time that they pass to the pure engine. */

import {
  MAX_SAMPLES,
  MAX_SECONDS,
  createReceipt,
  inputAt,
  normalizeSample,
  validateReceipt,
} from "./adapter.js";

const sameRiver = (a, b) =>
  (a?.seed >>> 0) === (b?.seed >>> 0) && (a?.stream >>> 0) === (b?.stream >>> 0);

export class InteractionSession {
  constructor(river) {
    this.listeners = new Set();
    this.reset(river);
  }

  reset(river) {
    this.river = { seed: river.seed >>> 0, stream: (river.stream ?? 0) >>> 0 };
    this.live = createReceipt(this.river);
    this.replay = null;
    this.full = false;
    this.notify();
  }

  sync(river, at) {
    const next = { seed: river.seed >>> 0, stream: (river.stream ?? 0) >>> 0 };
    const last = this.live.samples.at(-1);
    // A loaded receipt is deliberately O(1)-seekable in either direction. Only
    // a live recorder must reset before accepting backwards river time.
    if (!sameRiver(this.river, next) || (!this.replay && last && at < last.at)) this.reset(next);
  }

  subscribe(listener) {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  notify() {
    if (!this.listeners) return;
    const snapshot = this.snapshot();
    for (const listener of this.listeners) listener(snapshot);
  }

  record(value) {
    if (this.replay) return false;
    const sample = normalizeSample(value, this.live.samples.length);
    const first = this.live.samples[0];
    if (first && (sample.at - first.at > MAX_SECONDS || this.live.samples.length >= MAX_SAMPLES)) {
      this.full = true;
      this.notify();
      return false;
    }
    const last = this.live.samples.at(-1);
    if (last && sample.at < last.at) throw new RangeError("interaction sample moved backwards in river time");
    if (last && sample.at === last.at) this.live.samples[this.live.samples.length - 1] = sample;
    else this.live.samples.push(sample);
    this.live.window.startedAt = this.live.samples[0].at;
    this.live.window.endedAt = this.live.samples.at(-1).at;
    this.notify();
    return true;
  }

  load(value) {
    this.replay = validateReceipt(value, this.river);
    this.notify();
    return this.replay;
  }

  resumeLive() {
    this.replay = null;
    this.notify();
  }

  at(t) {
    return inputAt(this.replay ?? this.live, t);
  }

  receipt() {
    return validateReceipt(structuredClone(this.replay ?? this.live), this.river);
  }

  snapshot() {
    const receipt = this.replay ?? this.live;
    return {
      river: { ...this.river },
      mode: this.replay ? "replay" : "live",
      samples: receipt.samples.length,
      window: { ...receipt.window },
      full: this.full,
      last: receipt.samples.at(-1) ?? null,
    };
  }
}

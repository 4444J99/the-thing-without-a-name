/** One local interaction session: camera, deterministic fallback, or receipt replay. */

import { NEUTRAL_INPUT, normalizeVisitor } from "./adapter.js";
import { LocalPoseCamera } from "./camera.js";
import { InteractionSession } from "./session.js";

const clamp = (value) => Math.max(0, Math.min(1, Number(value)));

export class InteractionController {
  constructor({ river, video, camera = {}, fallbackHz = 4 }) {
    this.session = new InteractionSession(river);
    this.listeners = new Set();
    this.mode = "off";
    this.message = "Interaction is off. The unmodulated river is complete on its own.";
    this.lastAt = 0;
    this.lastFallbackTick = -1;
    this.fallbackHz = Math.max(2, Math.min(8, fallbackHz));
    this.fallback = { slot: 0, confidence: 1, center: [0.5, 0.5], openness: 0.35, reach: 0 };
    this.camera = new LocalPoseCamera({
      video,
      ...camera,
      onSample: (sample) => this.record(sample),
      onMessage: ({ status, message }) => {
        this.mode = ["active", "no-person", "requesting", "loading", "dropout", "reconnecting"].includes(status)
          ? "camera"
          : status;
        this.message = message;
        this.notify();
      },
    });
    this.session.subscribe(() => this.notify());
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

  snapshot() {
    return {
      mode: this.mode,
      message: this.message,
      camera: this.camera?.phase ?? "stopped",
      retryable: Boolean(
        this.camera && !this.camera.desired && ["denied", "unavailable", "dropout", "error"].includes(this.camera.phase)
      ),
      fallback: structuredClone(this.fallback),
      session: this.session?.snapshot?.() ?? null,
    };
  }

  record(sample) {
    const kept = this.session.record(sample);
    if (!kept && this.session.full) {
      const cameraWasLive = this.mode === "camera";
      this.mode = "limit";
      if (cameraWasLive) {
        // Mark the mode before stop(): its terminal sample re-enters record().
        // That sample is also beyond the bound and must not recurse into stop().
        this.camera.stop(this.lastAt, "The ten-minute interaction receipt is full. Start a new river or save this receipt before continuing.");
      }
      this.mode = "limit";
      this.message = "Interaction stopped at its ten-minute receipt bound.";
    }
    this.notify();
    return kept;
  }

  tick(at, river, { motion = true } = {}) {
    const rewound = at < this.lastAt;
    this.lastAt = at;
    const reset = this.session.sync(river, at);
    if (reset || rewound) {
      this.lastFallbackTick = -1;
      this.camera.resetPollingCursor(at);
    }
    if (this.session.replay || !motion) return;
    if (this.mode === "camera") this.camera.poll(at);
    if (this.mode === "fallback") {
      const tick = Math.floor(at * this.fallbackHz + 1e-9);
      if (tick > this.lastFallbackTick) {
        this.lastFallbackTick = tick;
        this.record({ at, status: "active", source: "keyboard-touch", visitors: [normalizeVisitor(this.fallback)] });
      }
    }
  }

  async startCamera(at = this.lastAt) {
    this.resumeSessionAt(at);
    if (this.mode === "fallback") this.record({ at, status: "stopped", source: "keyboard-touch", visitors: [] });
    this.mode = "camera";
    this.message = "Camera permission is requested only by this explicit action.";
    this.notify();
    return this.camera.start(at);
  }

  startFallback(at = this.lastAt) {
    if (this.mode === "camera") this.camera.stop(at, "Camera stopped before keyboard and touch interaction began.");
    this.resumeSessionAt(at);
    this.mode = "fallback";
    this.message = "Keyboard and touch controls are influencing the same bounded room channels as pose input.";
    this.lastFallbackTick = -1;
    this.record({ at, status: "active", source: "keyboard-touch", visitors: [normalizeVisitor(this.fallback)] });
  }

  setFallback(values, at = this.lastAt) {
    this.fallback = normalizeVisitor({
      ...this.fallback,
      ...values,
      center: values.center ? [clamp(values.center[0]), clamp(values.center[1])] : this.fallback.center,
      confidence: values.confidence === undefined ? this.fallback.confidence : clamp(values.confidence),
      openness: values.openness === undefined ? this.fallback.openness : clamp(values.openness),
      reach: values.reach === undefined ? this.fallback.reach : clamp(values.reach),
    });
    if (this.mode !== "fallback") this.startFallback(at);
    else this.record({ at, status: "active", source: "keyboard-touch", visitors: [this.fallback] });
  }

  stop(at = this.lastAt) {
    if (this.mode === "camera") this.camera.stop(at);
    else if (this.mode === "fallback") this.record({ at, status: "stopped", source: "keyboard-touch", visitors: [] });
    else if (this.mode === "replay") this.resumeSessionAt(at);
    this.mode = "off";
    this.message = "Interaction stopped. The river continues without modulation.";
    this.notify();
  }

  retryCamera(at = this.lastAt) {
    this.resumeSessionAt(at);
    this.mode = "camera";
    return this.camera.retry(at);
  }

  loadReceipt(value) {
    if (this.mode === "camera") this.camera.stop(this.lastAt);
    this.session.load(value);
    this.mode = "replay";
    this.message = "Replaying anonymous derived controls from this river's receipt; no camera is active.";
    this.notify();
  }

  resumeLive() {
    this.resumeSessionAt(this.lastAt);
    this.mode = "off";
    this.message = "Receipt replay ended. Interaction is off.";
    this.notify();
  }

  inputAt(at) {
    return ["camera", "fallback", "replay"].includes(this.mode)
      ? this.session.at(at)
      : NEUTRAL_INPUT;
  }

  receipt() {
    return this.session.receipt();
  }

  resumeSessionAt(at) {
    if (this.session.resumeLive(at)) {
      this.lastFallbackTick = -1;
      this.camera.resetPollingCursor(at);
    }
  }
}

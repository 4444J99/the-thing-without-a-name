/** Explicit, device-local camera capture and pose inference.
 *
 * The MediaPipe runtime, WASM and model are vendored beside this module. Nothing
 * is fetched from a CDN. The caller supplies river time to poll(); the detector's
 * monotonic timestamp is a sequence number, not another application clock.
 */

import { MAX_VISITORS, visitorsFromPoseResult } from "./adapter.js";

export function cameraFailure(error) {
  const name = error?.name ?? "Error";
  if (["NotAllowedError", "PermissionDeniedError"].includes(name)) {
    return { status: "denied", message: "Camera permission was denied. Keyboard and touch controls remain available." };
  }
  if (["NotFoundError", "DevicesNotFoundError", "OverconstrainedError"].includes(name)) {
    return { status: "unavailable", message: "No usable camera was found. Keyboard and touch controls remain available." };
  }
  if (["NotReadableError", "TrackStartError", "AbortError"].includes(name)) {
    return { status: "dropout", message: "The camera became unavailable. Reconnecting locally…" };
  }
  if (["SecurityError", "TypeError"].includes(name)) {
    return { status: "unavailable", message: "Camera capture is unavailable in this browser context." };
  }
  return { status: "error", message: "Local pose input could not start. Keyboard and touch controls remain available." };
}

export async function createLocalPoseDetector({ maxVisitors = MAX_VISITORS } = {}) {
  const { FilesetResolver, PoseLandmarker } = await import("./vendor/mediapipe/vision_bundle.mjs");
  const wasmRoot = new URL("./vendor/mediapipe/wasm", import.meta.url).href.replace(/\/$/, "");
  const model = new URL("./vendor/mediapipe/pose_landmarker_lite.task", import.meta.url).href;
  const files = await FilesetResolver.forVisionTasks(wasmRoot);
  const options = {
    baseOptions: { modelAssetPath: model, delegate: "GPU" },
    runningMode: "VIDEO",
    numPoses: maxVisitors,
    minPoseDetectionConfidence: 0.5,
    minPosePresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
    outputSegmentationMasks: false,
  };
  try {
    return await PoseLandmarker.createFromOptions(files, options);
  } catch {
    return PoseLandmarker.createFromOptions(files, {
      ...options,
      baseOptions: { ...options.baseOptions, delegate: "CPU" },
    });
  }
}

export class LocalPoseCamera {
  constructor({
    video,
    onSample,
    onMessage = () => {},
    mediaDevices = globalThis.navigator?.mediaDevices,
    detectorFactory = createLocalPoseDetector,
    hz = 10,
    maxVisitors = MAX_VISITORS,
  }) {
    this.video = video;
    this.onSample = onSample;
    this.onMessage = onMessage;
    this.mediaDevices = mediaDevices;
    this.detectorFactory = detectorFactory;
    this.hz = Math.max(2, Math.min(15, hz));
    this.maxVisitors = maxVisitors;
    this.phase = "stopped";
    this.desired = false;
    this.lastAt = 0;
    this.lastTick = -1;
    this.sequence = 0;
    this.reconnects = 0;
    this.retryAt = null;
    this.pending = null;
    this.stream = null;
    this.detector = null;
    this.generation = 0;
  }

  emit(at, status, visitors = [], message = null) {
    this.phase = status;
    this.onSample({ at, status, source: "camera", visitors });
    if (message) this.onMessage({ status, message });
  }

  async start(at, { reconnect = false } = {}) {
    this.lastAt = at;
    this.desired = true;
    if (this.pending) return this.pending;
    const generation = ++this.generation;
    this.pending = this.startOnce(at, reconnect, generation).finally(() => { this.pending = null; });
    return this.pending;
  }

  async startOnce(at, reconnect, generation) {
    this.disposeMedia();
    if (!this.mediaDevices?.getUserMedia) {
      this.desired = false;
      this.emit(at, "unavailable", [], "This device does not expose a camera API. Keyboard and touch controls remain available.");
      return false;
    }
    this.emit(at, reconnect ? "reconnecting" : "requesting", [], reconnect ? "Reconnecting to the camera locally…" : "Waiting for camera permission…");
    let stream;
    try {
      stream = await this.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: "user",
          width: { ideal: 640 },
          height: { ideal: 480 },
          frameRate: { ideal: this.hz, max: 15 },
        },
      });
    } catch (error) {
      if (!this.desired || generation !== this.generation) return false;
      const failure = cameraFailure(error);
      this.desired = failure.status === "dropout";
      this.emit(at, failure.status, [], failure.message);
      if (failure.status === "dropout") this.scheduleReconnect(at);
      return false;
    }

    if (!this.desired || generation !== this.generation) {
      for (const track of stream.getTracks?.() ?? []) track.stop?.();
      return false;
    }

    this.stream = stream;
    this.video.srcObject = stream;
    this.video.muted = true;
    this.video.playsInline = true;
    for (const track of stream.getVideoTracks?.() ?? []) {
      track.addEventListener?.("ended", () => this.dropout(this.lastAt));
      track.addEventListener?.("mute", () => this.dropout(this.lastAt));
    }
    try {
      await this.video.play?.();
      if (!this.desired || generation !== this.generation) {
        this.disposeMedia();
        return false;
      }
      this.emit(at, "loading", [], "Camera stays on this device. Loading the local pose model…");
      const detector = await this.detectorFactory({ maxVisitors: this.maxVisitors });
      if (!this.desired || generation !== this.generation) {
        detector.close?.();
        this.disposeMedia();
        return false;
      }
      this.detector = detector;
    } catch (error) {
      if (!this.desired || generation !== this.generation) return false;
      const failure = cameraFailure(error);
      this.desired = false;
      this.disposeMedia();
      this.emit(at, failure.status === "dropout" ? "error" : failure.status, [], failure.message);
      return false;
    }
    this.reconnects = 0;
    this.retryAt = null;
    this.lastTick = -1;
    this.emit(at, "no-person", [], "Camera active locally; step into view, or use the fallback controls.");
    return true;
  }

  poll(at) {
    this.lastAt = at;
    if (this.desired && this.phase === "dropout" && !this.pending && this.retryAt !== null && at >= this.retryAt) {
      void this.start(at, { reconnect: true });
      return;
    }
    if (!this.detector || !["active", "no-person"].includes(this.phase)) return;
    if ((this.video.readyState ?? 0) < 2) {
      this.dropout(at);
      return;
    }
    const tick = Math.floor(at * this.hz + 1e-9);
    if (tick <= this.lastTick) return;
    this.lastTick = tick;
    const detectorTimestamp = Math.round((this.sequence++ * 1000) / this.hz);
    try {
      const result = this.detector.detectForVideo(this.video, detectorTimestamp);
      const visitors = visitorsFromPoseResult(result, { mirror: true });
      if (visitors.length) {
        this.emit(at, "active", visitors, `${visitors.length} ${visitors.length === 1 ? "visitor" : "visitors"}; only derived room controls are retained.`);
      } else {
        this.emit(at, "no-person", [], "Camera active locally; no body is currently influencing the room.");
      }
    } catch {
      this.dropout(at);
    }
  }

  scheduleReconnect(at) {
    const delays = [1, 2, 4];
    if (this.reconnects >= delays.length) {
      this.desired = false;
      this.retryAt = null;
      this.onMessage({ status: "dropout", message: "Camera reconnect stopped after three attempts. Retry explicitly or use the fallback controls." });
      return;
    }
    this.retryAt = at + delays[this.reconnects++];
  }

  dropout(at) {
    if (!this.desired || this.phase === "dropout") return;
    this.generation++;
    this.disposeMedia();
    this.emit(at, "dropout", [], "The camera was lost. Reconnecting locally; fallback controls remain available.");
    this.scheduleReconnect(at);
  }

  async retry(at) {
    this.reconnects = 0;
    this.retryAt = null;
    return this.start(at, { reconnect: true });
  }

  stop(at, message = "Camera stopped. No visitor image or raw landmark was retained.") {
    this.lastAt = at;
    this.desired = false;
    this.generation++;
    this.retryAt = null;
    this.disposeMedia();
    this.emit(at, "stopped", [], message);
  }

  disposeMedia() {
    try { this.detector?.close?.(); } catch { /* the privacy boundary still closes */ }
    this.detector = null;
    for (const track of this.stream?.getTracks?.() ?? []) track.stop?.();
    this.stream = null;
    if (this.video) this.video.srcObject = null;
  }
}

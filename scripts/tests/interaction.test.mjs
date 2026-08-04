#!/usr/bin/env node

import assert from "node:assert/strict";

import {
  InteractionRenderer,
  PRIVACY,
  aggregateVisitors,
  createReceipt,
  featuresFromLandmarks,
  inputAt,
  modulateFrame,
  validateReceipt,
  visitorsFromPoseResult,
} from "../../interaction/adapter.js";
import { LocalPoseCamera, cameraFailure } from "../../interaction/camera.js";
import { InteractionController } from "../../interaction/controller.js";
import { InteractionSession } from "../../interaction/session.js";

let passed = 0;
async function test(name, fn) {
  await fn();
  passed++;
  process.stdout.write(`  ok ${passed} - ${name}\n`);
}

const river = Object.freeze({ seed: 0x10203040, stream: 7 });
const visitor = (slot = 0, x = 0.25) => ({
  slot,
  confidence: 0.8,
  center: [x, 0.4],
  openness: 0.6,
  reach: 0.7,
});
const sample = (at, status = "active", visitors = [visitor()], source = "fixture") => ({
  at, status, source, visitors,
});

await test("receipts validate a strict bounded privacy contract", () => {
  const receipt = createReceipt(river);
  receipt.samples = [sample(10), sample(10.5, "no-person", [])];
  receipt.window = { startedAt: 10, endedAt: 10.5 };
  assert.deepEqual(validateReceipt(receipt, river), receipt);
  assert.deepEqual(receipt.privacy, PRIVACY);

  const raw = structuredClone(receipt);
  raw.samples[0].landmarks = [{ x: 0, y: 0 }];
  assert.throws(() => validateReceipt(raw), /keys must be exactly/);

  const wrongRiver = { ...river, seed: river.seed + 1 };
  assert.throws(() => validateReceipt(receipt, wrongRiver), /different river/);

  const unbounded = structuredClone(receipt);
  unbounded.samples[1].at = 611;
  unbounded.window.endedAt = 611;
  assert.throws(() => validateReceipt(unbounded), /exceeds 600 seconds/);

  const indexedVisitor = structuredClone(receipt);
  indexedVisitor.samples[0].visitors = [visitor(0), { ...visitor(1), confidence: 2 }];
  assert.throws(() => validateReceipt(indexedVisitor), /visitor 1\.confidence/);
});

await test("replay is deterministic, seekable, and fades denial or dropout", () => {
  const receipt = createReceipt(river);
  receipt.samples = [
    sample(2),
    sample(2.5, "dropout", []),
    sample(4, "active", [visitor(0, 0.8)]),
  ];
  receipt.window = { startedAt: 2, endedAt: 4 };
  const valid = validateReceipt(receipt);
  const first = inputAt(valid, 4.1);
  inputAt(valid, 2.75);
  assert.deepEqual(inputAt(valid, 4.1), first);
  assert.equal(inputAt(valid, 1).strength, 0);
  assert(inputAt(valid, 2.75).strength > 0);
  assert.equal(inputAt(valid, 3.9).strength, 0);
  assert.equal(first.center[0], 0.8);
});

await test("repeated no-person polls cannot extend a departed visitor's fade", () => {
  const receipt = createReceipt(river);
  receipt.samples = [
    sample(2),
    sample(2.1, "no-person", []),
    sample(2.4, "no-person", []),
    sample(2.7, "no-person", []),
  ];
  receipt.window = { startedAt: 2, endedAt: 2.7 };
  const valid = validateReceipt(receipt);
  assert(inputAt(valid, 2.5).strength > 0);
  assert.equal(inputAt(valid, 2.81).strength, 0);
});

await test("multiple visitors aggregate independently of detector order", () => {
  const left = visitor(0, 0.2);
  const right = { ...visitor(1, 0.8), confidence: 0.5, openness: 0.2 };
  assert.deepEqual(aggregateVisitors([left, right]), aggregateVisitors([right, left]));
  assert.equal(aggregateVisitors([left, right]).count, 2);

  const pose = (x) => Array.from({ length: 33 }, (_, index) => ({
    x: x + (index % 2 ? 0.02 : -0.02),
    y: 0.2 + index / 100,
    visibility: 0.95,
    presence: 0.9,
  }));
  const visitors = visitorsFromPoseResult({ landmarks: [pose(0.75), pose(0.25)] }, { mirror: false });
  assert.equal(visitors.length, 2);
  assert.deepEqual(visitors.map(({ slot }) => slot), [0, 1]);
  assert(visitors[0].center[0] < visitors[1].center[0]);
});

await test("raw landmarks reduce to anonymous features and do not escape", () => {
  const landmarks = Array.from({ length: 33 }, (_, index) => ({
    x: 0.25 + (index % 3) * 0.1,
    y: 0.1 + index * 0.015,
    visibility: 0.9,
    presence: 0.85,
    z: index / 33,
  }));
  const feature = featuresFromLandmarks(landmarks, { mirror: true });
  assert.deepEqual(Object.keys(feature).sort(), ["center", "confidence", "openness", "reach", "slot"]);
  assert.equal(feature.center[0], 0.65);
  assert(!JSON.stringify(feature).includes('"z"'));
  assert.equal(featuresFromLandmarks(landmarks.slice(0, 3)), null);
});

await test("embodied modulation is bounded and neutral input is exact identity", () => {
  const state = {
    t: 12,
    cut: "bands",
    divergence: 0.2,
    azimuth: 0.1,
    elevation: -0.1,
    spread: 0.25,
    projK: 0.3,
  };
  const draw = { seed: river.seed, matteK: 0.1 };
  const neutral = modulateFrame(state, draw, inputAt(createReceipt(river), 12));
  assert.strictEqual(neutral.state, state);
  assert.strictEqual(neutral.draw, draw);

  const embodied = {
    status: "active", source: "fixture", count: 4, confidence: 1,
    center: [1, 0], openness: 1, reach: 1, dwell: 9, freshness: 1, strength: 1,
  };
  const changed = modulateFrame(state, draw, embodied);
  assert.deepEqual(modulateFrame(state, draw, embodied), changed);
  assert(changed.state.divergence <= 1.15);
  assert(changed.state.azimuth <= 1.35);
  assert(changed.state.elevation <= 0.7);
  assert(changed.state.spread <= 1);
  assert(changed.state.projK <= 1);
  assert(changed.draw.matteK <= 1);
  assert.strictEqual(modulateFrame(state, draw, embodied, { reducedMotion: true }).state, state);
  assert.strictEqual(modulateFrame({ ...state, cut: "black" }, draw, embodied).draw, draw);
});

await test("renderer facade leaves the canonical engine arguments untouched when off", () => {
  const calls = [];
  const base = {
    gl: {}, canvas: {}, corpus: {},
    draw(cast, state, draw) { calls.push({ cast, state, draw }); return { planes: cast.length, missing: 0 }; },
  };
  const state = { t: 1, cut: "bands", divergence: 0, azimuth: 0, elevation: 0, spread: 0, projK: 0 };
  const draw = { seed: river.seed };
  const cast = [{ id: "one" }];
  const wrapper = new InteractionRenderer(base, () => inputAt(createReceipt(river), 1));
  assert.deepEqual(wrapper.draw(cast, state, draw), { planes: 1, missing: 0, interaction: wrapper.last });
  assert.strictEqual(calls[0].cast, cast);
  assert.strictEqual(calls[0].state, state);
  assert.strictEqual(calls[0].draw, draw);
});

await test("sessions reset on river or backwards time and replay only matching rivers", () => {
  const session = new InteractionSession(river);
  session.record(sample(8));
  session.record(sample(8.25, "no-person", []));
  const receipt = session.receipt();
  session.sync(river, 7);
  assert.equal(session.receipt().samples.length, 0);
  session.record(sample(9));
  session.load(receipt);
  assert.equal(session.at(8).count, 1);
  session.sync(river, 7);
  assert.equal(session.at(8).count, 1);
  assert.equal(session.resumeLive(7), true);
  assert.equal(session.at(8).count, 0);
  assert.doesNotThrow(() => session.record(sample(7)));
  session.sync({ seed: 99, stream: 0 }, 0);
  assert.deepEqual(session.snapshot().river, { seed: 99, stream: 0 });
  assert.throws(() => session.load(receipt), /different river/);
});

class FakeTrack extends EventTarget {
  constructor() { super(); this.stopped = false; }
  stop() { this.stopped = true; }
}

function fakeStream(track = new FakeTrack()) {
  return {
    track,
    getVideoTracks: () => [track],
    getTracks: () => [track],
  };
}

function fakeVideo() {
  return { srcObject: null, muted: false, playsInline: false, readyState: 4, play: async () => {} };
}

await test("camera denial and missing hardware fail closed to accessible fallbacks", async () => {
  assert.equal(cameraFailure({ name: "NotAllowedError" }).status, "denied");
  assert.equal(cameraFailure({ name: "NotFoundError" }).status, "unavailable");
  const denied = [];
  const camera = new LocalPoseCamera({
    video: fakeVideo(),
    onSample: (value) => denied.push(value),
    mediaDevices: { getUserMedia: async () => { const error = new Error("no"); error.name = "NotAllowedError"; throw error; } },
    detectorFactory: async () => assert.fail("detector must not load after denial"),
  });
  assert.equal(await camera.start(1), false);
  assert.equal(camera.phase, "denied");
  assert.equal(camera.desired, false);
  assert.equal(denied.at(-1).visitors.length, 0);

  const absent = new LocalPoseCamera({ video: fakeVideo(), onSample: () => {}, mediaDevices: {} });
  assert.equal(await absent.start(2), false);
  assert.equal(absent.phase, "unavailable");
});

await test("no-person, re-entry, device loss, and local reconnect are explicit", async () => {
  const streams = [fakeStream(), fakeStream()];
  let mediaCalls = 0;
  const detections = [
    { landmarks: [] },
    { landmarks: [Array.from({ length: 33 }, (_, index) => ({ x: 0.3, y: 0.1 + index / 100, visibility: 1 }))] },
  ];
  const samples = [];
  const camera = new LocalPoseCamera({
    video: fakeVideo(),
    hz: 10,
    onSample: (value) => samples.push(value),
    mediaDevices: { getUserMedia: async () => streams[mediaCalls++] },
    detectorFactory: async () => ({ detectForVideo: () => detections.shift() ?? { landmarks: [] }, close() {} }),
  });
  assert.equal(await camera.start(3), true);
  camera.poll(3.1);
  camera.poll(3.2);
  assert(samples.some(({ status }) => status === "no-person"));
  assert(samples.some(({ status }) => status === "active"));
  streams[0].track.dispatchEvent(new Event("ended"));
  assert.equal(camera.phase, "dropout");
  assert.equal(camera.retryAt, 4.2);
  camera.poll(4.2);
  await camera.pending;
  assert.equal(mediaCalls, 2);
  assert.equal(camera.phase, "no-person");
  assert.equal(streams[0].track.stopped, true);
  camera.stop(5);
  assert.equal(streams[1].track.stopped, true);
});

await test("camera polling resumes immediately after river time rewinds", async () => {
  let detections = 0;
  const controller = new InteractionController({
    river,
    video: fakeVideo(),
    camera: {
      hz: 10,
      mediaDevices: { getUserMedia: async () => fakeStream() },
      detectorFactory: async () => ({
        detectForVideo() { detections++; return { landmarks: [] }; },
        close() {},
      }),
    },
  });
  assert.equal(await controller.startCamera(10), true);
  controller.tick(10.1, river);
  assert.equal(detections, 1);
  controller.tick(2, river);
  assert.equal(detections, 2);
  assert.equal(controller.receipt().samples[0].at, 2);
  controller.stop(2.1);
});

await test("an explicit stop wins races with pending permission or model work", async () => {
  const source = fakeStream();
  let finishDetector;
  let detectorClosed = false;
  const camera = new LocalPoseCamera({
    video: fakeVideo(),
    onSample: () => {},
    mediaDevices: { getUserMedia: async () => source },
    detectorFactory: () => new Promise((resolve) => { finishDetector = resolve; }),
  });
  const starting = camera.start(30);
  while (camera.phase !== "loading") await Promise.resolve();
  camera.stop(30.1);
  finishDetector({ detectForVideo() {}, close() { detectorClosed = true; } });
  assert.equal(await starting, false);
  assert.equal(camera.phase, "stopped");
  assert.equal(source.track.stopped, true);
  assert.equal(detectorClosed, true);
});

await test("an explicit restart supersedes a stopped permission request", async () => {
  const streams = [fakeStream(), fakeStream()];
  const resolvePermission = [];
  let mediaCalls = 0;
  const camera = new LocalPoseCamera({
    video: fakeVideo(),
    onSample: () => {},
    mediaDevices: {
      getUserMedia: () => {
        const index = mediaCalls++;
        return new Promise((resolve) => { resolvePermission[index] = () => resolve(streams[index]); });
      },
    },
    detectorFactory: async () => ({ detectForVideo() { return { landmarks: [] }; }, close() {} }),
  });

  const first = camera.start(40);
  assert.equal(mediaCalls, 1);
  camera.stop(40.1);
  const second = camera.start(40.2);
  const secondPending = camera.pending;
  assert.equal(mediaCalls, 2);

  resolvePermission[0]();
  assert.equal(await first, false);
  assert.strictEqual(camera.pending, secondPending);
  assert.equal(streams[0].track.stopped, true);

  resolvePermission[1]();
  assert.equal(await second, true);
  assert.equal(camera.phase, "no-person");
  assert.strictEqual(camera.stream, streams[1]);
  camera.stop(40.3);
});

await test("keyboard/touch fallback records and replays the same derived controls", () => {
  const controller = new InteractionController({ river, video: fakeVideo(), camera: { mediaDevices: {} } });
  controller.startFallback(20);
  controller.setFallback({ center: [0.9, 0.1], openness: 0.8, reach: 1 }, 20.25);
  controller.tick(20.5, river);
  const expected = controller.inputAt(20.5);
  const receipt = controller.receipt();
  assert.equal(expected.source, "keyboard-touch");
  assert.equal(expected.center[0], 0.9);
  assert(receipt.samples.length >= 2);

  const replay = new InteractionController({ river, video: fakeVideo(), camera: { mediaDevices: {} } });
  replay.loadReceipt(receipt);
  assert.deepEqual(replay.inputAt(20.5), expected);
  replay.stop(20.5);
  assert.equal(replay.inputAt(20.5).strength, 0);
});

await test("camera retry exits replay and discards future live history after a seek", async () => {
  const controller = new InteractionController({ river, video: fakeVideo(), camera: { mediaDevices: {} } });
  controller.startFallback(20);
  const receipt = controller.receipt();
  controller.loadReceipt(receipt);
  controller.tick(5, river);
  assert.equal(controller.session.snapshot().mode, "replay");
  assert.equal(await controller.retryCamera(5), false);
  assert.equal(controller.session.snapshot().mode, "live");
  assert.deepEqual(controller.receipt().samples.map(({ at, status }) => ({ at, status })), [
    { at: 5, status: "unavailable" },
  ]);
});

await test("the receipt duration bound stops influence without recursive shutdown", () => {
  const controller = new InteractionController({ river, video: fakeVideo(), camera: { mediaDevices: {} } });
  controller.startFallback(0);
  controller.setFallback({ reach: 1 }, 601);
  assert.equal(controller.mode, "limit");
  assert.equal(controller.session.full, true);
  assert.equal(controller.inputAt(601).strength, 0);
});

process.stdout.write(`interaction: ${passed} deterministic checks passed\n`);

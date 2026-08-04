# Room-event contracts

Danse uses one immutable event vocabulary for the live browser, stereo fallback,
offline renderer, and a declared multichannel speaker field. This is fixture and
reference infrastructure for issue #11. It is not evidence that apartment
recordings are cleared, that a venue accepted a layout, or that hardware was
installed or calibrated.

## Contract boundary

`engine/room-events.js` compiles `danse.room.events.v1` once per passage from the
validated musical score. Every event carries:

- an explicit type (`passage.start`, `movement.start`, `plane.assembly`,
  `score.cue`, `plane.recast`, or `score.note`);
- absolute river time and immutable passage identity;
- a listener-relative normalized-room vector, depth, and bounded intensity;
- the originating score row and score/MIDI contract digests; and
- an audio role plus exact source digest, or `null` when no cleared bytes exist;
  score notes also retain their authored MIDI pitch for identical playback rate.

The bus has one-second event-start lookup buckets. `roomEventsBetween()` and its
Python twin query only intersecting buckets and retain the half-open
`[start, end)` START semantics of `engine/score.js`: a note already active at a
seek boundary is not invented as a new note-on. Voice restoration would require
a separate authored allocation/offset contract.

`sound/room-layout.json` is a digest-bound registry containing a portable stereo
fallback and a normalized four-channel reference simulation. It makes no venue
or equipment claim. Each speaker has a stable channel and position; every
multichannel tap has an explicit stereo fold-down coefficient. Routing uses
the registry listener as the absolute anchor for each relative event vector,
equal-energy inverse-distance weights, propagation-delay differences, the
declared latency budget, a per-event gain ceiling, and a downstream limiter
ceiling. A layout with duplicate channels, an invalid matrix, stale identity, or
an exceeded latency budget fails before scheduling.

## Consumers

- `scheduleRoomWebAudio()` schedules the same renderer-neutral plan to stereo or
  multichannel WebAudio nodes. It revalidates both contract digests at this
  byte-owning boundary and routes the merger through the declared hard sample
  ceiling. Zero-delay taps bypass `DelayNode` creation; multichannel output
  configures the exact discrete destination channel count only after a source is
  admitted (including a fixed-channel offline destination). Every admitted graph
  returns an idempotent `stop()` handle and disconnects automatically after its
  final source ends. With `enabled: false` it returns that exact plan without
  touching an `AudioContext`.
- `loadRoomLayouts()` lives in the WebAudio adapter rather than pure `engine/`.
  It has a five-second default and a hard 30-second maximum, races even a
  non-cooperative fetch, and accepts caller cancellation.
- `sound/room_render.py` emits offline stereo or multichannel render
  instructions from the buses in `sound/control.mjs` output. It binds every bus
  to the control seed, stream, score/MIDI identity, and a contiguous passage
  sequence that covers the complete capture interval.
- `sound/score.py` securely loads the registry named by the control receipt,
  then uses `validate_room_event_control()` when its legacy byte renderer only
  needs identity validation. `room_event_plan()` is the explicit path that also
  computes and returns speaker taps.

Every byte-owning path fails closed. A role without `source_sha256` is blocked;
a supplied buffer must carry the same digest. The committed fixture therefore
plans spatial behavior but cannot emit score-driven sound. Private recordings,
grain payloads, and generated renders remain outside git.

```bash
# Metadata-only control receipt with one immutable bus per intersecting passage.
node sound/control.mjs --rate 0 --score music/score.json > /tmp/danse-control.json

# Renderer instructions only; no recordings or hardware are opened.
python3 sound/room_render.py /tmp/danse-control.json --layout stereo --output stereo
python3 sound/room_render.py /tmp/danse-control.json \
  --layout reference-quad --output multichannel

python3 scripts/tests/room-events.test.py
```

## Diagnostic calibration

`calibrationBus()` emits one direct-to-speaker `calibration.impulse` event per
declared channel. Those events are marked `diagnostic-only`, have no recording
digest, and are never part of the artwork. A later venue-owned harness may
compile a distinct diagnostic bus bound to an exact admitted impulse digest;
this repository records only the deterministic test scene and expected route,
not a claim that it played in a physical room.

## Archived predecessor disposition

The predecessor is preserved at exact Limen commit
`a232f2d7160e213802580e2d532a0d2d9ac65727` under the archive receipt
`docs/continuations/danse-predecessor-experiments-20260802.md`; issue #3 forbids
merging it wholesale.

| Archived idea | Disposition here |
|---|---|
| Plane position, signed depth, intensity, and stereo placement | Ported into typed normalized event fields and deterministic speaker taps. |
| Source selection tied to the same 32-bit visual RNG | Ported for event positions only; recording selection remains digest-gated. |
| Bounded transient density, limiting, and distance delay | Ported as validated safety declarations; authored score starts are never decimated. |
| Mutable voice timers, `setTimeout` bed loops, previous-frame maps, and per-second counters | Rejected; immutable passage buses and lookup buckets supersede accumulated playback state. |
| Oscillator unlock/test tones | Rejected for artwork paths; diagnostic impulses are separately typed and never presented as source material. |
| Public-bank URLs and implicit local recording availability | Rejected; exact declared digests are required before any buffer node is created. |
| Fixed room dimensions, projector/speaker hardware, and successful installation behavior | Deferred to issue #14 and venue evidence; the tracked layouts are simulations only. |

## Remaining external gates

Completion of the physical predicate still requires source bytes to be selected
and cleared, a venue-approved speaker map, measured device latency/limiting, and a
documented human-observed plane/cue room test. None is inferred from a green
fixture suite.

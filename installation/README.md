# Danse installation reference contract

This directory is the machine-readable, reference-only installation layer for
issue #14. It proves that the same projector camera, score clock, room-event bus,
interaction adapter, and recovery policy can be carried into a declared room. It
does **not** prove that a room, projector, surface, speaker, host, cable, power
drop, or venue has been approved, installed, measured, or observed.

## Contract map

| File | Authority |
|---|---|
| `digital-twin.json` + schema | Deterministic reference geometry, source digests, logical outputs, speaker order, thresholds, and recovery policy. |
| `contract.py` | Strict byte, identity, geometry, calibration, venue, hardware, runtime, wall-plug, and restore validator. |
| `gates.json` | Current truth: every physical gate is blocked and issue #14 cannot close. |
| `evidence.schema.json` | Shape a venue-owned external evidence receipt must have. No completed evidence is committed here. |
| `runtime.py` | On-demand foreground supervisor for one exact venue-approved launcher in a canonical release. |
| `archive-disposition.json` | Claim-by-claim port/reject/defer record for the non-authoritative Limen proposal. |
| `OPERATIONS.md` | Setup, calibration, operation, recovery, strike, transport, restore, troubleshooting, and conservation procedure. |

The contract digest is computed over canonical JSON with
`identity.contract_sha256` blank. Every source path is repository-relative,
regular, non-symlinked, and bound to its raw SHA-256. A changed score, camera,
program, speaker registry, interaction adapter, or probe therefore makes the
twin stale instead of silently changing the installation.

## Reference geometry and output specification

- Coordinates use the engine’s normalized room with two metres per unit. The
  2017 picture plane is 4.0 m × 3.0 m in this **simulation**, not a venue claim.
- The projector eye is `[0, 0, 2.4]`, its vertical field of view is derived as
  `2 atan(0.75 / 2.4)`, and its aspect is exactly 4:3. These are the same values
  exported by `engine/room.js`.
- Two logical outputs receive the same complete projector view and the same
  frame ticket. Their reference surfaces sit at normalized z = ±0.5. Both
  surfaces and all physical output assignments remain venue-unassigned.
- The reference edge rule is a hard boundary with zero overlap and no blend.
  It is an artistic/reference policy inherited deliberately from the archive,
  not evidence that a lens or surface satisfies it.
- The output sync threshold is 16.667 ms at 60 fps. Hardware sync is unproven
  until it is measured at the venue.

`frame_ticket()` is pure in `(spec, seed, stream, frame)`. Every output receives
the same absolute score time `frame / fps`; seeking, restarting, or generating
tickets out of order does not introduce a second clock.

## Speaker and calibration specification

The audio field is the digest-bound `reference-quad` layout in
`sound/room-layout.json`, in channel order:

1. front-left;
2. front-right;
3. rear-left;
4. rear-right.

Those names are simulation roles. Venue evidence must bind each role to a unique
verified asset and retain the room-layout 25 ms latency budget and −1 dBFS
limiter ceiling. Diagnostic impulses are typed calibration events and are never
artwork audio.

The deterministic calibration plan orders release integrity, room safety,
surface geometry, projector registration, output synchronization, speaker
routing, audiovisual synchronization, a human-visible plane/cue test, and
runtime recovery. Admission thresholds are:

| Measurement | Maximum |
|---|---:|
| projector registration error | 2 px |
| inter-output skew | 16.667 ms |
| audiovisual skew | 25 ms |
| speaker route errors | 0 |
| limiter ceiling | −1 dBFS |

These are candidate acceptance thresholds. A venue must approve the exact twin
before its measurements can satisfy them.

## Runtime boundary

The runtime is one foreground process supervising one exact argument vector from
an external venue receipt. It:

- refuses developer checkouts, Git metadata, symlinks, absolute executables,
  stale release manifests, unverified hardware, failed calibration, non-loopback
  health URLs, and unapproved launchers; health probes use numeric loopback
  addresses directly and bypass ambient proxy settings;
- passes the approved river seed, stream, epoch, output IDs, evidence ID, and
  contract digest through environment variables;
- binds the canonical digest of the admitted evidence into the child environment
  and emits append-only JSONL health/restart telemetry without local paths or
  credentials;
- admits at most three restarts in a five-minute window with fixed backoff; and
- exits when the budget is exhausted instead of looping forever.

It never installs or generates a LaunchAgent, LaunchDaemon, cron entry,
systemd user unit, plist, or other host service. A venue may approve its own
power-on/session launcher, but that external mechanism and exact command must be
captured in evidence. Do not install one on this Mac.

```bash
# Reference contracts only; this is expected to pass anywhere.
python3 scripts/check-installation.py
python3 scripts/check-installation.py --emit calibration
python3 scripts/check-installation.py --emit frame --seed 20170620 --stream 0 --frame 120

# Expected to fail until external evidence and a canonical release exist.
python3 scripts/check-installation.py --phase complete

# Venue-only admission and foreground execution after evidence exists.
python3 installation/runtime.py --check \
  --evidence /external/evidence.json --release-root /external/release
python3 installation/runtime.py --run \
  --evidence /external/evidence.json --release-root /external/release \
  --telemetry /external/recovery-session.jsonl
```

## Current blockers

The tracked gate ledger intentionally records all eight physical gates as
blocked. Completion still requires venue approval; exact hardware, cabling,
power, ventilation, and safety receipts; projector/speaker/AV measurements; the
human-visible plane/cue test; launcher approval; three distinct human-observed
wall-plug recoveries; and setup/strike/clean-restore evidence. A green reference
suite cannot close issue #14.

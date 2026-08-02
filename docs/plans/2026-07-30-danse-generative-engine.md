# danse — the 2017 transmutation, rebuilt as a machine

> Historical design record. This plan was written while Danse lived under
> `apps/danse` in `organvm/limen`. The canonical implementation now lives at the
> root of `organvm/the-thing-without-a-name`; [`LINEAGE.json`](../../LINEAGE.json)
> binds this preserved plan to its source commits and migration receipt.

## Context

On **20 June 2017** you photographed a ballerina — your then-girlfriend — for one afternoon in
your apartment, in a room hung with framed classic-horror movie posters. **161 frames.** No video.
On **25 July 2017** you cut three of those photographs into vertical strips and interleaved them by
hand with strips of the wall behind her, keeping the floor line continuous so the pieces read as
one impossible room. Then it sat for nine years.

You want it to move: *"a space that is constantly changing, constantly new, always different,
always selecting different parts of the ballerina, always selecting different photographs."*
Writing to Chris — a peer you share work with, not a client or a director — you named the missing
dimension yourself: **screens at different angles and depths with different transparencies,
projected upon**, the way that museum room worked. And you named the path in the same message:
*"ideally in a real space, but can be built digitally first to prove the concept."* In follow-up dialogue with Chris, you declared the final evolution: (a) user interaction, and (b) sound from the space that each generation of panel/slice will trigger between the XY axes of the background. That message is
the brief; this plan is its execution.

**The intended outcome is one engine with five faces:** a film for ScreenDance Miami, a living web
page, an Instagram presence, something strangers can put themselves into, and a costed pitch for
the physical room. The engine is the work; everything else is a render target.

### What the research established

| Fact | Source |
|---|---|
| **Every fact about the call** — deadline, specs, what the call never states | [`submission/screendance-2027.yaml`](../../submission/screendance-2027.yaml), checked by `check.py` |
| Panel convenes end Sept · notification end Oct · **festival 20–24 Jan 2027** | call text |
| Venues include **New World Center's 7,000 sq ft projection wall at Soundscape Park** and **PAMM** | 2024–26 programs |
| Pioneer Winter weights *"choreography, performance, cinematography, editing"* | interview |
| **A 5-min 4K/60 render takes ~15–30 min**, not overnight | measured on this Mac |
| Corpus: **161 raw @ 3264×2448**, one session; 3 transmutations @ 750×750 | Photos.app |

### The three decisions that make this work

1. **Projective texturing, not per-plane UVs.** Every photo is pre-registered to one room frame;
   every fragment samples through a *shared room projector* matrix. Two planes at different depths
   and angles then place the floor line and wall line on the **same screen-space lines** —
   automatically. The continuity rule your hand-cuts obey becomes a property of how pixels are
   fetched, not a constraint the generator has to remember.

   *Binds:* `check-danse.py` asserts the arithmetic half — the score partitions the frame with no
   gaps, no overlaps, nothing outside the frustum, since a hole in the partition is a hole in the
   room. The GPU half is [`probe.html`](../../probe.html), whose self-test sweeps `projK`
   across all 256 tiles at the home position and requires **max Δ 0/255**.

2. **The flattening is the CAMERA, not `projK`.** *Corrected 2026-07-30 — this decision originally
   read "`projK` is the film's spine", and the probe disproved it.* `projK` is real: `0` makes a
   plane a **window** onto whatever the room casts where it now is, `1` makes it a **carried
   picture** holding its assigned crop. But it is not the reveal. With the arrangement fully
   exploded, standing at the projector *still* returns the flat composite — projective texturing
   looks painted-on from the light's own viewpoint. So the reveal is a **move**, not a uniform
   sweep, and the arrangement can be built up invisibly while the camera is on-axis.

   *Binds:* `check-danse.py` asserts `divergence(seed, 0) === 0` exactly across seeds, and that the
   same holds again one `PERIOD` later — the 2017 composite is a recurring event in the animation,
   not its first frame. [`verify.html`](../../verify.html) measures the consequence:
   the flat state scores **31.60 dB** against `T-2017-full.png`, matching a GPU-free numpy
   reconstruction from the same plates to 0.01 dB.

   > **The full-resolution 2017 composite (supplied 2026-07-30) is the spec for `projK = 1`.**
   > It is not vertical slats: it is a **tiled grid — ~7 columns × ~5 bands — of body fragments at
   > different scales, opacities and saturations, composited over one continuous room.** Multiple
   > instances of her coexist in a single unbroken space (the *Tango* move). Measured seams:
   > columns `[0.0537, 0.0742, 0.292, 0.3154, 0.377, 0.5693]`, rows `[0.4622, 0.4857, 0.7695,
   > 0.8021]`.
   >
   > **And the bands land on the room's own architecture.** Rows 0.4622/0.4857 match the
   > poster-rail transition measured independently from the dancer-free frame (0.4661/0.4886) —
   > **within 0.4% of frame height.** The hand-cut rule was *cut on the architecture*, so the
   > engine derives its bands from the measured room lines rather than inventing a grid.
   > Per-tile **scale, opacity and saturation** are grammar variables, not decoration: the 2017
   > piece desaturates some tiles and not others (saturation spread 0.162).

3. **The engine is a pure function `f(seed, t)`.** No accumulated state, no `requestAnimationFrame`
   inside the engine. That single property buys: deterministic film renders, O(1) seek to any
   moment, addressable permalinks, trivial multi-projector sync, and the honest claim that every
   state has a number.

   *Binds:* `check-danse.py` evaluates the clock **out of order** — late, then early, then late
   again — and requires the two late evaluations to be bit-identical, because a stateful clock
   answers differently the second time and a t-ascending test would hide exactly that. It also
   fails the build if `requestAnimationFrame`, `Date.now`, `performance.now`, or `Math.random`
   appears anywhere under `engine/`.

---

## ⚠️ Time-critical, needs your answer today

**Are you a current South Florida resident (Miami-Dade / Broward / Palm Beach / Monroe)?**
Your records are ambiguous — Miami Dade College teaching 2015–present, but the Watermill bio says
"Based in NYC." I can't resolve it and it gates real money closing in hours:

- **Oolite Arts — The Ellies Creator Award: $10,000 unrestricted, 35 awards, closes 31 Jul 2026,
  11:59pm (tomorrow).** No project proposal required. Unrestricted cash buys the entire proof-of-
  concept rig.
- **Oolite Arts — 2027 Studio Residency**, same deadline: free Miami Beach studio + digital lab,
  Feb 2027 – Jan 2028, plus a $12,000 Knight housing stipend. **This is literally the room this
  project wants.**
- **Bakehouse Art Complex short-term studio — closes 1 Aug 2026.**

If yes, these are the highest-value actions available this week and they cost an evening.
If no, they're closed and the plan is unchanged — Cinedans (15 Sept, free, **accepts installations**)
becomes proportionally more important.

---

## Where it lives

Follows the precedent already in this repo (`apps/vision-board-studio` — pure static, zero build,
zero dependencies, "deploys as-is to Cloudflare Pages"). **Nothing here forks new substrate.**

| Surface | Path | Why |
|---|---|---|
| The app | `` | `apps/` is root-manifest-sanctioned, no CI cost, static-deploy precedent |
| Governance record | `organs/artist/chambers/danse.yaml` | A-MAVS-OLEVM chamber; auto-covered by the existing `artist-records` gate |
| This plan | `docs/plans/2026-07-30-danse-generative-engine.md` | `docs/plans/` convention; `plans` dir row already exists |
| Deploy | `.github/workflows/deploy-danse.yml` + a `deploy_triggers.danse` row in `institutio/governance/gates.yaml` | `check-gates.py` enforces **exact set equality** — the row is mandatory, not optional |
| Session stream | rides the existing `artist` domain stream | no new stream row needed |

Chamber standing: **CURATED → STAGED** (the three 2017 pieces are the curated selection; the
submission package is the staged output). Must satisfy `validate-artist.py` rules 1–6 — including
`governance.artist_gate: true`, ≥1 `human_gates` entry, real `standard.evidence`, and
`artifacts.next_reviewable_output`.

**Deploy detail:** `wrangler pages deploy` ignores `.assetsignore` (recorded in
`docs/agent-code-diff-review.md:861`), so stage a public-only directory first —
`pipeline/` and `render/` must never ship.

---

## Phase 0 — Corpus (days 1–5)

`pipeline/` — local only, never deployed, derived artifacts gitignored.

```
0_export.sh          uvx osxphotos export ./raw --album danse --download-missing
1_vision/main.swift  VNDetectHumanBodyPoseRequest + VNGeneratePersonSegmentationRequest
1_vision/build.sh    swiftc -O -framework Vision -framework AppKit    # zero packages
align-tool.html      manual floor/wall line pass — static, local
2_register.py        homography → room frame; numpy masked phase-correlation refine
3_cluster.py         near-duplicate clustering (32×32 grayscale, L2)
4_derive.py          tier encode + manifest emit
```

`osxphotos` isn't installed — use `uvx osxphotos` (uv is present) after granting Full Disk Access.
**If it fights you, use Photos.app → Export Unmodified Originals.** Two minutes. Do not let the
export tool become the blocker.

> **Corrected by measurement (2026-07-30), after running the Vision pass on all 162 frames.**
> The original design keyed the crop vocabulary off body-pose joints. That was wrong for this
> corpus: **161 of 162 frames carry a person matte** (coverage 11–18%, quality 0.987–0.998), but
> **pose finds joints in only 65 and never reaches 8 confident ones.** The joint histogram says why
> — knees 40%, ankles 37%, hips 35%, then shoulders 3% and faces 2%. **The shoot frames legs.**
> There is no upper body for a whole-person model to anchor on. The first classifier reported
> 0 dancers out of 162.

**The matte is the primary instrument; pose is an optional refinement.** Regions are derived from
the silhouette — per-row connected components separate the two legs, and the lowest point of each
component is a ground contact, which is exactly the **`anchorY`** that makes feet land on the floor
at any plane angle. Where joints *are* available (14 frames), they name the anatomy; elsewhere the
proportional bands of each leg component carry it. Vocabulary is leg-weighted to match the material:
`foot, ankle, calf, knee, thigh, leg, contact, full`.

**Auto-partition by coverage, not pose:** matte < 5% → `room`; ≥4 confident joints → `dancer`;
otherwise `figure`. Both `figure` and `dancer` are the body stratum. Measured result:
**14 dancer / 147 figure / 1 room.**

**Only one frame (IMG_1570) is dancer-free** — so synthesize the clean room plate instead: median-
composite all 161 frames with the dancer masked out. The camera is locked off, so every frame
contributes its non-dancer pixels and the median is a perfect empty room. Conceptually apt, too —
the room is what's left when you remove her from every frame.

**Registration is the load-bearing step.** Do the manual pass *first*: `align-tool.html` shows each
photo, you click the floor line and the wall line. 161 × 5s = **15 minutes, zero risk.** Then refine
with masked FFT phase correlation and keep whichever residual is lower. Do not spend three days
automating to save fifteen minutes.

**Never ship alpha-matted cutouts, never premultiply.** One separate single-channel mask per whole
photo, sampled independently. Crops stay pure metadata — 16 regions × 161 photos = **2,576 crops
for zero additional bytes** — and the shader can animate the matte edge (`smoothstep` with animated
thresholds) to get the soft projected-light falloff a baked matte can't.

**Tiers:** `web` = 2048px AVIF q58 + 1024px luma WebP → **~11 MB total**; `film` = 3264px PNG
(~1.2 GB, gitignored). Manifest schema is `danse.corpus.v1`, all coordinates normalized [0,1].

> **Payload budget: 12 MB, ~3.5 MB before first frame.** This exceeds `experience-audit.py`'s
> default `max_kb: 1500` — add an explicit overlay entry in `experience-surfaces.json` rather than
> silently failing the estate gate.

---

## Phase 1 — Engine (days 5–15)

**Raw WebGL2. No three.js, no bundler, no importmap — plain relative ESM.** The scene is one
geometry (a quad) and one material; three.js's scene graph, loaders, PBR and raycaster are dead
weight and would be the largest dependency in a repo that has none. ~250 lines of GL boilerplate
buys total control of the frame loop, which the deterministic harness depends on. **The zero-build
convention survives** — this was the one place I expected it to break, and it doesn't.

```

  index.html   live page      film.html   render harness (no UI, no rAF)
  studio.html  seed browser   join.html   visitor upload (post-deadline)
  engine/
    gl.js        context, program, VAO, texture-array, FBO      ~250 ln
    rng.js       stateless SplitMix64 hash streams               ~40 ln
    room.js      room frame, projector matrix, continuity       ~120 ln
    grammar.js   stageAt(seed,t) — PURE, zero GL                ~300 ln
    renderer.js  stage → 3 blend passes → composite             ~280 ln
    corpus.js    manifest, residency window, prefetch           ~180 ln
    clock.js     RafDriver | StepDriver                          ~50 ln
    profile.js   perf tiering                                    ~60 ln
    engine.js    create() / renderAt(t) / resize() / setSeed()  ~120 ln
    shaders/     plane.vert.js  plane.frag.js  composite.frag.js
```

**PRNG: stateless hashing, not a stream.** `hash01(seed, stream, index)`. A sequential generator
would require replaying from t=0 to recover the state at 4:31; hashing makes every parameter at
every moment a direct function of `(seed, stream, index)` — **O(1) seek**. That is what makes the
piece addressable. Permalink: `#s=<seed64hex>&t=<seconds>`.

**Metamorphosis, never cuts.** Every slot has an independent phase offset and lifetime; every value
is `mix(paramAt(k), paramAt(k+1), ease(u))` with an opacity envelope. ~40 planes on independent
schedules means the stage is permanently mid-transition — **there is no frame at which anything
switches.**

**Slats are fragment, not geometry.** Non-negotiable: geometry slats would need a per-strip
projector solve and would destroy the continuity. Fragment slats also let count/width/skew animate
continuously, which is the metamorphosis requirement.

**Transparency: three ordered passes, no OIT.** Group by blend mode — `multiply` (the poster/shadow
planes give the piece weight), then `over` (the room), then `add` (light adds and never occludes —
*that is what a projector does*). Painter's sort back-to-front within each group, depth test on,
`depthMask(false)`. Sorting 40 items on CPU is free and deterministic.

**One texture array + a residency window** of 24 layers (web) / 48 (film). Because
`stageAt(seed, t+Δ)` is computable ahead of time, **the grammar's determinism is the prefetcher** —
you know exactly which photos beat *n+2* needs. Web VRAM ≈ 80 MB.

**Perf tiering by measurement, not UA-sniffing:** run a 60-frame warmup, measure actual frame time,
step tiers. hi = 40 planes @ DPR 2.0, mid = 28, lo = 16. Measured: 48 planes at 4K = 8.7 ms;
1080×1920 × 48 planes = 3.2 ms. **60fps on a mid-range phone is achievable but only with tiering.**

> **Go/no-go gate at day 9: two planes at different angles and depths must share one continuous
> floor line.** If that doesn't land, the whole conceit collapses to collage — stop and fix it
> before writing any grammar.

---

## Phase 2 — The film (days 13–30)

### Shape: 6:30, hard ceiling 7:00

At 6:30 a programmer building 75–90 minute blocks can place you anywhere. At 11 minutes you're a
decision. It also clears every cap on the parallel-submission ladder — **one master cut serves
everything.** (For reference, *Ghostcatching* is 7 minutes.)

| | Movement | |
|---|---|---|
| 0:00–0:45 | **ONE** | A single plane, one photograph, whole, barely moving. Room and floor line established. *An unaltered 2017 photograph is the opening shot — provenance first.* |
| 0:45–2:00 | **DIVISION** | The plane splits into slats; planes enter at depth; first lateral move; parallax does its work. The floor line still holds. **They learn the space is impossible.** |
| 2:00–3:45 | **PHRASE** | Full engine. Limbs arrive from different photographs on a pulse. Build an actual phrase with accents, a repeat, a variation — so a dance-literate viewer can *count* it. Do not let it become texture. |
| 3:45–4:45 | **STILLNESS** | Resolves to near-stasis. One near-complete figure assembles out of many photographs and holds. A body that never existed, in a room that never existed. Hold it longer than is comfortable. |
| 4:45–6:30 | **RESEED** | The engine visibly restarts — same structural moves, entirely different material. Then again, faster. Then again. Cut to black mid-phrase. |

**The infinitude is delivered by that final compression, not asserted.** Then hold four seconds on
one line of white text: **the seed of the version they just watched.** That makes the fixed cut
honest — this is not *the* film, it is *seed 0x…* of it — and it's the hinge to everything else.
No fades between movements; use the engine's own reseeds as punctuation.

**Also export a 2:50 cut** — Times Square Arts' Midnight Moment requires exactly 170 seconds
horizontal, rolling, no fee. Make it a render parameter, not a re-edit.

### Sound — generative from the same seed (your call, confirmed)

Rights-clean by construction: no clearance, no cue sheet, no counterparty. But the failure mode is
real and specific: **a one-to-one sonification where every slat goes *ping* is a data-art cliché**
and will sound like a screensaver — exactly the accusation to avoid.

- **Bed: room tone from a real room.** Large, dry, wood-floored, low HVAC hum ~50–60 Hz. This is
  what stops it sounding synthetic. Record it yourself — ideally the actual apartment if you still
  have access, which would be the honest version.
- **Depth → pitch + reverb send.** Each active plane gets a sustained tone; far planes lower and
  wetter, near planes higher and drier. As the camera moves and depth order changes, **the chord
  voices itself.** Restrict to a low drone plus a narrow set of just-intoned partials so it never
  sounds like a scale and never sounds random. *The chord literally is the spatial arrangement.*
- **Limb events → percussive transients, decimated 80–90%.** Short, dry, physical in origin — hand
  on wood, pointe shoe box on floor, fabric. Throw most away so a **pulse** emerges rather than a
  rattle. **This single decision is what makes it read as choreographic rather than as data. The
  rhythm should be countable by a dancer.**
- **Reseed → one sub-bass swell and a moment of silence.** Across the four accelerating reseeds
  those silences shorten — that's what communicates the acceleration.

Deliver **stereo**, ~-16 LUFS integrated, true peak ≤ -1 dBTP (it may play outdoors at Soundscape
Park next to traffic). Bake analyzed beat times into `program.json` so the cut stays deterministic.
**Lock the sound approach by day 5** — this is the schedule risk most likely to bite.

### Render harness

**Playwright + system Chrome (`channel="chrome"`)** — verified to give ANGLE Metal on this machine.
Playwright's bundled browsers aren't installed and `chrome-headless-shell` has no GPU. **Assert the
renderer string at startup and abort if it lacks "Metal"/"Apple"** — one line that prevents
rendering an entire film on SwiftShader and only noticing at the end.

**Capture path** (each step chosen from measurement, not intuition):
```
draw (8.7–17 ms)
  → PBO readPixels into PIXEL_PACK_BUFFER          # direct readPixels = 889 ms. Never.
  → fenceSync + clientWaitSync(f, 0, 0) POLLED     # a large timeout throws INVALID_OPERATION
  → getBufferSubData                                # 11–28 ms
  → new Blob([buf])                                 # Blob 1470 MB/s vs Uint8Array 34 MB/s
  → POST to local node sink → ffmpeg stdin
≈ 45–70 ms/frame → 18,000 frames ≈ 14–21 min
```

**Segmented rendering — 600-frame segments, one fresh browser process each.** Not an optimization:
sustained per-frame blob churn triggers `net::ERR_BLOB_OUT_OF_MEMORY`. Segmenting caps memory by
construction, makes renders **restartable** (segment 7 fails → re-render only 7), and allows 2-way
parallelism. Determinism is unaffected — segment *k* renders `t ∈ [k·10s, (k+1)·10s)` from the same
pure function.

```bash
seq 0 38 | xargs -P2 -I{} python3 render/render.py \
  --program render/program.json --tier film \
  --width 3840 --height 2160 --fps 60 --segment {} --segment-frames 600 --capture raw

ffmpeg -f concat -safe 0 -i segments.txt -c copy DANSE_master_4K60_ProRes422HQ.mov
ffmpeg -i DANSE_master.mov -vf scale=1920:1080:flags=lanczos -c:v libx264 -preset slow \
  -crf 18 -pix_fmt yuv420p -movflags +faststart -c:a aac -b:a 320k DANSE_1080p.mp4
```
(`-vf vflip` on the raw path — GL readback is bottom-up. `prores_ks -profile:v 3 -qscale:v 9` for
the master; `prores_videotoolbox` for the fast lane.)

**Determinism proof — make it a gate:** render segment 3 twice, `shasum` both, require equality.
Grain must be `hash(seed, frameIndex, pixel)`, never `random()`. **Run this by day 20, not day 30.**

**Delivery:** ProRes 422 HQ master + H.264 1080p screener → **Vimeo private-with-password, download
enabled.** Verify download is actually on — it's the #1 mechanical failure. No expiry before
December. Don't build auth for a festival deadline.

---

## Phase 3 — The public surfaces (days 22–32, then ongoing)

### Live page — free once the film runs
`RafDriver` instead of `StepDriver`, same engine. `seed = hash(dayOfYear)` gives a shareable
"today's dance"; `?seed=` overrides; a permalink button emits `#s=…&t=…`.

### Identity — your structure, implemented
You said it well and Vaynerchuk is right about it: **the project gets its own handle; @4444jj999
becomes the feed for your love of visual and audio work.** So:

- **`@` (new, project)** — the piece's own account. Every post is a seed.
- **`@4444jj999`** — features the work as one of the visual/audio things you love, in your own
  voice, cross-posted not duplicated. It's a curatorial feed, not a promo channel.
- The app deploys to its own Cloudflare Pages URL, featured from `a-mavs-olevm`.
  *(Optional cheap lever: register a domain for the piece — `etceter4.com`, named in the portfolio
  repo, is still unregistered.)*

### The seed mechanic
A post is **one seed, rendered, plus its number.** First caption line is the seed, alone:
`seed 0x3F2A1C`. Within ten posts followers recognize the account by the shape of its caption —
worth more than any hashtag.

**One change to the original idea, and it's the important one:** don't just let them visit *your*
seed — the site's first action is to **generate a fresh seed, render it live, and hand it back as a
shareable image plus a short URL.** A seed someone else chose is a link; a seed with your name on it
is an object. No upload, no camera permission, no account, no privacy exposure. **Every participant
becomes a post.**

**Format split:** Reels ~50% (7–15s, one continuous move through one seed, no cuts, loop-safe,
rendered natively vertical — **never letterboxed**, and **silent-legible**, because parallax has to
carry it with sound off). Carousels ~30% — where the 2017 material lives, and where the saves come
from. Stills ~20%. Stories daily with a "tap for a new seed" sticker.

**First 10 posts** (2×/week, Aug–Sept — sequence matters more than any single post):

1. **THE AFTERNOON** — one unaltered 2017 photograph. `20 June 2017`. No seed, no link yet.
2. **THE HAND-CUT** — a 2017 photomontage shot flat, cut edges and continuous floor line visible.
   **This is the credibility floor: it proves the concept predates the technology.**
3. **THE FIRST SEED** — first engine render. Carousel: render / 2017 source / the montage from #2.
   *"Same room. Same afternoon. The scalpel is now a number."* Link goes live. **Pin this.**
4. **PARALLAX** — first Reel. 10s, one continuous move, silent-legible, loops.
5. **ANATOMY** — carousel: a render, the same frame with pose overlaid, then the four *different*
   2017 photographs the four visible limbs came from. *"It doesn't crop for composition. It reaches
   for a forearm."* **This answers the skeptic in public, in advance.**
6. **YOUR SEED** — the interaction post. First call to action; don't make one before #6.
7. **THE WALL** — the poster wall legible, and the one honest paragraph about what the machine does
   to her. Say it once; don't repeat it weekly.
8. **NEVER TWICE** — nine seeds, each labelled. Show the infinitude, don't assert it.
9. **THE NINE YEARS** — the gap itself. It's the most interesting fact about the work.
10. **SUBMITTED** — day the entry goes in (~20–24 Aug). The film's final frame: the seed, white on
    black. *"Six minutes thirty. One seed. It's in."* Claim nothing about selection.

**Cadence:** 2×/wk Aug–Sept → 3×/wk Oct (notification) → daily during Art Week (1–7 Dec) → **daily
all January.** January is **Genuary** (the annual generative-art challenge, prompts at genuary.art)
*and* the festival is 20–24 Jan. Run the engine as a Genuary practice for 31 days and let it land on
the screening. Nobody else in that program will do this.

> Verify every handle before tagging — tagging a wrong account is worse than not tagging.

### Visitor upload — **after 31 Aug**
**MediaPipe Tasks Vision, not MoveNet.** `pose_landmarker` returns 33 landmarks **and** a
segmentation mask from one model — exactly the two artifacts the corpus pipeline produces, so an
uploaded photo joins the vocabulary through the *identical* code path. MoveNet gives 17 keypoints
and no segmentation, forcing a second model anyway. Vendor it offline into `vendor/mediapipe/`
(~8.5 MB), **lazy-load on click only** so it never touches the 12 MB base budget. Fully
client-side — say so in the UI.

---

## Phase 4 — The room

### Proof-of-concept shoot (~$185, this month) — this produces the one image everything else needs

**Materials:** 20 yd white polyester chiffon/voile 60" (~$70 — *if you buy one thing, this is it*),
3 frosted PEVA shower liners ($25), 10 yd bridal tulle ($20), one mylar emergency blanket ($2,
crumpled — poor-man's HoloGauze, use sparingly), vellum roll ($15), tension rods + 40lb mono +
binder clips + gaff ($53). Foam core is free from any frame shop.

**Projector: rent, don't buy** — Miami day rates $80–240. Get **3LCD (Epson/Panasonic), not DLP** —
no color wheel means no rainbow artifacts. **The brightness math decides it:** four layers at ~65%
transmission each leaves the back layer 0.65³ ≈ **27%** of source. At 3,000 lm that's ~13 fc —
cleanly photographable. At 400 lm (a cheap LED "1080p-supported" unit) it's 1.8 fc, which is noise.

**Room prep is what makes it not a home video:** kill every ambient source (trash bags on windows,
tape over router and smoke-detector LEDs — one stray LED in frame is the tell). Real distance
between layers: 2 / 4 / 7 / 10 ft. **Terminate the image in void** — black sheet behind the last
layer. Nothing touches anything. **One practical light at 1–2% of projection brightness**, bounced
off a side wall, never in frame — without it you have a screensaver; with it you have a room.

**Camera settings — the part that decides whether the footage is usable:**
- Force the projector to **exactly 60.00 Hz**, then shoot **30fps @ 1/60 (180°)** — exactly one
  refresh per exposure, zero banding. This is the safe default.
- **Want 24fps? Do NOT use 1/48** — at 60 Hz that integrates 1.25 refreshes and bands. Use
  **24fps @ 1/60 (144°)**, or 24fps @ 1/30 (288°) for a dreamier look. Both clean.
- Only 59.94 Hz available? Shoot **29.97 @ 1/60** (exactly 2.0 refreshes), conform in post.
- **If you see banding, LENGTHEN the shutter. Never shorten it.** Shortening guarantees banding,
  and the imagery moves slowly enough that dragging the shutter is free light.
- Rolling shutter: keep angular velocity under ~10°/sec. Slider or fluid head. No whip pans.
- **ISO 800–1600, f/2.0–2.8, manual WB locked 5600–6000K** (auto WB will hunt as the imagery's
  color changes and ruin every take), **manual focus locked** (AF hunts on low-contrast scrim in
  the dark, guaranteed). **Expose the brightest projected highlight to ~70–75 IRE — underexpose
  2/3 stop from instinct**; projected light clips unrecoverably. **10-bit log** (Apple Log ProRes
  4K30 on iPhone is fine) — layered projection is almost entirely shadow gradients and 8-bit 4:2:0
  will band. Record room tone separately; the fan is in every take.

**The one image the whole shoot exists to produce:** *a wide, eye-level photograph in which a human
figure at real scale stands between two lit scrims, and a dancer's arm exists simultaneously on the
near scrim (sharp, bright) and the far scrim (soft, dim, larger, offset) — with a visible
negative-space shadow where the near arm blocked the far one.* Every element is load-bearing: human
figure = knowable scale; same arm at two sharpnesses = the thesis proven optically with no caption;
the occlusion shadow = proof the light is physically transported, not composited.

**12 shots, one day.** The critical three: **#3 lateral truck** (the parallax shot — if you get one
shot, this is it), **#4 rack focus** front layer → back (optical proof of discrete surfaces at
discrete distances), **#12 empty room with work lights on** showing scrims, projector, cables —
**include this; showing the apparatus is what makes a curator trust you.**

### Digital twin
Same three.js-free scene + room geometry + a walkable camera. **Not a mockup — the simulation with
the walls turned on.** Say that out loud; curators can tell a rendered concept from a running system.

**Compute the transmittance honestly — it's cheap and it's the difference.** Render from each
projector's POV accumulating transmittance front-to-back (τ ≈ 0.65 sharkstooth, 0.85 bobbinet).
5–6 extra draw calls. Payoff: when a bright arm on layer 2 occludes layer 4, **layer 4 gets a dark
arm-shaped hole.** That negative-space shadow is the most convincing artifact in the piece, a
game-engine flythrough will never have it, and **it is physically real in the room — so twin and
install match.**

Reads-as-photograph, in descending impact: a **human camera** (6-DOF noise at 1/f spectrum, walking
0.5 m/s not 1.3 — flythroughs are the #1 tell) · physical camera model with real focal length and
CoC bokeh · lens imperfections at 20% strength each · animated grain last · **real architecture —
a baseboard, an HVAC diffuser, an EXIT sign glowing green in the far corner.** A curator's eye finds
the EXIT sign and believes the whole image. Nobody puts an EXIT sign in a render.

**The seal:** intercut the twin with the POC footage, matched in grade, unlabeled — then reveal
which was which on the last slide. If they can't reliably tell, you've won the argument that it can
be built.

### Installation spec, costed
**Tier A (15×20 ft project space): ≈ $18,750.** 7 surfaces — 5 fabric panels at yaw
−38° / −14° / +7° / +26° / +49°, depths 3.5 / 5.5 / 8 / 10.5 / 13 ft, graded bobbinet → HoloGauze →
sharkstooth; the back wall itself; **and one panel running into a room corner so the image folds at
90°** — the most photographable moment in the room, and it costs nothing. Asymmetric, no two planes
parallel: parallel planes read as a corridor, non-parallel planes read as a body.
2 × Panasonic PT-VMZ51 ($3,049 ea — **optical lens shift** matters more than resolution here, since
you must hang off-axis to keep visitor shadows out of the beam) + 1 × Optoma ZH450ST short-throw.
One Mac mini M4 Pro driving all three.

**Tier B (30×40 ft museum): ≈ $46,000** renting the big projectors, ≈ $90,000 buying everything.

**Two opinionated calls that save real money and read better:**
- **No mapping software. $0.** The scene is already 3D — place each virtual camera at the surveyed
  real projector pose with the real lens's FOV and the render is automatically correct on every
  surface. MadMapper/Resolume would treat your output as a flat texture to deform, **discarding the
  depth information that IS the artwork.** Saves ~$3k in licenses and removes a proprietary
  dependency from a work meant to run forever.
- **Do not edge-blend.** Blending is for seamless walls; this work wants **fracture.** Let beams
  overlap and disagree — you get real additive brightness you cannot render, for free, and you
  delete the largest source of load-in time and calibration fragility.

**Interaction — the design worth fighting for.** One fixed IR camera, MediaPipe on-device, and
**the visitor never appears in the piece.** Their pose becomes a *query* against the pose index
already computed on the 161 photographs: raise an arm, and the engine re-weights selection toward
fragments whose pose rhymes with yours. **The archive answers you.** Compositing a live silhouette
is what every mediocre interactive installation does — it turns the work into a mirror. The query
approach is a better artwork, cheaper (no live compositing, no latency budget), and makes privacy
*trivially true*: nothing written, nothing transmitted, landmarks discarded next frame. Interaction
is a modulation, never a requirement — **the engine must be beautiful with zero visitors**, because
empty is the most common state.

**One acceptance test that matters:** yank the wall plug. Three times. If the room doesn't come back
cold within 4 minutes with zero human input, it isn't finished.

**Rows that win a venue:** zero dedicated staffing · zero daily ops · no consumables · 20,000 hr
laser · no haze, no fog · ~1,150 W on 3 × 20A circuits · no rigging (freestanding) · **no internet
needed to run.** Most media installations are a maintenance tax. Be the one that isn't, and say so
numerically. All textiles **NFPA 701 certified — have the PDF**; this kills more installs than any
technical failure.

### Pitch artifact
**One self-hosted single-scroll page** (with an auto-generated PDF from the same source for grant
portals). Not a deck — the work is a running system, and a deck argues against your own premise.
The page **embeds the actual engine, running live and deterministic, in the browser of the person
deciding.** No other medium can do that.

Section 0 is a full-viewport cold open: the engine, silent, autoplay, **no text for 4 seconds.**
Then small, bottom-left: *"Running live in your browser. Seed 0x…. It has never shown you this
arrangement, and it never will again."* Section 3 is a tight contact-sheet grid of all 161 stills —
the section a technologist would delete, and the one that makes it an artwork rather than a demo.
Section 6 is an **orthographic measured plan and section, drawn as a line drawing, not a render** —
that signals "buildable" harder than any render can. Section 7 is the venue requirements table
above; it's the section venues actually read. Four entry fragments (`#curator`, `#programmer`,
`#space`, `#grant`) reorder one artifact for four audiences.

---

## The submission argument

The risk isn't "this isn't dance." It's **"I don't see a body."** Against *choreography,
performance, cinematography, editing*, this scores very high on editing, arguably high on
choreography, and structurally weak on performance and cinematography. **That gap closes in the
form, not the statement:**

1. **Pose detection means the unit of selection is a body part, not a rectangle.** The engine
   reaches for a forearm, a clavicle, an instep. That's a choreographic operation and it's visible
   on screen — **and it must be legible within the first fifteen seconds.** If the cut lands on
   anatomy, the audience reads a body. If it lands on rectangles, they read graphics.
2. **The continuous floor line** is what makes fragments *a body in a space* rather than a collage.
   Protect it, reveal it early.

**The lineage that makes it legible** — Maya Deren's *A Study in Choreography for Camera* (1945),
whose whole move is the impossible geography assembled by the cut ("with a turn of the foot, he
makes neighbors of distant places"); Norman McLaren's *Pas de Deux* (1968), individual frames
exposed up to 11 times so one body appears in many simultaneous states; **Hilary Harris's *Nine
Variations on a Dance Theme* (1966)** — one ~50-second phrase performed nine times, only the
treatment varying, which is your film in one sentence; and **Zbigniew Rybczyński's *Tango* (1980,
Academy Award)** — 36 bodies looping in one impossible room, ~16,000 hand-cut mattes over seven
months. **Your 2017 hand-cuts are the same gesture; the engine is what Rybczyński would have built
if he could.** Rosenberg's screendance theory calls the form *"recorporealization — a literal
re-construction of the dancing body via screen techniques"*: the field's own definition is the
construction of an impossible body, not the recording of a dance.

**Do not cite** teamLab, Universal Everything, Ryoji Ikeda, or Rain Room — each reads to a dance
jury as commercial immersive spectacle or data aesthetics with no body, and will actively cost you.

**Answer the AI question head-on**, in one hard sentence in the technical note plus a credit line:
> Nothing is synthesised. Every pixel is a photograph taken on 20 June 2017. The pose model is a
> measuring instrument — it locates a shoulder; it does not draw one. No diffusion, no training on
> anyone else's work, no synthetic frame.

On-screen: **"No AI-generated imagery. All images photographic, 2017."** In 2026 that's a
credential, not a disclaimer — a generative work with a clean provenance chain is rare.

**The sociocultural criterion, one restrained sentence.** It's already in the material: a ballerina
in a room papered with the advertising of classic horror — the genre built on the spectacle of the
imperilled female body (*The Red Shoes* → *Suspiria* → *Black Swan*). The engine then does to her
what the posters do: takes her apart and re-sells her in pieces, forever, never repeating. **Say it
once, and name your own complicity** — that's more persuasive than absolving yourself, and it's the
register this festival programs in.

**Plant the installation without asking for it** — two sentences in the technical note, as fact:
> The film is a fixed render of a live engine. The engine runs in real time at exhibition resolution
> and has been built for multi-surface projection.

No ask. Anyone who wants the room now knows it exists. Pitching an installation *inside* a film call
signals the film is a means rather than an end, and puts a request in front of a panel with no
authority to grant it.

### Package

The package *specification* is not prose — it is
[`submission/screendance-2027.yaml`](../../submission/screendance-2027.yaml),
and `submission/check.py --package <dir>` is the predicate that says whether a staged package can
be filed. What follows is the reasoning behind those numbers; the numbers themselves live there.

Vimeo password-protected + **download enabled** · ProRes master, 1080p or 4K screener, 24 or 30fps,
**16:9** · rights declaration · ~50-word synopsis (program copy, read aloud) · ~200-word long
synopsis carrying the 2017→2026 arc · artist statement · **six stills ≥3840×2160, each a different
seed with the seed in the filename** (`seed-0x3F2A1C.jpg`) — free, and it makes the concept material
before they press play — **including one unaltered 2017 photograph** · a 40-second trailer that is
one unbroken camera move through one seed, no cuts · bio leading with the nine-year gap · technical
note.

**File 20–24 Aug, not on the 31st.** Submittable timestamps are visible, and a panel convening in
September notices who was scrambling. It also leaves room to fix a broken download link.

### Credit — your decision, and I'm not building the plan on it
The strongest read of the work names her. But this was your apartment and your ex, and whether to
contact or credit her is entirely yours; nothing in this plan requires it. Two things worth knowing
so you're choosing rather than defaulting: (a) the submission form asks you to attest clearances
*including dancers* — you own the photographs, and a personal release is a separate, prudent
document, especially before any museum installation or sale; (b) a panel led by Pioneer Winter,
whose practice is about who gets to be visible and who is treated as material, will register the
credit line either way.

**The poster wall is the one genuine legal exposure.** They're photographed as set dressing,
fragmented, partly out of focus — a strong transformative/incidental posture, but a posture is not
a clearance. Inventory which posters are legible enough to function as a *reproduction* rather than
as *a wall*, and let slat density break those further. Fine for a festival; **get an IP attorney to
review the inventory before any museum installation or sale — museums will ask.**

---

## Parallel ladder (verified deadlines)

| Target | Deadline | Fee | Note |
|---|---|---|---|
| **Oolite Ellies Creator Award** | **31 Jul 2026, 11:59pm** | none | $10k unrestricted. South FL only. |
| **Oolite Studio Residency 2027** | **31 Jul 2026** | none | Free Miami Beach studio + $12k stipend. South FL only. |
| **Bakehouse short-term studio** | **1 Aug 2026** | — | Wynwood. |
| **ScreenDance Miami 2027** | **31 Aug 2026, 11:00pm EST** | none | The target. |
| **Cinedans FEST 2027** (Amsterdam) | **15 Sept 2026** | **none** | **Out-of-Competition explicitly accepts installations — pitch the room openly here.** |
| **Times Square Midnight Moment** | rolling | none | Exactly 170s horizontal. Monumental public projection. |
| **Miami-Dade Cultural Affairs Q2** | **13 Oct 2026** | none | ART grants $7.5k/$15k. Pre-app consult required — do it in September. |
| **Mignolo Screendance** (Brussels) | **13 Oct 2026** | ? | Laurel + European premiere *before* the Miami notification. |
| **Locust Projects Main Gallery** | ~Sept–Nov 2026 | none | $10k budget + $5k fee + per diem, for **new installation work**. Watch weekly from 1 Sept. **The #1 target.** |
| **WaveMaker Grants** | ~Feb–Apr 2027 | none | Up to $6k for work in *unconventional spaces*. Covers the whole Tier A rig. |

**The conversion move, if selected:** you get ~12 weeks between notification (end Oct) and the
festival — the only window all year when Miami's institutions have a reason to take your call. Use
it within 10 days. Ask Miami Light Project for **"Films You Gotta See BIG!"** placement on the
7,000 sq ft Soundscape Park wall: *"This piece is composed of translucent planes at depth. It gains
rather than loses at that scale — most shorts don't."* That's a programming argument and programmers
respond to programming arguments. If it lands, you get a monumental public projection in January —
**functionally the installation's first public exhibition, paid for by someone else, with press
attached.** Second, smaller ask: run the live engine in the Light Box lobby during festival nights.
Bring your own projector; ask for nothing but a wall and a power drop.

**Dead ends, don't build on them:** ICA Miami and The Bass both explicitly refuse unsolicited
proposals (relationship only). Knight Arts Challenge appears discontinued. Eyebeam is NYC-only.
Superblue is gallery-brokered. Miami Light Project's *Here & Now* closed 20 June — put ~June 2027
in the calendar.

---

## Verification

`done.sh` — exit 0 ⟺ done. Calls `scripts/verify-scoped.sh` plus:

1. `manifest.json` validates against `danse.corpus.v1`; all 161 photos have `align.h`, `lines`,
   ≥1 region; every referenced plate and mask exists.
2. **Determinism gate:** `render.py --segment 3` twice → `shasum` equal. *This is the one that
   catches a leak of non-purity into the engine.*
3. **Continuity gate:** render two planes at known different angles/depths from the same seed;
   assert the detected floor line lands within 2px on both. *The day-9 go/no-go, as a test.*
4. `python3 organs/artist/validate-artist.py --fleet --quiet` passes with the new chamber.
5. `python3 scripts/check-gates.py` passes — the `deploy_triggers.danse` row exactly mirrors
   `deploy-danse.yml`'s `on.push.paths`.
6. Page loads with first frame < 1.5s; total payload ≤ 12 MB; `experience-surfaces.json` overlay
   declares the budget.
7. Master exists, runtime within 6:20–7:00, Vimeo link resolves with password **and download on**.

Live check: `bash scripts/cf-wrangler.sh pages deploy <staged-dir> --project-name danse`, then
load the URL and confirm two different seeds produce visibly different rooms.

---

## Open items

| Item | Owner | Note |
|---|---|---|
| **South Florida residency?** | you | Gates ~$22k closing 31 Jul / 1 Aug. Needed today. |
| **The AI-chat brainstorm corpus** | you | Both declared stores are **not on disk** — `~/Workspace/_conversations-private` is missing entirely and `session-meta` exists only in the Archive4T backup. `corpus_resolve.py` reports 0 populated. Point me at them (or mount Archive4T) and I'll fold them in; the social plan above is my design, marked so it's easy to swap. |
| **Title** | you | Working name `danse`. "DANSE MACABRE" is available and apt — a ballerina in a room of horror posters already *is* one. |
| **Dancer credit / release** | you | See above. Nothing in the plan depends on it. |
| **Confirm with Miami Light Project** (305.576.4350) | me, week 1 | Runtime cap, codec spec, premiere requirement, aspect preference for the Soundscape wall — none are stated in the public call. |
| **Poster inventory → IP review** | you, before any museum | Festival-safe; a sale or museum install is not. |
| **New IG handle** | you | Project account; `@4444jj999` becomes your visual/audio feed. |

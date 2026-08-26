# ScreenDance 2027 score-led ballet hardening

Branch: `work/screendance-2027-hardening`

This revision supersedes the public-surface-first order in the earlier August 26
plan. Deadline work is limited to the shared engine and competition film. Public
site polish, LinkedIn collateral, installation proof, and live browser audio wait
until a durable filing receipt exists.

## Locked production design

- River seed `20170620`, passage 0, one fixed competition capture.
- Two Delibes movements at native tempo: *Valse lente* from *Sylvia* (reference
  2:34) and *Valse* from *Coppélia* (reference 3:23), followed by the existing
  four-second black signature. Production must remain below 6:15 and may not use
  affine time-stretching.
- Preserve the exact Paul De Bra source arrangements and their supplied SHA-256
  identities. Keep composition, edition, source arrangement, adapted MIDI,
  performance, recording, and samples as separate evidence layers.
- Adapt the five accordion voices to violin I, violin II, viola, cello, and
  contrabass while retaining declared triangle and timpani accents. Render with a
  version-pinned FluidSynth and digest-bound `MuseScore_General.sf3`.
- Required credit: “Music by Léo Delibes. Source arrangements by Paul De Bra,
  adapted and re-orchestrated for Danse under CC BY 4.0. Changes include
  instrumentation, sequencing, cue markers, and mix.”

## Choreography contract

Add a self-digesting `danse.choreography.v1` contract bound to the score and
corpus. It owns authored motifs, ordered registered source-frame IDs, phrase
assignments, pose dwell, transition duration, cut mode, and legibility limits.
Authored frame order is not a claim of photographic chronology.

Expose pure `poseAt(score, choreography, seed, t)` queries in JavaScript and
Python, and pass choreography through the shared live/offline `step()` and
`frameAt()` path. Keep 32-bit seeds, random access, and `f(seed,t)` purity.

Production grammar:

- `ONE`: one whole registered photograph for a complete phrase.
- `ASSEMBLY`: the exact 2017 composite, stable and unchanged.
- `DIVISION`: slow camera departure with coherent two-bar poses.
- `PHRASE`: countable whole-body motif, repeat, and variation.
- `STILLNESS`: one readable body for a complete phrase.
- `RESEED`: controlled development and climax without global hard reseeds.
- `SIGNATURE`: cadential fade into the complete four-second black receipt.

Ordinary poses dwell at least two bars; pose and topology transitions last at
least one bar; at most 25 percent of fragment area may begin changing on a bar
boundary. Selection may be cached, but blend and camera interpolation use exact
time continuously.

## Truth and custody

The tracked corpus contains 162 records: 161 registered raw photographs and one
unregistered archival composite. The current 256-tile solve uses 77 corpus
sources—76 registered raws plus the archival composite itself. New choreography
motifs must use registered raw photographs only; do not misstate the current solve
as 77 registered sources.

The official deadline pages conflict: Submittable says 11:00 PM EST and the
program page says 11:59 PM. The operational wall is 10:00 PM
America/New_York on August 31 as a conservative internal buffer, not a timezone
conversion. Upload is targeted for 6:00 PM local.

## Delivery and gates

Production delivery must reject fixture repertoire, pending selection, affine
timing, absent or mismatched score/choreography/MIDI/audio/toolchain identities,
and duration drift. The competition audio profile uses only declared classical
sources; the private apartment grain bank remains a later hybrid profile.

Human gates remain separate and unresolved until their receipts exist: artistic
final-cut approval, dancer/photograph/poster rights, music credit and license
review, biography and rights-declaration approval, film-tier hydration, origin
photograph custody, upload, password/download verification, archive choice, terms,
and submission. Anthony retains every account-owned action.

## Acceptance

Run the repository portable batch on the exact tree, add score/choreography/audio
identity and cross-runtime parity tests, retain the 31.60 dB floor, run the
Chrome/Metal batch, produce a synchronized full-speed A/B review, and require
`./done.sh --package <package-root> --phase package` before upload. If the full
diptych lacks artistic approval by August 28 at 6:00 PM, the registered
contingency is the complete *Sylvia Valse lente* movement using the same contracts;
the fixture and flashing cut are never fallbacks.

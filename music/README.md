# Music contracts

This directory contains reversible fixture infrastructure for Danse issues #8
and #9. It does **not** contain selected repertoire, an approved arrangement, a
cleared recording, or an accepted score/image relationship.

`repertoire.yaml` separates six rights/provenance layers that cannot clear one
another: composition, edition, arrangement/MIDI, performance, recording, and
sample. `validate_repertoire.py` verifies every tracked source digest and rejects
ignored or untracked paths before reading them. Cleared recordings require exact
tracked source bytes, and every licensed layer must name its license. The
validator also rejects the false equivalence “public-domain composition therefore
free recording.” Its enforced interchange shape is `repertoire.schema.json`.

`fixtures/generated-study.mid` is 891 bytes of generated test data. It is exactly
reproducible from `generate_fixture_midi.py`; it mirrors the nominal 390-second
shares of `render/program.json` only so all seven movement bindings can be tested.
It is explicitly `not-selected` and `fixture-only` in the register.

`compile_score.py` reads the MIDI markers, tempo, meter, CC expression, program
changes, and notes and emits `score.json` under `score.schema.json`. The compiled
contract contains:

- tempo and meter maps plus beat/downbeat positions;
- phrase, cue/accent, and dynamic events;
- orchestration/stem declarations and source digests;
- movement boundaries bound in order to the existing program;
- fixed lookup buckets for random access independent of elapsed river time.

CC11 expression is channel-local, so each register entry must name one explicit
`score.dynamics_source` track/channel for the global score clock. Expression on
other stems is never silently folded into that value. Notes beginning at the same
tick retain their authored Standard MIDI File track/event order. Program changes
and sustain-pedal state follow their MIDI output channel across tracks; files that
declare unsupported multiple output ports fail closed.

The program still owns each passage's varying absolute duration. At query time,
the nominal score is restarted and affinely mapped over that passage. Both image
and audio-event consumers use the same absolute `{t0, seconds}` window; no clock,
entropy, or accumulated playback state enters `engine/`.

The interval API is deliberately a half-open stream of authored note-ons and cue
starts. A note already sounding at an arbitrary seek boundary is not emitted as
a second note-on. Sustained-voice restoration would require its own declared
voice-allocation and buffer-offset contract; this fixture does not invent one.
The embedded contract digest covers a type-tagged canonical form of every score
field except the digest itself, and both JavaScript and Python reject stale or
edited content before exposing its identity.

## Reproduce and verify

```bash
python3 music/generate_fixture_midi.py --check
python3 music/validate_repertoire.py
python3 music/compile_score.py --check
python3 scripts/tests/music-score.test.py
```

The fixture is opt-in and therefore does not silently alter the current artwork:

```bash
python3 -m http.server 8080
# open http://localhost:8080/?score=music/score.json

node sound/control.mjs --rate 30 --score music/score.json --out .work/fixture-control.json
python3 render/render.py --score music/score.json --segment 0 --codec preview
```

`sound/web_audio.mjs` creates a deterministic plan and schedules only supplied,
declared `AudioBuffer` sources. Each supplied stem is paired with its
`audio_source_sha256`, which must match the cleared orchestration identity before
the adapter creates a source node. It never creates an oscillator. The Python
sound renderer exposes the same mapped event plan, but deliberately refuses to
render this fixture: its orchestration declares no cleared audio source bytes.

## Remaining gates

Anthony must select the repertoire and explicitly accept the score/image
relationship. Before any score-driven cut or package can be called final, the
register must then identify and validate the exact composition evidence, edition,
arrangement/MIDI, performance, recording, samples/stems, and derived bytes. Only
after those sources exist can the stem renderer and A/B audiovisual evidence be
completed. A fixture passing tests is not artistic approval.

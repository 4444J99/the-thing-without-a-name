# Score → motion A/B capture evidence (2026-08-05)

For one fixed (seed, stream, passage) the same absolute time is sampled with and
without the score clock. `score_delta` is exactly the choreography the score
contributes; the image alone (`without_score`) is the control. `audio.notes` is
what `planWebAudio` schedules in the same 250 ms window.

- score contract: `f58a3a29a6fed5ecaa2e8580553841c852ad1d7aa63b16f054c5be08ffd580a4`
- seed: `0x12345678`, stream: `0`
- passage: `0` (t0=0s, 437.579039s) over 390 source seconds

**220 boundaries sampled** (25 structural, 22 where the score moves the image across the boundary, 31 with an audible note in-window). The full machine receipt is `score-to-motion-ab.json`; downbeats are in the JSON.

## Declared structural boundaries that move the image

`score_delta` = visual state WITH the score minus WITHOUT it at the same time (the choreography the score contributes). `score_transition` = the image motion the boundary itself causes under the score (just-after minus just-before, ±10 ms).

| t (s) | boundary | movement | beat | dynamic | score_transition (div, azi, ele, spread, projK, turn) | recast | hold | audio notes |
|---|---|---|---|---|---|---|---|---|
| 44.880 | movement ASSEMBLY | ASSEMBLY | 80 ↓ | 56 | d-0.050 | y |  | — |
| 44.880 | phrase assembly | ASSEMBLY | 80 ↓ | 56 | d-0.050 | y |  | — |
| 72.930 | movement DIVISION | DIVISION | 130 | 76 | d+0.031, a-0.038, e-0.012, s+0.031 | y |  | fixture-piano-ch1-p0 61@72.92984 |
| 72.930 | phrase division | DIVISION | 130 | 76 | d+0.031, a-0.038, e-0.012, s+0.031 | y |  | fixture-piano-ch1-p0 61@72.92984 |
| 72.930 | cue division-entry | DIVISION | 130 | 76 | d+0.031, a-0.038, e-0.012, s+0.031 | y |  | fixture-piano-ch1-p0 61@72.92984 |
| 134.640 | movement PHRASE | PHRASE | 240 ↓ | 104 | a-0.100, e+0.052, t+0.142 | y |  | fixture-piano-ch1-p0 62@134.639704 |
| 134.640 | phrase countable | PHRASE | 240 ↓ | 104 | a-0.100, e+0.052, t+0.142 | y |  | fixture-piano-ch1-p0 62@134.639704 |
| 134.640 | cue phrase-entry | PHRASE | 240 ↓ | 104 | a-0.100, e+0.052, t+0.142 | y |  | fixture-piano-ch1-p0 62@134.639704 |
| 143.616 | cue phrase-accent-a | PHRASE | 256 ↓ | 104 | a-0.001, s+0.038, t+0.166 | y |  | fixture-piano-ch1-p0 63@143.615685 |
| 148.104 | cue phrase-accent-b | PHRASE | 264 ↓ | 104 | a-0.043, t+0.229 | y |  | fixture-piano-ch1-p0 64@148.103675 |
| 152.592 | cue phrase-accent-c | PHRASE | 272 ↓ | 104 | a-0.001, e+0.050, t+0.300 | y |  | fixture-piano-ch1-p0 65@152.591665 |
| 252.449 | movement STILLNESS | STILLNESS | 450 | 36 | a-0.012, e-0.061 | y | y | fixture-piano-ch1-p0 66@252.449445 |
| 252.449 | phrase stillness | STILLNESS | 450 | 36 | a-0.012, e-0.061 | y | y | fixture-piano-ch1-p0 66@252.449445 |
| 252.449 | cue stillness-entry | STILLNESS | 450 | 36 | a-0.012, e-0.061 | y | y | fixture-piano-ch1-p0 66@252.449445 |
| 319.769 | movement RESEED | RESEED | 570 | 92 | a+0.110, e-0.035, p+0.052 | y |  | fixture-piano-ch1-p0 67@319.769298 |
| 319.769 | phrase reseed | RESEED | 570 | 92 | a+0.110, e-0.035, p+0.052 | y |  | fixture-piano-ch1-p0 67@319.769298 |
| 319.769 | cue reseed-entry | RESEED | 570 | 92 | a+0.110, e-0.035, p+0.052 | y |  | fixture-piano-ch1-p0 67@319.769298 |
| 345.575 | cue reseed-accent-a | RESEED | 616 ↓ | 92 | a+0.001, e+0.001, t+0.292 | y |  | fixture-piano-ch1-p0 60@345.575241 |
| 381.479 | cue reseed-accent-b | RESEED | 680 ↓ | 92 | a+0.002, t+0.391 | y |  | fixture-piano-ch1-p0 61@381.479162 |
| 433.091 | movement SIGNATURE | SIGNATURE | 772 ↓ | 24 | d-0.920, a-0.715, e-0.118, s-1.000, p-0.550, t-1.600 | y |  | fixture-piano-ch1-p0 62@433.091049 |
| 433.091 | phrase signature | SIGNATURE | 772 ↓ | 24 | d-0.920, a-0.715, e-0.118, s-1.000, p-0.550, t-1.600 | y |  | fixture-piano-ch1-p0 62@433.091049 |
| 433.091 | cue signature-entry | SIGNATURE | 772 ↓ | 24 | d-0.920, a-0.715, e-0.118, s-1.000, p-0.550, t-1.600 | y |  | fixture-piano-ch1-p0 62@433.091049 |

## Declared structural boundaries that do not perturb the image

These land exactly on their declared time without a measurable image delta —
the choreography only moves what each movement declares.

| t (s) | boundary | movement | beat | dynamic | audio notes |
|---|---|---|---|---|---|
| 0.000 | movement ONE | ONE | 0 ↓ | 48 | fixture-piano-ch1-p0 60@0 |
| 0.000 | phrase origin | ONE | 0 ↓ | 48 | fixture-piano-ch1-p0 60@0 |
| 0.000 | cue origin-entry | ONE | 0 ↓ | 48 | fixture-piano-ch1-p0 60@0 |

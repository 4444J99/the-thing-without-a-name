# Score → motion A/B — observable frames (2026-08-05)

The numeric A/B receipt proves the score moves the image in state
arithmetic. This renders the actual frame at each declared boundary,
WITH the score and WITHOUT it, at the same absolute time, and measures
the pixel difference. Every number here is a picture first: the contact
sheet shows the pair, and the PSNR is the number under it.

- score contract: `f58a3a29a6fed5ecaa2e8580553841c852ad1d7aa63b16f054c5be08ffd580a4`
- seed: `0x12345678`, stream: `0`, passage: 0 (t0=0s)
- tier `screen` at 1024×768 on ANGLE (Apple, ANGLE Metal Renderer: Apple M5, Unspecified Version)
- contact sheet: `score-to-motion-frames.png` (sha256 `a1ebffca46971aaa`)
- determinism: the WITH frame at t=0.0s rendered in a fresh process is **byte-identical** (`3dee5ccde650` vs `3dee5ccde650`). The instrument reports real differences or nothing; a rerun of the same input proves it.

## Why there is no identical control

The A/B numeric receipt samples the six camera channels and records `score_delta` as their difference. It never samples `material`. The score changes `material` (the plates drawn) even where all six camera channels are flat — at t=0 the pair legitimately differs by ~14.6 dB. So WITH vs WITHOUT is never the identity check; the determinism re-render is. The material coupling is itself part of what the score contributes, and it is visible in the sheet at every row.

## Boundary pairs

`Δmax` is the largest of the six camera `score_delta`s at that boundary;
PSNR is measured on the actual WITH vs WITHOUT pixels.

| t (s) | boundary | movement | Δmax | PSNR (with vs without) |
|---|---|---|---|---|
| 0.000 | origin ONE | ONE | 0.000 | 10.7 dB |
| 44.880 | movement ASSEMBLY | ASSEMBLY | 0.042 | 10.8 dB |
| 72.930 | movement DIVISION | DIVISION | 0.086 | 25.4 dB |
| 134.640 | movement PHRASE | PHRASE | 0.142 | 37.4 dB |
| 143.616 | cue phrase-accent-a | PHRASE | 0.176 | 13.7 dB |
| 148.104 | cue phrase-accent-b | PHRASE | 0.241 | 14.3 dB |
| 152.592 | cue phrase-accent-c | PHRASE | 0.314 | 14.2 dB |
| 252.449 | movement STILLNESS | STILLNESS | 0.113 | 13.8 dB |
| 319.769 | movement RESEED | RESEED | 0.052 | 15.2 dB |
| 345.575 | cue reseed-accent-a | RESEED | 0.279 | 14.6 dB |
| 381.479 | cue reseed-accent-b | RESEED | 0.367 | 13.8 dB |

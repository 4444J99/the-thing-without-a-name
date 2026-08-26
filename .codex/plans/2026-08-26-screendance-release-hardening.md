# ScreenDance 2027 release + public-surface hardening

Branch: `work/screendance-2027-hardening`

## Objective

Finish the repository-owned release path for *The Thing Without a Name* and derive every public artifact from tracked, verified project assets. No generated substitute imagery and no public claim without a repository source or an explicit artist confirmation.

## Non-negotiables

- Preserve the engine invariants in `AGENTS.md`.
- Use topic-branch + pull-request delivery.
- Do not commit raw/private photographs, private recordings, generated submission packages, credentials, or personal paths.
- The tracked derivative corpus and the repository's existing reference/release images are the only visual sources for public collateral.
- A capture is one passage, never the unbounded work.
- Do not claim submission, selection, festival affiliation, adoption, or rights clearance until a durable receipt exists.

## Verified repository-owned visual sources

- `reference/T-2017-full.png` — 2017 composite.
- `reference/reconstruction-comparison.png` — original / reconstruction / source-map comparison.
- `reference/score-2017-provenance.png` — score provenance.
- `reference/projection-probe.png` — projective-texturing proof.
- `release/frames/score-to-motion-frames.png` — repository-generated score-to-motion frame sheet.
- `release/frames/score-to-motion-frames.json` — frame manifest.

## Workstreams

### 1. Claim and lineage audit

- Remove or qualify the unsupported exact source-frame count in `README.md`.
- Reconcile all public claims against `LINEAGE.json`, `corpus/manifest.json`, `corpus/score-2017.json`, tracked receipts, and artist confirmation.
- Distinguish measured reconstruction claims from claims about the original hand process.
- Keep the canonical call facts in `submission/screendance-2027.yaml`; do not duplicate deadline/spec authority elsewhere.

**Done when:** public-facing prose contains no unsupported source-frame count and every quantitative statement has a repository owner.

### 2. Public site audit

- Inspect `index.html`, `film.html`, styles, renderer state, mobile behavior, project explanation, source links, accessibility, and the distinction among river / passage / capture / room.
- Compare the live GitHub Pages surface with the current repository head.
- Make the original composite and the transition from hand process to engine legible without turning the artwork into a technical brochure.

**Done when:** the public surface explains the work accurately, remains artistically coherent, and passes the portable verification batch.

### 3. Repository-native LinkedIn collateral

- Add an in-repo composition script that creates a LinkedIn landscape image from the real tracked assets above.
- Target a platform-safe landscape canvas; keep all critical text inside a conservative safe area.
- The visual should privilege the actual artwork: composite → measured reconstruction/provenance → real engine capture.
- Keep copy minimal; the post body and comments carry the argument.
- Generate alt text from the exact final asset composition.

**Done when:** the social image can be reproduced from a clean checkout, has an asset/claim manifest, and contains no synthetic dancer imagery or fabricated project views.

### 4. ScreenDance film/package

- Lock passage seed, camera path, runtime, sound, title, and final frame.
- Build master, screener, stills, origin still, text package, rights declaration, and attestations according to `submission/screendance-2027.yaml`.
- Resolve or consciously accept every `unstated` field.
- Keep application status explicit: draft / package / uploaded / submitted.

**Done when:** `./done.sh --package <package-root> --phase package` passes and the human has approved the final cut and rights declaration.

### 5. Verification and delivery

Run once per exact tree:

```bash
python3 scripts/check-danse.py
python3 scripts/tests/danse-delivery.test.py
python3 -m py_compile render/*.py pipeline/*.py sound/*.py submission/*.py scripts/*.py
node --check sound/control.mjs
bash -n done.sh
```

Run the machine-bound visual batch on the appropriate macOS/Chrome/Metal host:

```bash
python3 render/browser.py --check --verify --arrival --probe
```

Open a pull request with:

- changed-claim inventory;
- before/after public-surface captures;
- generated social-asset manifest;
- portable verification receipt;
- unresolved human gates.

## First pass order

1. Audit and correct `README.md` origin language.
2. Inventory actual site/reference/release assets and current render paths.
3. Audit the live public surface against `main`.
4. Build the repository-native LinkedIn composition script.
5. Continue the final film and package path.

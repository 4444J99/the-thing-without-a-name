# Release manifest and public-project framework

`manifest.json` is the single source for the project page, installation pitch,
accessibility materials, captions/transcript, press copy, credits, posting plan,
and release-media inventory. It consumes frozen opportunity snapshot
`omega-20260804` at SHA-256
`3aeb84a8c919c6866272138e3ce1bd7d9222af77ad618d810ba69f6159544b3e`,
frozen at `2026-08-04T02:32:44Z`, and binds its source-evidence manifest at
`0b28e98f151e8ea940c6c047fff91afc79f6b0df0343e623da79952b9aa87c37`.
The snapshot also binds the complete ScreenDance YAML consumer contract; a release
build never fetches changing call terms.

The manifest has three cumulative phases:

- `draft` validates all existing bytes and emits visibly marked, `noindex` local
  review artifacts while named human and external gates remain open;
- `public` additionally requires public approval, verified claims and technical
  requirements, cleared credits and media, an approved contact route, and final
  accessibility material; and
- `release` adds independent custody, restore rehearsal, and actual presentation
  evidence.

Current tracked state is intentionally `draft`. `public` and `release` fail before
creating an output directory. A passing draft is not permission to publish.

## Validate and build

```bash
python3 scripts/check-release.py --phase draft --list-gates
python3 scripts/tests/release-manifest.test.py

release_output="$(mktemp -d)/danse-release"
python3 scripts/build-release.py \
  --output "$release_output" \
  --phase draft \
  --source-commit "$(git rev-parse HEAD)"
python3 scripts/build-release.py \
  --verify "$release_output" \
  --phase draft \
  --source-commit "$(git rev-parse HEAD)"
```

The output contains:

- `project/index.html`
- `pitch/danse-installation-pitch.pdf`
- accessibility summary, WebVTT captions, and transcript
- press kit, public credits, and a non-sending posting calendar
- release-media inventory and only media whose source digest and clearance both pass
- `release-build.json`, which digests every delivered byte and binds the exact source
  commit, release manifest, opportunity snapshot and receipt, source-evidence manifest,
  phase, and version

The builder accepts only an absent or empty output outside the repository, rejects
symlinks and path traversal, sets deterministic file timestamps, and reproduces the
same PDF and other bytes for the same manifest, phase, dependency version, and source
commit. Generated outputs are not committed from the draft phase.

## Closing a gate

Do not change a status alone. A completed claim, credit, medium, or gate names a
tracked public-safe evidence file and its SHA-256. Ready media also names an exact
source path, SHA-256, byte count, public destination under `media/assets/`, and
accessible description. Its ID and phase scope must match `rights/register.json`,
and its clearance must satisfy the typed rights/production receipt contract.
Private releases, signatures, contacts, raw media, package roots, and credentials stay
in their owning custody; only their redacted receipt can be referenced here.

Before changing `status` to `public-approved` or `released`:

1. Replace draft/pending language with approved factual copy.
2. Bind the exact #10 cut, #14 room evidence, and #16 redacted rights register.
3. Resolve caption/transcript applicability against the final media.
4. Run `check-release.py` at the intended phase and build twice byte-identically.
5. Render and visually inspect every pitch PDF page.
6. Record human publication approval separately; the builder never deploys, posts,
   sends outreach, creates accounts, pays fees, or accepts terms.

## Publication boundary

The root GitHub Pages artifact remains the immersive artwork. `scripts/build-pages.py`
does not include `release/` or `project/`; the Pages regression asserts both stay out.
The generated `/project/` route may enter a future Pages allowlist only after the
`public` predicate succeeds and a separately reviewed deployment change binds its
artifact receipt.

# Rights and attribution contract

`register.json` is the public-safe inventory for issue #16. It binds the exact
photographic corpus, layered music register, pose-vendor bundle, ScreenDance terms,
package text, people, pictured objects, recordings, fonts, textures, stills, software,
installation evidence, credits, and any later third-party material. Its interchange
shape is `register.schema.json`; its executable semantics live in
`scripts/rights_contract.py`.

The tracked register contains no release, signature, contact, raw-media location,
credential, or private evidence. Those remain in external custody. A completed gate
may cite only a tracked redacted receipt and its SHA-256. Pending prose is not evidence,
ownership of a photograph does not clear a depicted person or object, public-domain
composition does not clear a recording, and one use never silently clears another.

Run the portable draft predicate with:

```bash
python3 scripts/check-rights.py --phase draft
python3 scripts/tests/rights.test.py
```

Draft success means the inventory, source digests, redaction boundary, license bundle,
and rule graph are sound. It does **not** mean the artwork is cleared. Shipping phases
fail closed until their exact inputs and human gates exist:

```bash
python3 scripts/check-rights.py --phase package --package <package-root>
python3 scripts/check-rights.py --phase submitted --package <package-root>
python3 scripts/check-rights.py --phase public --release-manifest release/manifest.json
python3 scripts/check-rights.py --phase release \
  --package <package-root> \
  --release-manifest release/manifest.json \
  --receipt <redacted-receipt.json>
```

Every non-draft receipt records the canonical `America/New_York` validation date and
rechecks fixed-term permissions against that shipping date, including permissions
reached through an active artifact rule. The timezone name is loaded from the canonical
submission register and must agree with its offset-bearing hard wall. The package
checker verifies every manifested byte and byte count, requires the master, screener,
score source, generated-still minimum, and origin still declared by that same register,
then repeats the complete package inventory after validation. It also binds the declared
corpus tier against the package builder's current source-tree identity, every derivative
selected by the canonical Pages corpus allowlist against one file-count and tree-digest
binding, the copied score-source
WAV and hydrated grain-bank identity, registered origin still, every copied text file's
manifest row, rights-bearing unmanifested files, symlink boundaries, and package rights
rules. The release checker requires the exact rights-register digest; a complete
inventory of the active phase's `media/assets/` boundary with no extra, symlinked, or
off-phase bytes; every media and public-copy byte at its declared destination; stable
manifest and media identities rechecked after inventory; clearance evidence; and credit
text identical to the approved attribution label. Every cleared release-media row cites
a tracked `danse.rights.media-clearance.v1` receipt binding its media id, canonical
destination, SHA-256, byte count, authority, decision, and phase scope. A `satisfied`
human gate must cite a tracked `danse.rights.decision.v2` receipt binding that exact
gate, authority, decision, phase scope, credited asset, and approved wording. Every
cleared asset use likewise cites a tracked
`danse.rights.use-decision.v1` receipt binding that asset, use, rights holder, medium,
phase scope, territory, term, promotion, and archive grant. Every manual or choice
assertion in the canonical submission specification must map to a phase-owning human
gate with the same typed value contract. Package attestations may satisfy only package,
uploaded, and submitted gates; their registered canonical values are bound into the
redacted phase receipt, and they never replace durable public/release receipts. Public
receipts, register prose, and release manifests reject absolute machine paths on POSIX
and Windows as well as contacts, credentials, and sensitive fields. Release manifests
must use either the compact closed interchange shape or the full closed release schema;
unknown top-level or media, clearance, credit, gate, source, and evidence fields fail.
The Pages workflow runs the public phase before artifact upload, and the artifact builder
copies every ready, cleared public release destination into its digest allowlist while
excluding release-only rows and the manifest itself. An uncleared release cannot publish
a new deployment.
Malformed graph references, regular expressions, credit dependencies, and fixed-term
dates become explicit blockers rather than tracebacks. Receipt output is rejected before
phase validation whenever it would overlap the register, schema, a bound source, a
package tree, a release manifest, or the staged release-media boundary.

Current state is deliberately `draft`. Anthony must still approve the final cut,
biography, submission copy, and rights declaration; settle dancer permission and credit;
disposition the poster wall; select and clear repertoire and press stills; make the
archive choice; and personally accept filing terms. Do not change a status alone—add the
exact public-safe evidence receipt and rerun the intended phase.

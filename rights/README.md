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

The package checker verifies every manifested byte and byte count, the declared corpus
tier against the package builder's current source-tree identity, the copied score-source
WAV and hydrated grain-bank identity, registered origin still, every copied text file's
manifest row, rights-bearing unmanifested files, symlink boundaries, and package rights
rules. The release checker requires the exact rights-register digest; every media and
public-copy byte staged at its declared `media/assets/` destination; clearance evidence;
and credit text identical to the approved attribution label. A `satisfied` human gate
must cite a tracked `danse.rights.decision.v1` receipt binding that exact gate, authority,
decision, and phase scope. Package attestations may satisfy only package, uploaded, and
submitted gates; their registered canonical values are bound into the redacted phase
receipt, and they never replace durable public/release receipts. The Pages workflow runs
the public phase before artifact upload, so an uncleared release cannot publish a new
deployment.

Current state is deliberately `draft`. Anthony must still approve the final cut,
biography, submission copy, and rights declaration; settle dancer permission and credit; disposition the
poster wall; select and clear repertoire and press stills; make the archive choice; and
personally accept filing terms. Do not change a status alone—add the exact public-safe
evidence receipt and rerun the intended phase.

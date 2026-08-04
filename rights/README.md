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

The package checker verifies every manifested byte, byte count, source-tree identity,
sound source and score identity, registered origin still, copied biography and rights
declaration, rights-bearing unmanifested file, symlink boundary, and package rights
rule. The release checker requires the exact rights-register digest, release media,
clearance evidence, and approved credits. Package attestations may satisfy only package,
uploaded, and submitted gates; they never replace durable public/release receipts.

Current state is deliberately `draft`. Anthony must still approve the final cut,
biography, submission copy, and rights declaration; settle dancer permission and credit; disposition the
poster wall; select and clear repertoire and press stills; make the archive choice; and
personally accept filing terms. Do not change a status alone—add the exact public-safe
evidence receipt and rerun the intended phase.

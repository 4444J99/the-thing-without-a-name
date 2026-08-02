# Canonical import receipt — 2 August 2026

This repository is the canonical public owner of Danse / **THE THING WITHOUT A
NAME**. The import is a path-preserving extraction of the publishable source from
`organvm/limen`, including the delivery hardening at Limen source head
`911410ba4c99a44368c823725561431e27354ec5`.

The import deliberately excludes raw photographs, private recordings, generated
render/package caches, credentials, and local machine paths. Those hydrate only
the ignored roots named by the code and remain in their private archival custody.

The executable import predicate is the portable batch in the root `AGENTS.md`:

```bash
python3 scripts/check-danse.py
python3 scripts/tests/danse-delivery.test.py
python3 -m py_compile render/*.py sound/*.py submission/*.py scripts/*.py
node --check sound/control.mjs
bash -n done.sh
```

`LINEAGE.json` is the machine-readable owner receipt. The originating delivery
work remains reviewable at <https://github.com/organvm/limen/pull/1762>; future
source changes and delivery work belong to this repository's pull requests and
issues.

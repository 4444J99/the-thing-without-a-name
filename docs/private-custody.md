# Private custody snapshots

`scripts/private_custody.py` creates a portable source bundle plus a byte-exact
archive of every ignored or untracked material file in one clean Git worktree.
It then copies the same immutable snapshot to a second physical device. It does
not remove, move, or rewrite the source.

The snapshot directory is private. Its manifest contains relative filenames and
must stay with the controlled custody media; it must never be committed or used
as a public receipt. The restore command emits a separate redacted receipt with
only opaque medium identities, source and remote commits, aggregate counts and
bytes, and artifact digests.

## Snapshot and duplicate

Fetch the relevant remote first, then use two existing directories on genuinely
independent physical media:

```bash
python3 scripts/private_custody.py snapshot \
  --source SOURCE_WORKTREE \
  --primary-root PRIMARY_CUSTODY_ROOT \
  --secondary-root SECONDARY_CUSTODY_ROOT \
  --snapshot-id PORTABLE_SNAPSHOT_ID \
  --remote-ref origin/BRANCH \
  --remote-mode equal
```

Use `--remote-mode ancestor` only for a deliberately retained historical commit
that is proven reachable from the named remote branch. `equal` is the default
custody expectation for an archive branch. The tool refuses a dirty tracked
tree, unsafe or unresolved remote reference, fetch/push remote mismatch,
escaping symlink, special file, destination collision, insufficient space, or
two destinations on the same physical device. On macOS, independence is derived
from the parent whole disk rather than the APFS volume alone.

An interrupted or failed snapshot remains under its hidden `.incomplete`
directory for inspection. The tool never deletes or resumes it and will not
overwrite it on a later invocation.

## Restore rehearsal

Restore from the second copy into a new directory on a clean target. The receipt
parent must already exist, and neither the restore target nor receipt may exist:

```bash
python3 scripts/private_custody.py restore \
  --primary PRIMARY_CUSTODY_ROOT/PORTABLE_SNAPSHOT_ID \
  --secondary SECONDARY_CUSTODY_ROOT/PORTABLE_SNAPSHOT_ID \
  --primary-id OPAQUE_PRIMARY_MEDIUM_ID \
  --secondary-id OPAQUE_SECONDARY_MEDIUM_ID \
  --target NEW_EMPTY_RESTORE_PATH \
  --receipt EXISTING_PRIVATE_RECEIPT_PARENT/redacted-receipt.json
```

The restore starts from the bundled Git commit, overlays only the private
inventory, rejects archive traversal and overwrite attempts, hashes every
restored file, checks the exact ignored/untracked census, and requires a clean
tracked diff. Both custody copies are re-hashed before extraction.

The generated receipt intentionally leaves `human_acceptance.ok` false and
`cleanup_authorized` false. A successful machine restore does not authorize
worktree reclamation. Issue #21 remains open until the redacted receipt is
reviewed, durably tracked, and explicitly accepted by the owner; issue #3 must
also finish the archived experiment dispositions before its retained worktree
can be reclaimed.

# Danse session closeout

Every repository-changing session must end with one durable, inspectable outcome:

1. a pushed commit and pull request whose exact head is recorded; or
2. an explicit no-change or blocker receipt on the owning issue.

An agent summary, local chat, unpushed worktree, or remembered conversation is not
a receipt. Conversation content that was never recorded cannot be reconstructed
later and must be classified as `not recorded`, never silently promoted into a
creative or legal claim.

Use only the convergence vocabulary `merged`, `active`, `archived`, `ported`,
`superseded`, `blocked`, and `not recorded`. A session is not terminal while it
is `active`. A blocker receipt names the missing authority or external state and
the executable predicate that will clear it.

Before removing a branch or worktree, record all of the following:

- its exact head and whether that head is reachable from the canonical remote;
- fetch/push remote parity and an empty tracked diff;
- an inventory of ignored and untracked data;
- the pull request, issue, or archive receipt that owns its outcome; and
- for material/private data, two independent checksum-verified copies, a
  clean restore rehearsal, and explicit owner acceptance.

Never delete, move, merge, or publish private custody merely to make the inventory
look closed. Archive branches are inputs to deliberate ports, not wholesale merge
candidates.

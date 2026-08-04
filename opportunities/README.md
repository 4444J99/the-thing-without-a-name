# Frozen opportunity registry

`omega-20260804.json` is the immutable source snapshot for the Alpha → Omega
release. It dispositions every target named in the tracked plan, records facts as
`verified`, `unstated`, `not-applicable`, or `conflicted`, and keeps every account
action, fee, agreement, and public send behind an explicit human gate.

The snapshot does not contact live sites during a build. Its checked-at evidence is
bound by `omega-20260804.receipt.json`; `submission/screendance-2027.yaml` consumes
that exact SHA-256 identity for issue #2, and issue #12 must cite the same identity
from the future release manifest. Run:

```bash
python3 scripts/check-opportunities.py
python3 scripts/tests/opportunities.test.py
```

Calls continue to change after this freeze. Do not edit the frozen snapshot to make
a later application current. Issue #22 owns a newly dated snapshot, new source
checks, and a new digest. A `deadline_at` on a source that publishes only a date is
an explicitly conservative scheduling boundary; the accompanying deadline fact
states that limitation and a human confirms the portal cutoff before sending.

# Deploy-ops release admission contract (2026-10-08)

This is a **documentation-only, deny-by-default** contract. It does not authorize a production mutation, workflow dispatch, merge, runner change, queue or SQLite write.

## Current gate

- Treat #580/PWQ-41 and successor #1089 as a **single serialized production release barrier**: while either lacks current-main terminal/release evidence, production admission is denied.
- #1135 is a separate dashboard-recovery safety dependency. PR-triggered and healthy-dashboard recovery must prove zero privileged operations.
- #1108 retains exclusive ownership of its nine metadata/planner/preflight/intent paths; PWQ-258 remains Worker 2 scope.
- #1160 and #1164 describe unresolved exact-SHA status binding and complete PR-less pagination gaps. Neither tracking issue authorizes implementation on an occupied path.

## Pre-admission evidence tuple

All evidence must refer to the **same immutable 40-hex main commit SHA** and a bounded observation window. The tuple consists of:

1. Canonical main SHA before inventory, and unchanged canonical main SHA after every inspection.
2. Both serialized gate owners terminal/released, with explicit owner handoff and no conflicting active production writer.
3. Complete, uncapped, cursor-exhausted open-PR inventory **and** PR-less branch inventory; changed-path overlap checked on the same main SHA.
4. Candidate head SHA, non-draft status, owner assignment, changed-path set and fresh exact-head successful test/regression evidence.
5. Dashboard-recovery safety evidence: untrusted PR or healthy external dashboard cannot execute service, SQLite or other privileged recovery mutations.
6. Production preflight exact-head status and a separate, bounded post-deploy health/rollback receipt, only after gates actually release.

## Deny cases (independent)

- Missing, malformed, stale or contradictory SHA/status metadata.
- Pagination incomplete, repeated cursor, API error, moving main, stale PR head or unaccounted PR-less branch.
- Successful tests from a prior head only, ambiguous review/ownership, overlapping paths or draft PR.
- Untrusted event/actor/ref, absent authorization, still-open serialized gate, healthy dashboard with proposed recovery, or missing recovery rollback proof.
- Any unavailable or unknown evidence: **deny**; never substitute hosted CI for live production proof.

## Safe verification sequence

Read exact main; inventory both PRs and all branch pages; establish ownership disjointness; re-read main. Verify final candidate head and checks, then re-read main again. Record an evidence-only decision with explicit reason codes. Do not dispatch, merge or deploy from this document. A new independent release decision is required after every change of main or candidate head.

Related: [#1009](https://github.com/Zennay/zCloud/issues/1009), [#1135](https://github.com/Zennay/zCloud/issues/1135), [#1160](https://github.com/Zennay/zCloud/issues/1160), [#1164](https://github.com/Zennay/zCloud/issues/1164).

# Deploy-ops gate release decision record (non-authorizing)

This is a **manual evidence template**, not an authorization to merge, deploy, alter runtime state, cancel CI or rewrite PR metadata. Project phase: Accelerate → Verifying.

## Immutable snapshot

- Observation time (UTC): NOT CAPTURED
- Canonical main SHA: NOT CAPTURED
- Gate #580/PWQ-41 state, head, owner and latest check URLs: NOT CAPTURED
- Successor #1089 state, head, owner and latest check URLs: NOT CAPTURED
- Other serialized deployment writers / PR-less branch owners: NOT CAPTURED
- PWQ-258 / Worker 2 owner confirmation: NOT CAPTURED

**Default disposition: HOLD.** Blank, incomplete, mismatched, stale or indeterminate evidence means HOLD. A closed former gate #576 does not substitute for #1089.

## Evidence required before any integration candidate is considered

1. Observe #580 and #1089 in a fresh snapshot and independently confirm they have both relinquished the serialized writer/deploy window. Closed PR alone is not proof of production release.
2. Pin canonical main to an exact SHA; rerun successor-aware backlog inventory #1098 and deterministic wave planning #1099 against that same SHA.
3. Capture the complete open-PR changed-file ownership set **and** PR-less unmerged branch ownership; unresolved overlap means HOLD.
4. Require review-ready, non-draft, current-main candidate head, exact-head required validations and explicit owner handoff. Earlier head green does not transfer.
5. Verify #1100 metadata-drift audit and #1108 immutable-intent + preflight evidence refer to the same candidate identity. The #1108 stack is read-only and never confers merge or deploy rights.
6. Release only one bounded, conflict-free integration batch under the authorized owner; after any merge, invalidate previous main-head evidence and reacquire gate, checks, ownership and production receipts.
7. Require a guarded post-integration production receipt before declaring completion; any missing receipt means HOLD.

## Explicit failure-mode decisions

| Observed condition | Decision | Required next evidence |
| --- | --- | --- |
| Either #580 or #1089 remains active, or its release owner is ambiguous | HOLD | Two independent terminal/relinquishment receipts |
| #576 is closed but #1089 status is unknown | HOLD | Direct successor #1089 observation |
| Candidate check is green only on a previous commit | HOLD | Exact candidate-head complete validation |
| Main advances after inventory or batch selection | HOLD | New main-bound inventory, ownership and check set |
| PR-less branch inventory is truncated or unavailable | HOLD | Complete unmerged-branch ownership snapshot |
| Two candidates edit the same path | HOLD | Explicit owner arbitration and isolated new batch |
| #1108 preflight identity differs from immutable intent | HOLD | Matching PR number, head SHA and body digest |
| Post-integration production receipt missing or failed | HOLD | Guarded production verification owned by release lane |

These are necessary checks, never sufficient authorizations. No automated action may interpret an all-clear table as a deploy capability.

## Evidence freshness and non-replay rule

Record a single observation timestamp plus the canonical main SHA and candidate PR number/head/body digest. Any change to main, a candidate head/body, a gate PR's state or its owner invalidates that decision record. The next reviewer must start a **new** record instead of editing historical evidence to make it appear current. Preserve links to historical CI successes as history only, clearly marked with their tested SHA.

## Decision

- Gate disposition: HOLD (default)
- Reviewer / authorized owner: UNASSIGNED
- Exact evidence links: NONE
- Authorized batch identities: NONE
- Production operation requested: NO
- mutation_performed: false
- merge_authorized: false
- deploy_authorized: false

Never use this document as a workflow input or machine-readable capability token. It intentionally contains no executable instructions, tokens, webhook endpoints, deployment commands, or live target addresses.

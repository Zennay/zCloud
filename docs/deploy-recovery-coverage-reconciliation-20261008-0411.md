# Dashboard recovery: duplicate offline-coverage reconciliation (2026-10-08)

Tracking: [#1196](https://github.com/Zennay/zCloud/issues/1196). This is a **review input**, not an ownership grant or an admission decision.

## Observed candidate owners

| Candidate | Claimed scope | Review decision |
| --- | --- | --- |
| [#1189](https://github.com/Zennay/zCloud/pull/1189) | deny-first offline authorization simulation: event/actor/ref, SHA, approval, health, evidence, concurrence | Candidate canonical *authorization-matrix* owner; compare tests and latest head before selection |
| [#1192](https://github.com/Zennay/zCloud/pull/1192) | independent offline recovery authorization fixtures, overlapping #1189 | Duplicate candidate; avoid merging both without case-by-case gap proof |
| [#1143](https://github.com/Zennay/zCloud/pull/1143) | workflow quarantine (recover unconditional skip per #1177) | Actual workflow owner; simulation is NOT permission to enable recovery |
| [#1194](https://github.com/Zennay/zCloud/pull/1194), [#1195](https://github.com/Zennay/zCloud/pull/1195) | health-observation documentation | Supporting evidence only; not authorization |
| [#1187](https://github.com/Zennay/zCloud/pull/1187) | read-only incident triage | Supporting guide only; no live remediation |

## Required acceptance matrix before any promotion

Use case IDs to reconcile existing modules rather than creating yet another overlapping module.

- REC-01: pull_request and push trigger are rejected.
- REC-02: manual dispatch lacks independently recorded approval, deny.
- REC-03: untrusted actor or noncanonical repository/ref, deny.
- REC-04: missing/invalid/stale head SHA, deny; SHA must match immutable main evidence.
- REC-05: expired/missing approval and mismatch of approval to run attempt, deny.
- REC-06: either #580/PWQ-41 or #1089 gate closed, deny.
- REC-07: externally healthy dashboard, deny recovery (an internal transient 503 alone is insufficient).
- REC-08: unknown VPS health, missing or partial evidence, deny.
- REC-09: overlapping recovery in flight, deny.
- REC-10: missing run/request/receipt correlation, deny.
- REC-11: simulated approved case produces a bounded mock plan only; no shell, SSH or service calls.
- REC-12: on both PR and manual workflow dispatch, quarantined recover job remains skipped and external verification is hosted/read-only.

## Evidence still required (not asserted here)

1. Complete **paginated** open PR and PR-less branch inventory on one immutable main SHA; capture paths, ownership and remaining cursor. The initial branch listing returned a non-null cursor, so first-page results cannot prove an unowned path.
2. Inspect actual test methods in #1154, #1181, #1189, #1192 and evidence from #1171/#1172; mark each REC case pass, missing or duplicate with exact head SHA. The table above is based on candidate PR descriptions, not verified test execution.
3. Obtain hosted tests pinned to selected PR SHA and full zCloud regression; check #1143 exact-head workflow evidence.
4. Obtain #1143 and serialized gate-owner review. No workflow merge, dispatch, recovery or production mutation from this document.

**Default outcome: NOT ADMITTED.** CI green or documentation alone cannot unlock #580/PWQ-41 + #1089.

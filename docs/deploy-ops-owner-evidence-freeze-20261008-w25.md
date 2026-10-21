# Deploy-ops owner handoff: evidence freeze checklist (2026-10-08, w25)

This document is **non-authorizing**. It does not record a successful release, grant deployment access, or permit a mutation of production, a VPS, workflows, queues, workers, or SQLite.

## Purpose

Prevent concurrent deploy-ops proposals from accidentally being treated as independent production release approvals. The active serialized admission owners remain #580/PWQ-41 and #1089 until explicit documented handoff.

## Snapshot, not an exhaustive inventory

At 2026-10-08 04:26 UTC, an open-PR search displayed #1203 (terminal attempt evidence), #1202 (receipt identity), #1201 (recovery reconciliation), #1200 and #1197 (exact-SHA statuses), #1199 (approval replay), #1198 (environment approval), and #1195 (dashboard observations), among others. This is a partial result set; it **does not** establish that PR-less branches or further pages are unowned.

## Required evidence before adopting any deploy-ops proposal

1. Obtain a **paginated** open PR and branch inventory; record search cursor exhaustion and specific file-path overlaps. If incomplete, ownership is **unknown**, not free.
2. Pin canonical `main` SHA and candidate PR head SHA immediately before validation; record both, the time, and the operator.
3. Check overlapping proposals for duplicate coverage; reconcile behavioral contracts rather than assuming disjoint filenames imply disjoint functionality.
4. Execute candidate-specific tests on the **exact** candidate head on a trusted runner. Treat queued, skipped, neutral, cancelled, stale, or mismatched-SHA runs as non-evidence.
5. Recheck canonical `main` SHA after tests. If either head or main has changed, repeat the affected validation.
6. Require full regression at an approved integration SHA and explicit serialized owner signoff before considering any production action.
7. Independently establish production preflight, health, environment approval, rollback capability, and immutable deployment receipt. An offline helper returning true is not release authority.
8. Report each failed or unavailable gate as **not accepted**; do not infer success from absence of a failure report.

## Negative cases

| Evidence observed | Release conclusion |
| --- | --- |
| Test passes for a previous head SHA | Not accepted |
| One open-PR page with more branches possible | Ownership unknown |
| Environment protection approved but no signed-off serialized gate | Not accepted |
| Successful offline fixture without hosted exact-head run | Not accepted |
| Deployment receipt with unmatched run attempt or SHA | Not accepted |
| Internal health failure with healthy external dashboard and no incident decision | Observe; not automatic restart authorization |
| Parallel owner touching #580/#1089 live surface | Stop competing writes; require explicit handoff |

## Scope

This file is a review aid only. No live action was taken as part of authoring it. Its presence must never be interpreted as completion of an acceptance gate.

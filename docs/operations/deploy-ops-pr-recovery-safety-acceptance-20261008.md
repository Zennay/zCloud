# PR-triggered dashboard recovery: deploy-ops acceptance boundaries

Date: 2026-10-08. Scope: **documentation only**, independent of the existing dashboard-recovery workflow/audit owner.

## Current incident and non-claims

- Issue [#1135](https://github.com/Zennay/zCloud/issues/1135) records a failed recovery job alongside a successful independent external dashboard verification. A failed recovery job does **not** establish dashboard unavailability.
- Existing `.github/workflows/zcloud-dashboard-access-recovery.yml` may invoke privileged recovery for pull requests. The workflow owner must resolve this; this document does not authorize an edit or dispatch.
- The serialized production gate is #580/PWQ-41 **and** #1089. Both must be conclusively released at the same current-main SHA before any production repair. A stale or missing gate result means deny.

## Independently verifiable acceptance matrix

| Invocation / evidence | Allowed action | Required proof |
| --- | --- | --- |
| Pull request, any author | Read-only external/hosted verification | Zero self-hosted privileged steps, `sudo`, service/DB writes and runner-control actions |
| Healthy external dashboard | No repair | External probe result bound to run; zero stop/restart or SQLite/filesystem changes |
| Untrusted repository/ref/actor | Deny | Canonical repository, protected ref, actor, exact main SHA checks fail closed |
| Current main changed after admission | Deny and re-evaluate | Admission SHA equals execution SHA; stale acceptance never reused |
| #580 or #1089 active/unknown | Deny | Same-snapshot gate inventory, no inference from old green checks |
| Missing privilege or guarded runner identity | Deny safely | No fallback to generic self-hosted, no partial repair |
| Explicit approved unhealthy recovery | Bounded repair only | Exact-main trusted dispatch, verified need, exclusive owner, rollback plan and redacted receipt |
| External check healthy but recovery job fails | Classify as recovery-path failure | Never report an outage solely from the failed recovery job |

## Evidence bundle before owner admission

Record only repository, workflow/run ID, trigger, head/main SHA, check conclusions, bounded probe state, decision code, serialized-gate IDs, owner approval, rollback confirmation and receipt. Do not include secrets, request payloads, service journals, process argv, environment values, or arbitrary SQLite rows.

The release review must establish independent hosted regression, guarded read-only VPS diagnostic, zero-mutating PR behavior, explicit healthy-noop behavior, and exact-final-head checks. No test should run live repair merely to gain a green check.

## Ownership and handoff

- Dashboard workflow remediation is tracked by #1135; first reconcile existing `audit/deploy-dashboard-recovery-mutation-boundary-20261008-w1` and other open owners. This document does not claim their code paths.
- #1108 owns its nine planner/preflight/immutable-intent paths. PWQ-258 is Worker 2. Do not modify either.
- #1009 coordinates integration only after release gates; this document neither merges nor permits service, browser, SQLite, runner, queue, workflow or production mutations.

**Release authorization: none.** This is an additive, non-executable checklist. Every missing or contradictory signal blocks mutation and escalates to the owning release reviewer.

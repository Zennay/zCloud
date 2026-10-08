# Dashboard recovery: deploy-ops admission matrix (2026-10-08)

> Non-authorizing, read-only handoff. This document does not release a gate, dispatch a workflow, authorize sudo, or claim ownership of the dashboard recovery workflow.

## Why this exists

Issue #1135 identified a critical distinction: a public dashboard can be healthy while a PR-triggered `recover` job fails after attempting privileged VPS operations. External health success is not permission to restart services or modify SQLite. Keep the active owner of `audit/deploy-dashboard-recovery-mutation-boundary-20261008-w1` and the #1108 nine-path stack undisturbed.

## Admission invariants

1. **PR events are observational only.** No PR event is permitted to stop, restart, reload or reconfigure services, edit SQLite ownership/modes/flags, write systemd drop-ins, or invoke privileged recovery.
2. **Healthy means no-op.** Successful independent external verification must always result in zero recovery mutations, regardless of whether a separate verification job is red.
3. **Untrusted context fails closed.** Noncanonical repository, non-main SHA/ref, untrusted actor, unknown runner identity, or stale/incomplete evidence must not reach privileged recovery.
4. **Serialized writer authority is separate from a green CI check.** Require explicit current-main evidence releasing *both* #580/PWQ-41 and #1089, plus exclusive bounded production-window admission. #576 was retired unmerged and cannot authorize anything.
5. **Evidence must bind to one immutable revision.** Record canonical main SHA, candidate SHA, triggering event/ref/actor, checked gate versions, timestamp and read-only verifier outcome. If main advances, restart admission.
6. **Recovery requires demonstrated unhealthiness.** The privileged path should be a distinct, explicitly approved trusted-main/manual operation, with bounded actions, rollback plan and sanitized receipt. An unconditional stop is forbidden.
7. **Do not leak runtime secrets.** Diagnostics may show bounded status codes and redacted commands, never credentials, full environment, database contents or arbitrary journal output.

## Negative/positive acceptance cases

| Case | Expected result | Mutation count |
| --- | --- | --- |
| Ordinary PR opened/updated, dashboard healthy | Hosted/read-only verification; no recovery | 0 |
| Ordinary PR opened/updated, dashboard unhealthy | Report unhealthy; no recovery | 0 |
| Trusted main, dashboard externally healthy | Verify success; do not restart | 0 |
| Noncanonical repository or untrusted actor | Reject before runner privilege path | 0 |
| Old main SHA or mixed-head gate evidence | Reject, require exact-main refresh | 0 |
| #580/PWQ-41 active, unknown or #1089 active/unknown | Deny production admission | 0 |
| Missing approval, runner identity or rollback record | Deny recovery | 0 |
| Trusted exact-main, unhealthy, both gates released, exclusive window and approval recorded | Permit *bounded* separate recovery; collect redacted receipt and post-check | Limited to approved actions |
| Privileged recovery reports failure but external verification succeeds | Mark recovery check failed separately; **do not** infer dashboard outage or retry blindly | No repeat mutation |

## Evidence required before production release

- Exact-current-main open-PR file-ownership inventory **and** complete PR-less unmerged-branch ownership inventory; any unknown overlap is a stop.
- Focused contract-test proof of every case above on the same exact candidate head.
- Hosted full regression and independent **read-only** external dashboard verification for that same head; distinguish verifier result from recovery job result.
- Release-owner approval, explicit both-gates-released receipt, unchanged main SHA, and bounded rollback/incident plan immediately before any actual operation.
- Post-action health verification and a sanitized receipt; if preconditions drift, return to read-only diagnosis.

## Boundaries and handoff

This matrix is for reviewers of #1135 / #1009. It is **not** a replacement for the issue owner, not a green deployment certificate, and not a claim on `.github/workflows/zcloud-dashboard-access-recovery.yml`, #1108's nine files, or PWQ-258/Worker 2. A subsequent implementation owner should translate these cases into executable tests only after a fresh non-overlapping ownership check.

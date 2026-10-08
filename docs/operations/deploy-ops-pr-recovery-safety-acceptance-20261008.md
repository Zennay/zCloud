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

## Exact main workflow review (2026-10-08)

Read-only inspection of `.github/workflows/zcloud-dashboard-access-recovery.yml` on `main@b8bd568435390eebc4d47fae4c4c2d749fe4a90b` identified these **current** contract gaps:

1. `on.pull_request.branches: [main]` exists while the `recover` job uses the generic `self-hosted` runner with no event/ref/actor admission condition.
2. Recovery invokes `sudo systemctl stop` before proving the dashboard needs recovery; a healthy dashboard can therefore be interrupted by verification traffic.
3. The recovery path changes SQLite ownership and modes, clears immutable bits, runs a write transaction, restarts the service and may install a systemd drop-in. These are privileged production mutations, not read-only probes.
4. `external_verify` is already a separate `ubuntu-latest` job. Its success cannot be used to bless the privileged recovery path; its failure alone cannot authorize repair.
5. A dispatch trigger alone is not proof of trusted actor, protected exact-main SHA or released serialized gates.

### Minimum non-mutating contract tests for the workflow owner

| Test fixture | Expected admission | Expected side effects |
| --- | --- | --- |
| `pull_request` targeting `main`, including same-repository PR | Verify only | No privileged/self-hosted recovery |
| `push` changing workflow on feature branch | Verify only | No privileged recovery |
| `workflow_dispatch` on stale main | Deny | None |
| Trusted dispatch on exact main, external+local healthy | No-op | None |
| Trusted dispatch on exact main, one gate unknown/open | Deny | None |
| Trusted dispatch on exact main, both gates released, unhealthy and explicit approval | Candidate for bounded recovery | Only allowlisted operations after receipt/rollback readiness |
| Runner label mismatched or `sudo -n` unavailable | Deny safely | No partial stop, chown, chmod or restart |
| Repair succeeds locally but external probe fails | Report incomplete | Do not claim production green |

All test fixtures must run with mocks or disposable runners; **none** are permission to exercise the live `recover` job. Workflow implementation remains owned by #1135's reconciled recovery owner, not this docs PR.

## Reviewer-ready release decision record

For every prospective recovery run the owner must record a **single immutable decision** before any privileged step:

| Field | Admissible value | Fail-closed rule |
| --- | --- | --- |
| canonical repository | `Zennay/zCloud` | Other / missing repository -> DENY |
| invocation | manually authorized trusted dispatch | PR, push or unknown -> VERIFY_ONLY |
| main revision | protected main SHA reread immediately before admission | stale / unknown / drift -> DENY |
| serialized gates | #580/PWQ-41 and #1089 both explicitly released on that same revision | either active or incomplete -> DENY |
| dashboard health | bounded independent status response plus local verification | healthy -> NOOP; unknown -> DENY |
| execution environment | allowlisted permanent guarded VPS runner and noninteractive privilege | generic runner / missing permission -> DENY |
| exclusive ownership | reviewer approval and no concurrent recovery | conflicting or missing owner -> DENY |
| rollback state | precise prior state captured without secrets | missing rollback state -> DENY |

Decision codes are `VERIFY_ONLY`, `DENY`, `NOOP_HEALTHY`, and `REPAIR_ADMITTED`. **Only** `REPAIR_ADMITTED` may enter a bounded privileged path. A successful external status check is evidence for `NOOP_HEALTHY`, not for `REPAIR_ADMITTED`.

### Safe completion receipt

For an explicitly admitted and actually necessary repair, record the reviewed admission SHA, authorized owner, action class, start/end time, original service state, bounded post-action local and external probe verdicts, rollback result (or not-needed reason), and a correlation/run ID. A pass requires **both** local API and external API verification against the same post-action deployment; a failure or timing gap is `INCOMPLETE`, never a green deployment.

### Why this is a separate PR

The known issue #1135 and existing audit branch retain their owner responsibilities. This add-only document is **not** executable remediation, not a workflow patch, and not a production gate bypass. Its purpose is to provide a stable shared acceptance contract without writing to the active owner's workflow, tests, SQLite, Notion queue or runner state.

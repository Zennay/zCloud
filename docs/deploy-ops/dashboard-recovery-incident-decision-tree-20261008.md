# Dashboard recovery incident decision tree (read-only)
Date: 2026-10-08. Scope: operator triage for issue #1135; this document does **not** authorize production recovery.

## Safety invariant
A pull_request event is never a recovery mandate. A successful external verification is positive evidence of reachability, not evidence that privileged repair is required. Any unavailable, conflicting or stale evidence means **STOP / read-only investigation**.

## Decision sequence
1. Record repository, trigger event, workflow run URL, requested ref, triggering SHA, observed main SHA, actor, and independent external health result. Redact credentials, private paths, journal data and personally identifying values.
2. If event is `pull_request`, **do not run** any self-hosted mutation-capable recovery job. Run only hosted/read-only validation. File a security incident if the workflow attempted stop/restart, permissions changes, SQLite writes, systemd override edits or immutable-flag changes.
3. If the external dashboard is healthy, **do not stop or restart** the service. Exit no-op with a minimal evidence receipt. A failed internal `recover` job alone does not override a healthy independent probe.
4. For an unhealthy external probe, independently confirm failure with bounded non-mutating diagnostics. Missing privilege is not a reason to attempt privilege escalation inside a PR.
5. Before even proposing repair, verify exact canonical repository and current main SHA, trusted manually authorized actor, guarded permanent VPS runner identity, no stale event SHA, and explicit release of **both** serialized gates **#580/PWQ-41** and **#1089**. Any unknown or active gate means stop.
6. Check active branch/PR file ownership and Worker 2's PWQ-258 lane. Do not adopt `audit/deploy-dashboard-recovery-mutation-boundary-20261008-w1` or the nine #1108 planner/preflight/intent paths without owner handoff.
7. Only in a separately approved serialized production window: require scoped change plan, pre-change state receipt, rollback steps, bounded execution and post-change health confirmation. Any failed check triggers rollback or escalation; never retry blindly.

## Explicit acceptance matrix
| Case | Expected decision |
| --- | --- |
| Unmerged PR, healthy or unhealthy dashboard | NO MUTATION; hosted/read-only checks only |
| Trusted exact-main dispatch, healthy external dashboard | NO-OP; do not stop service |
| Trusted dispatch, stale main SHA | DENY |
| Unknown actor, repository or runner identity | DENY |
| #580/PWQ-41 or #1089 active, unknown or not explicitly released | DENY |
| Unhealthy but probe evidence incomplete | READ-ONLY TRIAGE |
| Insufficient privilege or missing rollback receipt | DENY |
| Unhealthy, trusted exact-main, both gates released, explicit approval and bounded rollback | ELIGIBLE FOR SEPARATELY AUTHORIZED REPAIR; not auto-authorized |

## Evidence receipt (redacted)
Record `timestamp_utc`, `run_url`, `event_name`, `repo`, `trigger_sha`, `main_sha`, `external_health`, `gate_580`, `gate_1089`, `pr_branch_ownership_snapshot`, `decision`, `reason_code`. Do not store cookies, tokens, full URLs with secrets, raw journal output, system configurations or DB content in a PR.

## Non-goals
This document does not change the recovery workflow, rerun jobs, unlock #580/#1089, merge PRs, change service state, cancel actions or modify VPS/SQLite/queue. See issues #1135 and #1009 for release authority and ownership.

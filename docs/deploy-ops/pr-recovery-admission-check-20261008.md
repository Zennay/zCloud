# PR-triggered dashboard recovery: deploy admission checklist (2026-10-08)

Status: **NOT AUTHORIZED**. This is a read-only, operator-facing admission contract; it cannot release production locks.

## Background and ownership

- [#1135](https://github.com/Zennay/zCloud/issues/1135) records a failed self-hosted `recover` job on #1108 head `b76539d38eb17265a6e2559a7efb66c18ffd85b6` while independent `external_verify` succeeded. This is **not** proof of public outage.
- The `.github/workflows/zcloud-dashboard-access-recovery.yml` implementation and `audit/deploy-dashboard-recovery-mutation-boundary-20261008-w1` belong to their existing owners. #1108's nine paths and PWQ-258/Worker 2 are out of scope.
- #580/PWQ-41 and #1089 are serialized control-plane/release gates. Both must be explicitly released with current evidence before any production-changing operation. Historical #576 is retired.

## Required evidence: deny on unknown

Record each row as PASS/FAIL/UNKNOWN plus immutable evidence URL, Git SHA, UTC timestamp, reviewer and environment. An empty or stale field is UNKNOWN, **never** PASS.

| Scenario | Required observation | Admission |
| --- | --- | --- |
| `pull_request` from any head, including forks | Hosted/read-only validation only; **zero** systemctl, sudo, chmod, chown, chattr, SQLite, runner, browser or service operations | Deny mutation |
| Trusted workflow_dispatch on non-main / stale SHA | Canonical repository, protected main ref and exact current main SHA checked before any side effect | Deny mutation |
| Unapproved actor or unrecognized permanent runner | Explicit allowlist and runner identity checks before secrets/privileges | Deny mutation |
| Healthy external dashboard | Stable external verification with defined time bounds; no service interruption | No-op |
| Unhealthy signal with absent corroboration | Independent bounded diagnostics distinguish transient network/proxy error from listener outage | Deny mutation |
| #580/PWQ-41 or #1089 active/unknown | Both serialization owners closed/released; no competing live writer | Deny mutation |
| Missing sudo/permissions/rollback snapshot | Preflight fails without altering DB ownership, service or drop-ins | Deny mutation |
| Trusted exact-main, unhealthy and both gates released | Time-bounded minimal recovery, explicit rollback plan, post-action receipt and health evidence | Eligible for separately approved recovery, **not** automatic authorization |
| Failure midway through bounded repair | Exit nonzero, preserve forensic minimum, rollback where safe; never claim green based on external-only status | No further retry without new gate |

## Review sequence (read-only)

1. Re-fetch main SHA, all open PR owners **and** PR-less unmerged branches; fail closed on changed file ownership or stale snapshots.
2. Confirm the dedicated workflow owner has removed privileged operations from PR-triggered execution. Source diff, not a green badge, is the proof.
3. Run deterministic negative-contract tests for the cases above on the *exact* candidate head using hosted/read-only jobs.
4. Obtain exact-head complete zCloud regression and bounded permanent-VPS **diagnostic only**; inspect independent `external_verify` separately from `recover`.
5. Confirm #580/PWQ-41 and #1089 are explicitly released on that same current main SHA. A passed CI job or this document is insufficient.
6. Only the serialized production owner may explicitly approve an unhealthy recovery transaction; collect before/after state, rollback record, actor, timestamp and exact deployed SHA.

**Never:** auto-rerun a mutating job on a PR; stop a healthy listener for CI cosmetics; equate green external verification with permission to repair; publish journal content, token-bearing argv, SQLite rows or secret paths; merge an overlapping stale PR.

## Scope

This document is intentionally add-only and advisory. It does not modify workflows, runner configuration, production services, SQLite, queue, browser, secrets, PR metadata, or any existing owner branch. Its own acceptance requires verifying that this path is unclaimed and reviewing the PR diff before merge.

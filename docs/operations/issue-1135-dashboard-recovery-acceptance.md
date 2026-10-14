# Issue #1135 — PR-triggered dashboard recovery acceptance

This document is **review evidence only**. It does not claim ownership of `.github/workflows/zcloud-dashboard-access-recovery.yml`, dispatch a recovery workflow, or authorize production mutation.

## Evidence that motivated the gate

- Issue: https://github.com/Zennay/zCloud/issues/1135
- Reported run: https://github.com/Zennay/zCloud/actions/runs/37705775400
- The `recover` job failed while independent `external_verify` succeeded. A failed recovery job is not proof of a public dashboard outage.
- Separate existing owner: `audit/deploy-dashboard-recovery-mutation-boundary-20261008-w1`; coordinate instead of editing its audit paths.

## Required acceptance scenarios

| Scenario | Expected admission | Privileged mutations |
| --- | --- | --- |
| Same-repository pull request | Hosted/read-only verification only | None |
| Fork pull request | Untrusted; no recovery job on VPS | None |
| External dashboard healthy | Preserve healthy service | None |
| Manual dispatch from a feature ref | Reject | None |
| Wrong repository or untrusted actor | Reject | None |
| Trigger SHA differs from canonical current main | Reject | None |
| #580/PWQ-41 or #1089 window still occupied | Reject | None |
| Missing/incomplete gate, identity or health evidence | Reject (fail closed) | None |
| Canonical exact-main and released gates, confirmed unhealthy dashboard | Admit only bounded explicitly approved repair | Scoped repair with before/after receipt and rollback |

## Safe verification order

1. Owner rechecks complete open-PR changed-file and PR-less-branch ownership; this document is not a claim on recovery workflow files.
2. Validate triggers and job conditions offline using fixture-based contract tests. No pull-request event may enter a sudo-capable mutation job.
3. Run hosted exact-head regression and read-only external dashboard verification.
4. Obtain a fresh current-main SHA and *both* serialized gate identities: #580/PWQ-41 plus #1089. Retired #576 is not a release gate.
5. Require a separate trusted dispatch and approved maintenance window before a production repair. Re-read repository/ref/SHA, actor, runner identity and external health immediately before mutation.
6. If all checks pass and recovery is necessary, use a bounded operation with immutable before/after receipt, rollback path, and separate post-repair external verification. Abort on any drift.

## Reviewer's evidence checklist

- [ ] Exact PR head SHA and matching test-run IDs recorded
- [ ] PR and healthy-service scenarios prove **zero** `systemctl`, SQLite permission, immutable-flag or systemd drop-in changes
- [ ] Untrusted/stale/incomplete and occupied-gate scenarios fail closed
- [ ] No secrets, raw journald data or sensitive filesystem contents in outputs
- [ ] Permanent guarded VPS identity validated on an **approved read-only** proof
- [ ] All necessary serialized owner handoffs explicit
- [ ] No live repair performed merely to satisfy a CI status check

Any failed or missing checkbox means **not admitted**. This checklist never changes release authority and cannot substitute for current GitHub owner and gate evidence.

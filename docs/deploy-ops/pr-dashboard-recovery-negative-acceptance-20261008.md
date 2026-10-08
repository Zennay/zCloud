# PR-triggered dashboard recovery: negative acceptance matrix

Status: **non-authorizing, read-only handoff**. Applies to [issue #1135](https://github.com/Zennay/zCloud/issues/1135). This document does not grant deploy, workflow-dispatch or service rights.

## Owner and scope boundary

The `.github/workflows/zcloud-dashboard-access-recovery.yml` implementation belongs to its existing workflow owner (#581); the audit branch `audit/deploy-dashboard-recovery-mutation-boundary-20261008-w1` retains its owner. The nine planner/preflight/intent files belong to #1108; PWQ-258 belongs to Worker 2. Do not change any of those files from this documentation lane.

## Deterministic negative-case contract

For each case below, the owning implementation must collect **exact-head** test output, the GitHub event/ref/repository/actor trust decision, and a redacted list of steps entered. Negative cases must **never** invoke privileged recovery commands. A skipped or successful external probe is not a permission token.

| Case | Input/event | Required decision | Forbidden effects |
| --- | --- | --- | --- |
| N01 | `pull_request` from same repository | Verify externally/read-only only | no sudo, systemctl, drop-in writes, chmod/chown/chattr, SQLite mutations |
| N02 | `pull_request` from fork | Deny VPS recovery | no self-hosted privileged job, no secrets or writes |
| N03 | Trusted main, external dashboard healthy | Success with explicit no-op receipt | no stop/restart even if DB probe is green |
| N04 | Healthy repeated twice | Second run identical no-op | no service churn, database touch, or changed recovery state |
| N05 | Noncanonical repository or actor | Deny before runner mutation | no sudo or service action |
| N06 | Trusted repository but ref != exact main | Deny | no recovery |
| N07 | Main SHA drift after preflight | Abort and recollect evidence | no re-use of stale trust decisions |
| N08 | #580/PWQ-41 or #1089 open/unknown | Deny repair | no deploy, dispatch, service or browser mutation |
| N09 | External probe times out or returns transient 503 | Diagnostic-only bounded retry | do not infer persistent outage |
| N10 | Privilege absent | Safe failure with redacted receipt | no partial permission/ownership writes |
| N11 | Health contradictory (healthy public, unhealthy local) | Diagnostic-only; human/owner reconciliation | no unconditional restart |
| P01 | Trusted exact-main, truly unhealthy, gates independently released | Serialized owner may evaluate bounded repair | no action without explicit window, timeout, rollback and post-operation evidence |

## Evidence acceptance

- Bind every proof to the immutable candidate head SHA **and** the current canonical main SHA after CI; old-head green is historical only.
- Collect both external and local health observations, distinguishing probe failure from proven unhealthy state; redact tokens, paths and arbitrary journal content.
- Record `mutation_performed=false` for all N01–N11. The contract fails if any privileged step was *entered*, even if it later failed.
- Require exact-head hosted regression and guarded **read-only** VPS diagnostics. Tests must mock all privileged operations; never use production as a negative-case fixture.
- For P01, require explicit current release of #580/PWQ-41 **and** #1089, full PR and PR-less ownership inventory, named operator/owner, monotonic timeout, rollback and sanitized final receipt. An unavailable or incomplete gate inventory means deny.
- Never rerun a known-mutating PR-triggered recovery workflow just to collect green evidence.

## Known regression evidence

As of 2026-10-08 02:20 UTC, #1135 and Notion handoff record runs 37715959929 and 37710645579 with external verification SUCCESS but VPS recovery FAILURE. The documented sequence includes an active service, green SQLite probe, unnecessary restart and transient /api/status 503. That is **not** proof of public dashboard outage. The operational release decision remains **NOT ADMITTED**.

## Exit checklist for owning worker

1. Remove all privileged execution from PR-triggered paths, explicitly test N01/N02.
2. Enforce healthy no-op before any privileged command and prove N03/N04/N11.
3. Prove canonical identity, changed-SHA and gate fail-closed cases N05–N10.
4. Prove P01 only by mocked bounded recovery until a separately authorized production window exists.
5. Publish exact-final-head green checks, redacted receipts, and independent ownership/gate snapshots before marking release-ready.

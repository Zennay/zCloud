# zCloud deploy-ops serialized release runbook

This runbook defines the fail-closed sequence for deploy-ops work that can mutate GitHub Actions state, the VPS runtime, services, browser state, SQLite/queue state, or production deployment state.

## Current serialized owners

The serialized integration window is owned by:
- PR #580 / PWQ-41
- PR #576

Do not merge, deploy, recover, cancel Actions runs, mutate services, or write queue/SQLite state from a parallel deploy-ops lane while either owner remains active.

## Release prerequisites

A write-capable deploy-ops claim is admissible only after all of the following are true:

1. PR #920 has landed on the exact current `main` used for admission.
2. PR #924 has landed on that same exact `main`, so PR-less branch ownership is part of admission rather than a blind spot.
3. A fresh bounded serialized-writer snapshot reports:
   - `inventory_complete=true`
   - `status=clear`
4. PR #580 / PWQ-41 and PR #576 are terminal and have explicit release evidence.
5. Current `main` SHA is re-read immediately before the claim.
6. Open PR changed-file ownership is re-checked for every intended path.
7. A fresh PR-less branch snapshot is collected and must report:
   - `inventory_complete=true`
   - `status=complete`
   - complete per-branch changed-file evidence
8. The intended paths are checked against that PR-less branch snapshot and must report `status=clear`.
9. Any stale-main result, changed-file overlap, incomplete inventory, active owner, PR-less branch owner, or returned blocker aborts the claim before any write-capable action.

## Safe order for #712

Only after the release prerequisites pass:

1. Re-fetch canonical `main` and record the exact SHA.
2. Re-run open-PR changed-file ownership preflight for `.github/workflows/zssh-governance-runner-priority.yml`.
3. Re-run the PR-less branch ownership snapshot and require complete evidence plus `status=clear` for that path.
4. Build the hosted-only contract change on a fresh branch from that exact `main`.
5. Run exact-head regression.
6. Re-run the serialized-writer snapshot immediately before any live dispatch.
7. Re-run both open-PR and PR-less branch ownership checks against the exact candidate paths.
8. Require `inventory_complete=true` and `status=clear` again for every admission layer.
9. Perform only the bounded cancellation proof described by #712.
10. Read back the result and record the exact run/commit evidence.

A blocker or path owner that reappears between the final checks and live dispatch cancels the claim. Never fall back to a stale earlier `clear` result.

## Parallel-worker rule

Parallel workers may prepare file-disjunct read-only evidence, tests, or documentation. They must not take over paths or runtime responsibilities already owned by another open PR, issue, branch (including a branch without an open PR), or active worker.

## Failure rule

When any prerequisite cannot be proven, stop the write-capable path and continue only with safe, file-disjunct work. Do not reinterpret missing evidence as approval.

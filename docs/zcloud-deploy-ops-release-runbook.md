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
2. A fresh bounded serialized-writer snapshot reports:
   - `inventory_complete=true`
   - `status=clear`
3. PR #580 / PWQ-41 and PR #576 are terminal and have explicit release evidence.
4. Current `main` SHA is re-read immediately before the claim.
5. Open PR changed-file ownership is re-checked for every intended path.
6. Any stale-main result, changed-file overlap, incomplete inventory, active owner, or returned blocker aborts the claim before any write-capable action.

## Safe order for #712

Only after the release prerequisites pass:

1. Re-fetch canonical `main` and record the exact SHA.
2. Re-run open-PR/branch ownership preflight for `.github/workflows/zssh-governance-runner-priority.yml`.
3. Build the hosted-only contract change on a fresh branch from that exact `main`.
4. Run exact-head regression.
5. Re-run the serialized-writer snapshot immediately before any live dispatch.
6. Require `inventory_complete=true` and `status=clear` again.
7. Perform only the bounded cancellation proof described by #712.
8. Read back the result and record the exact run/commit evidence.

A blocker that reappears between steps 5 and 7 cancels the claim. Never fall back to a stale earlier `clear` result.

## Parallel-worker rule

Parallel workers may prepare file-disjunct read-only evidence, tests, or documentation. They must not take over paths or runtime responsibilities already owned by another open PR, issue, branch, or active worker.

## Failure rule

When any prerequisite cannot be proven, stop the write-capable path and continue only with safe, file-disjunct work. Do not reinterpret missing evidence as approval.

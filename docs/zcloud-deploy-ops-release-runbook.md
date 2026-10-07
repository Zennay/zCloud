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
2. PR #866 has landed on that same exact `main`, so open-PR changed-file ownership is enforced by the canonical bounded auditor.
3. PR #924 has landed on that same exact `main`, so PR-less branch ownership is part of admission rather than a blind spot.
4. A fresh bounded serialized-writer snapshot reports:
   - `inventory_complete=true`
   - `status=clear`
5. PR #580 / PWQ-41 and PR #576 are terminal and have explicit release evidence.
6. Current `main` SHA is re-read immediately before the claim.
7. A fresh open-PR ownership snapshot is collected with the #866 auditor and changed-file ownership is re-checked for every intended path.
8. A fresh PR-less branch snapshot is collected and must report:
   - `inventory_complete=true`
   - `status=complete`
   - complete per-branch changed-file evidence
9. The intended paths are checked against that PR-less branch snapshot and must report `status=clear`.
10. Any stale-main result, changed-file overlap, incomplete inventory, active owner, PR-less branch owner, or returned blocker aborts the claim before any write-capable action.

All admission evidence belongs to one exact-main transaction. Record the exact `main` SHA used to create the candidate and never compose a writer-window or ownership result from another main revision into that transaction.

## Canonical admission tools

After #920, #866 and #924 are landed on the exact admission `main`, use their canonical repository tools rather than reimplementing or approximating their classifiers:

- #920 writer-window classification: `scripts/zcloud_serialized_writer_window_audit.py`
- #866 open-PR changed-file ownership: `scripts/zcloud_open_pr_overlap_audit.py`
- #924 PR-less branch inventory: `scripts/zcloud_unpr_branch_snapshot.py`
- #924 PR-less path ownership: `scripts/zcloud_unpr_branch_overlap_audit.py`

A prose owner check, partial GitHub search, stale cached snapshot, or hand-built substitute is not equivalent admission evidence. If any canonical tool is missing from the exact `main` revision, the write-capable claim is inadmissible.

## Safe order for #712

Only after the release prerequisites pass:

1. Re-fetch canonical `main` and record the exact SHA.
2. Re-run the #866 open-PR ownership audit for `.github/workflows/zssh-governance-runner-priority.yml` with `scripts/zcloud_open_pr_overlap_audit.py`.
3. Re-run the PR-less branch ownership snapshot with `scripts/zcloud_unpr_branch_snapshot.py` and require complete evidence plus `status=clear` for that path via `scripts/zcloud_unpr_branch_overlap_audit.py`.
4. Build the hosted-only contract change on a fresh branch from that exact `main`.
5. Run exact-head regression.
6. Re-fetch canonical `main` after exact-head regression and require it to equal the candidate base SHA. If `main` advanced, abandon this admission transaction and restart from the new exact `main`; do not dispatch from the stale candidate.
7. Re-run the serialized-writer snapshot with `scripts/zcloud_serialized_writer_window_audit.py` immediately before any live dispatch.
8. Re-run both #866 open-PR and #924 PR-less branch ownership checks against the exact candidate paths.
9. Require `inventory_complete=true` and `status=clear` again for every admission layer.
10. Perform only the bounded cancellation proof described by #712.
11. Read back the result and record the exact run/commit evidence.

A blocker or path owner that reappears between the final checks and live dispatch cancels the claim. Never fall back to a stale earlier `clear` result.

## Parallel-worker rule

Parallel workers may prepare file-disjunct read-only evidence, tests, or documentation. They must not take over paths or runtime responsibilities already owned by another open PR, issue, branch (including a branch without an open PR), or active worker.

## Failure rule

When any prerequisite cannot be proven, stop the write-capable path and continue only with safe, file-disjunct work. Do not reinterpret missing evidence as approval.

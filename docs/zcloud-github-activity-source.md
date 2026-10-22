# zCloud GitHub activity source coverage

The read-only GitHub activity collector now includes a bounded branch-tip observation alongside pull requests, Actions runs and commits reachable from `main`.

## Branch observation contract

- Reads at most one page of 100 branch refs per canonical allowlisted repository. The endpoint is name-ordered, not activity-ordered; coverage is `bounded_truncated` when the page cap is reached and must not be treated as a complete branch inventory.
- Emits only a stable hash of the branch ref name, the immutable tip commit SHA, the commit timestamp, and the canonical commit URL. Raw branch names are not exported.
- The event kind `branch_tip_commit` means a recent commit is currently at a sampled branch tip. It is **not** a branch-ref update timestamp, proof of PR review, proof of merge to `main`, or proof of deployment.
- A tip SHA already present in the sampled `main` commit window is not emitted again as a branch event. PR-reported merge targets remain separate from proven main-history membership.
- Branch coverage and page-limit state are explicit in each repository's coverage receipt. A provider failure produces `partial` or `unavailable`, never a complete/empty claim.
- Worst-case request budget is 10 GitHub GETs per repository: five PR pages, two Actions pages, two main-commit pages and one branch page. The on-demand cache reserves the same worst-case cost before starting a refresh.

This collector is public/read-only by default in the on-demand cockpit. Its source timestamps and `observed_at` remain separate. The complete feed stays explicitly partial because local VPS activity, historical backfill, owner claims and deployment proof are not established by these GitHub reads. No worker/queue/SQLite/claim mutation, workflow dispatch, merge or deployment is performed.

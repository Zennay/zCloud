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

## Last-successful cache behavior

The on-demand reader retains the previous successful bounded receipt while a refresh is running. If a refresh fails or capacity/rate admission delays a new fetch, the response keeps that receipt and reports the current state separately, with `stale=true`, `last_successful_sync_at` (observer completion time) and `stale_age_s`. A provider failure does not replace useful prior evidence with an empty receipt. This timestamp is not the GitHub source timestamp; the receipt's `observed_at` and each event's `source_at` remain separate. The UI should render these fields explicitly and must continue to show the feed as partial.

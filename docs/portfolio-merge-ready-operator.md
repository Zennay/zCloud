# Portfolio merge-first operator helper (zSentinel / zCloud)

The existing `scripts/portfolio_merge_ready.py` is an **operator tool**, not an unattended merge bot. It lists selected GitHub PR receipts without changing workers, SQLite or repositories unless an explicit, separately authorized merge is requested.

## Merge-first cockpit: cross-project offline worklist

The new `scripts/zsentinel_merge_first_board.py` combines multiple **explicit existing** product PRs into a bounded, read-only operator worklist. It does not discover new owners, claim work, approve PRs, merge, restart runners or deploy. Its canonical repository allowlist comes from `projects.json`.

```sh
python3 scripts/zsentinel_merge_first_board.py \
  --pr Zennay/zCloud#1571 --pr Zennay/Supa#123 \
  --format text --output /private/new-zsentinel-merge-board.html
```

The output is a new, exclusive-create, private (0600) responsive standalone HTML file. Without `--output` there is no local write. JSON output is available using `--format json`. The HTML contains no JavaScript, network fetches, tokens, worker credentials or queue identifiers. Never point output at a shared web directory. Existing reports are never overwritten.

The board is intentionally bounded: at most 12 explicitly selected PRs, 6 per canonical repository, 3 repositories and 64 GitHub GETs. All PRs are read again after their observed reviews and check-runs; an unstable head/base/draft becomes `SOURCE_MOVED`, not a false pass. A missing or oversized evidence page is UNKNOWN. Exact-head independent approvals distinguish changes requested from no approval; recent Check Runs and legacy statuses are checked separately. A successful observed set is labeled **OBSERVED_GREEN_ONLY** because the board cannot grant required-context coverage or protected-branch authorization.

Operators can quickly select an independent unblocked product PR while other PRs wait on reviewer, CI, claim or release rights. For any actual merge, the **separate** single-PR `portfolio_merge_ready.py --merge` owner-claim preconditions and GitHub server-side protection still apply. Nothing in the board's HTML or JSON is a merge permit; `main_merged=false` and `live_verified=false` remain conservative snapshot indicators, not assertions about the actual GitHub state.

## Read-only inventory

```sh
python3 scripts/portfolio_merge_ready.py --repo Zennay/Supa --project supa --pr 123
```

The result describes the current PR source HEAD, draft/base status, **exact-HEAD independent** GitHub review receipts, complete observed GitHub check-runs/legacy statuses, and whether a merge would still need a valid exclusive lease. A green-looking result is **not** a claim, GitHub branch protection waiver or production-deploy proof. If the source is unavailable or untrusted the tool fails closed rather than inventing green checks.

## Authorized single-PR merge

```sh
python3 scripts/portfolio_merge_ready.py --repo Zennay/Supa --project supa \
  --pr 123 --merge --owner-id '<real claim owner>' --claim-key '<existing exclusive mainwriter claim>'
```

Use only an **already held and independently authorized** live claim. The helper will not create, renew, steal or modify one. Do not insert real credentials or private lease data into tickets or documentation. It requires:

1. The project-to-repository association matches the canonical `projects.json` entry exactly (including case-insensitive GitHub owner/repo). Missing/ambiguous project entries deny writes. A mainwriter claim in one repository cannot merge another repository.
2. Exactly one explicit PR number. No bulk automatic merges, no default-branch surprises; target default must be `main`.
3. The PR remains open, non-draft, mergeable and targeting `main`. The exact HEAD must still match.
4. At least one **independent approval for that exact HEAD**, no active changes-requested review, and no incomplete >100-review inventory.
5. All observed GitHub check-runs and relevant legacy statuses are successful; incomplete, missing, pending or failed sources deny the merge.
6. A **live non-expired** exact-head `task_claims` record with the specified owner, claim key and a structured `mainwriter` or `main-merge` capability. Strings pretending to be a capability list are rejected.
7. A second fresh PR, review, CI and lease check immediately before one SHA-bound GitHub squash merge request. GitHub's branch protection remains the final gate.

After the write, the helper independently reads the PR's `merged_at` and `merge_commit_sha`, and the actual `refs/heads/main` SHA. `MERGED` is only emitted if the result is corroborated by the fresh read. `main_commit` identifies the exact merge commit and `default_branch_head_observed` shows the later main ref observed. These may differ if another authorized mainwriter subsequently advanced the branch.

If GitHub accepted a merge but the follow-up receipt is unavailable, it reports `MERGE_ACCEPTED_POSTCHECK_UNKNOWN` with the provisional reported commit. **Do not automatically retry** the merge: query GitHub and reconcile first. A successful source merge does not imply a live VPS deployment or a resumed worker.

## Boundaries

Never use a claim from a different project, treat old approvals as current, bypass protected-main checks, increase GitHub billing, restart a runner or claim ownership on behalf of another worker. The current PR is source-only until its own required exact-head CI, independent review, protected-main merge and serialized release are satisfied. No QA or regression suite is created or manually run by this feature.

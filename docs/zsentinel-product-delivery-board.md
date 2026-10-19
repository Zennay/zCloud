# zSentinel · Offline product-delivery board

An independent zCloud operator product, not a new worker role. This command consumes the already-existing source-only reports from three complementary zSentinel PRs and optionally a manually exported Notion claim observation. It does **not** import their branches or touch their owned implementation files.

## Sources

- GitHub product issue inbox: source-only [PR #1539](https://github.com/Zennay/zCloud/pull/1539), JSON schema `zsentinel-product-issue-inbox-v1`.
- Queue claim-attempt incident console: source-only [PR #1555](https://github.com/Zennay/zCloud/pull/1555), JSON schema `zsentinel-retry-incident-v1`.
- Recent un-PR'd branch evidence: source-only [PR #1554](https://github.com/Zennay/zCloud/pull/1554), JSON schema `zsentinel-recent-prless-ownership-v1`.
- Optional operator snapshot: `zsentinel-operator-claims-v1`. This is NOT an authoritative lease; it highlights reported active claims but never grants work.

All input files are **existing operator-saved JSON snapshots**. Nothing in the board obtains GitHub credentials, opens the live SQLite database, discovers environment secrets, calls GitHub/Notion APIs, restarts a runner, dispatches a workflow or writes a queue.

## Command

```sh
python3 scripts/zsentinel_product_delivery_board.py \
  --inbox ./issue-inbox.json \
  --retry ./retry-observation.json \
  --branches ./recent-branches.json \
  --format html --output ./delivery-board-20261019.html

# Works with the inbox alone, but clearly reports unknown queue/claim context.
python3 scripts/zsentinel_product_delivery_board.py \
  --inbox ./issue-inbox.json --format markdown
```

The HTML is standalone and responsive, with no JavaScript, external assets or runtime fetches. Issue URLs are constrained to GitHub, HTML is escaped, and raw task titles/IDs from the queue are not imported. JSON/Markdown exports retain bounded public GitHub issue titles only. The `--output` path is **create-only**, using a mode-0600 scratch file plus an atomic no-replace link. Existing files, symlinks and source files cannot be overwritten. Never pass private exports to a public artifact store without owner approval.

Optional claims snapshot example:

```json
{
  "schema": "zsentinel-operator-claims-v1",
  "observed_at_utc": "2026-10-19T12:00:00+00:00",
  "authorizes_work": false,
  "claims": [
    {"project": "cloud", "work_id": 258, "status": "Running"},
    {"project": "supa", "work_id": 14, "status": "Running"}
  ]
}
```

This format is a **manual observation**. The absence of a row is NEVER proof that a workstream is free. The board marks the snapshot stale if it is older than 24 hours, or if the observation clocks differ from the inbox by more than six hours. A GitHub assignee, PR mention, old branch, user-supplied empty claim file, or high attempt count never becomes a worker/lease authorization. `attempts` counts claim cycles, not actual failures.

## Review and recovery flow

Choose an issue card with concrete product impact. A linked PR means **inspect and integrate the existing PR first**. A reported live claim means **coordinate with its owner**, not take it. Excessive retry attempts or an expired lease mean **investigate the existing job with its owner**, not requeue. A partial inbox, incomplete branch slice, stale source or missing claim evidence remains unknown, never green.

This board does not implement the native `FAILED_RETRYABLE / NEEDS_AI / FAILED_FINAL` retry state machine; [#677](https://github.com/Zennay/zCloud/issues/677) remains its serialized runtime writer. It does not supplant PR-less branch ownership proof [#870](https://github.com/Zennay/zCloud/issues/870), or issue/P0-P1 discovery. It is an operator view joining their **already-published read-only evidence** without taking active owners.

All required protected-main exact-head CI, independent review, zSSH capability and release gates remain enforced outside this tool. Source-only commits do not prove VPS or production activation. No QA/test suite was written or manually run for this deliverable.

## Compare two manual observations without continuous polling

Save two independent JSON board reports from the command above. At a later operator-selected time, compare them **offline**, without reopening the GitHub account or live VPS queue:

```sh
python3 scripts/zsentinel_product_delivery_board.py --inbox ./issues-before.json \\
  --retry ./retry-before.json --format json --output ./board-before.json
# After a separate read-only observation and new saved input files:
python3 scripts/zsentinel_product_delivery_board.py --inbox ./issues-after.json \\
  --retry ./retry-after.json --format json --output ./board-after.json
python3 scripts/zsentinel_product_delivery_delta.py \\
  --previous ./board-before.json --current ./board-after.json \\
  --format html --output ./board-delta.html
```

The delta command requires two distinct, monotonically timestamped snapshots no more than seven days apart with the **same project inventory scope**. It reports newly **observed** issues, issues **absent from a later bounded sample**, PR-reference changes, and per-project increases/decreases in active queue entries, claim-cycle pressure and expired leases. It never interprets a missing issue as closed, a new PR reference as merged, a lower claim count as success, a branch name as ownership or an empty operator claims list as a lease release. Missing queue data is explicitly `unknown`, not zero. Like the board, its HTML is fully offline and its report publication is create-only. It contains no automation, polling, GitHub requests, SQLite or production mutation.


## Exact-head GitHub PR blockers (optional; no release authority)

The delivery board can now consume a fourth independent source: exact commit-level GitHub PR observations from `scripts/zsentinel_pr_delivery_observation.py`. It reads **only GET** on the canonical `api.github.com` host. It never requests approvals, dispatches CI, merges, deploys or modifies a worker.

```sh
# Public repository queries work where GitHub allows anonymous API access.
# Optional: provide a user-approved read-only GITHUB_TOKEN through normal
# environment handling; never include the credential in the command line.
python3 scripts/zsentinel_pr_delivery_observation.py \
  --target 'Zennay/zCloud#1556' \
  --target 'Zennay/Haxlab#579' \
  --output ./pr-head-observations.json

python3 scripts/zsentinel_product_delivery_board.py \
  --inbox ./issue-inbox.json \
  --prs ./pr-head-observations.json \
  --format html --output ./delivery-with-pr-gates.html
```

Each target requires a canonical owner/repository and numeric PR identifier; at most **five** separate targets / **20** bounded read-only API calls per invocation. The collector opens only the immutable PR head, exact-SHA check-runs, exact-SHA legacy statuses and independent reviewer submissions, rejects malformed/partial or paginated responses, and publishes the observation file with the board's create-only private file writer. No PR body, arbitrary check output, commit message, prompt, raw token, user data or issue discussion is exported.

The board displays the exact observed PR SHA, check observations and count of independent observed approvals alongside explicit release unknowns. It never calls the state `release-ready`, even when GitHub says `mergeable` and all observed checks succeeded, because required branch protection, Code Owner/review freshness, active Notion claims, production gates and owner release authorization are *not* established by these endpoints. A newer branch commit invalidates the old exact-head observation until the collector is run again. Snapshots >24 hours old or >6 hours apart from the issue inbox are rejected as decision context (not erased).

The offline delta command also identifies when the observed PR **head SHA**, check result or review count changed, without inferring merge, issue closure, reviewer release approval or worker recovery. The original three source reporters (#1539/#1554/#1555) remain separate until their own CI/independent review/release gates are satisfied. A hosted GitHub permission/rate/billing refusal is surfaced as an incomplete evidence error; no token, billing or privileged workaround is attempted.

**Capability boundaries:** no paid runner dispatch, no automatic claim, no review assignment, no CI retrigger, no bypass of protected main, no production promotion and no QA suite written or manually executed.

# zCloud deploy-ops: exact-head review candidate preflight (2026-10-14)

Scope: isolated **offline-only** advisory for [issue #1009](https://github.com/Zennay/zCloud/issues/1009). Its input is an **untrusted, independently collected snapshot**, not authenticated live GitHub evidence. The script must never be treated as approval to merge, cancel Actions, dispatch a workflow, restart services or deploy.

## What it actually checks

- Current-main SHA is 40 lowercase hexadecimal characters and both PR and PR-less branch inventories are explicitly complete. The declared total number of open PRs and PR-less branches must exactly match their returned arrays (not merely a `complete=true` flag); mismatches deny the entire snapshot.
- Releases #580/PWQ-41, #1089 and #1135 have explicit known issue state (open/closed); even when all are closed the tool always emits `release_authorized=false`.
- Every PR has a unique numeric identity, exact immutable head and base SHA, explicit draft flag, owner approval signal and at least one uniquely named CI check bound to **that head** with a successful conclusion. In addition to the legacy `owner_approved` flag, a structurally complete independent review receipt is mandatory: reviewer identity differs from the PR author, the decision is explicitly approved and `reviewed_head_sha` equals the **current** PR head. A review for a former head is diagnosed as `owner_review_stale`; no approval is inferred from an old commit or a self-approval. The tool cannot authenticate actor identities, timestamps, source of reviews, or GitHub's dismissal rules; this remains **offline advisory evidence, not GitHub approval verification**. Each must also declare a nonempty, unambiguous `required_checks` inventory; missing required checks cannot be replaced by a single convenient green check. The collector must derive this requirement from authenticated branch protection/rulesets, not user-supplied JSON alone.
- Branch identities are compared exactly against the separately owned inventory and reject case-fold collisions, Unicode normalization ambiguity and invisible directional/control characters. The `main` ref cannot masquerade as a PR-less branch.
- The manifest must include a **separately owned #1312-compatible PR-less branch inventory receipt**; its exact main SHA, full branch names and head SHAs, completion counters and never-authorizing flags must agree with the candidate manifest. If the receipt is missing, partial or mismatched, the classifier refuses the entire input. This is schema/consistency checking, not authentication of data or ownership takeover.
- Renames must also reserve every **previous filename**; provide `previous_files` (possibly empty) and `files_evidence.rename_sources_complete=true` with an exact integer `previous_files_total`. Incomplete or missing rename-source coverage invalidates the **entire** snapshot; otherwise workers can bypass the conflict screen through file renames.
- Every PR and PR-less branch reserves its changed paths. Draft and not-yet-reviewed PRs still own their files; any overlap rejects both candidates. Every changed-file cohort must additionally carry exact main/base and head SHA, `complete=true`, and an integer total count matching the entire path list. This prevents page-1-only lists (GitHub PR files can span multiple pages), stale comparisons and empty PR diffs from masquerading as conflict-free. A genuinely unchanged PR-less branch may have zero paths and reserves none.
- Invalid JSON, duplicate keys, non-finite values, missing inventory, stale base, failed/pending/skipped tests, unknown gates, path traversal, conflicting owners and oversized input deny admission. File ingestion pins **every ancestor directory** using descriptor-relative `O_NOFOLLOW` (not only the final filename), and checks inode/size/mtime/ctime both before and after reading to reject in-place changes. The CLI also opens only an ordinary input file (no final-path symlink, FIFO or directory), with bounded size and non-blocking file-open protections. Path conflicts conservatively include a file-to-directory replacement, Unicode NFC-equivalent names and case-only differences, rather than relying only on exact strings. Invisible Unicode format controls (including bidirectional overrides and zero-width characters) are rejected while ordinary accented source paths remain supported. Invalid snapshots are not echoed.
- Snapshot `observed_at` must be strict UTC seconds, not older than five minutes or more than 30 seconds in the future relative to the execution clock. Timestamps are user-supplied advisory freshness constraints, **not** proof that GitHub was actually consulted at that instant.
- Each candidate includes counts of conflicting other PRs and PR-less branches (unique owner counts, not raw filenames), giving operators useful conflict information without leaking source paths or sensitive branch names. Each candidate now also supplies exactly one deterministic **suggested next action** (in Dutch) for an independent owner to carry out: resolve overlapping ownership, restack stale base, validate required checks, re-run exact-head tests, obtain review, or finish the draft. None of these actions is executed by this script.
- Outputs `review_candidate` (a suggestion for **subsequent human review**, **not merge eligibility**), bounded reason codes and non-authorization flags. The JSON includes `blocked_candidate_count` and `blocker_counts` (multiple reasons per PR are counted separately), while the Dutch summary shows the total backlog and most frequent blockers before the first 30 individual PRs. This **does not rank or dispatch work** automatically.

## Running the isolated proof

```sh
python -m unittest discover -s tests -p 'test_zcloud_deployops_review_candidates_20261014_w1.py' -v
python scripts/zcloud_deployops_review_candidates_20261014_w1.py snapshot.json
python scripts/zcloud_deployops_review_candidates_20261014_w1.py snapshot.json --format markdown
```

The input JSON schema uses:

```json
{
  "main_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "observed_at": "2026-10-14T01:00:00Z",
  "inventory": {"prs_complete": true, "branches_complete": true,
    "open_pr_count": 1, "prless_branch_count": 0},
  "branch_inventory_receipt": {
    "schema": "zcloud-prless-branch-snapshot-v1",
    "inventory_complete": true, "admission_authorized": false,
    "deployment_authorized": false, "paths_compared": false,
    "main_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "branch_count": 1, "open_pr_head_count": 1,
    "branch_pages": 1, "pr_pages": 1, "pr_less": []
  },
  "gates": {"580": "open", "1089": "open", "1135": "open"},
  "prs": [{
    "number": 12,
    "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "draft": false,
    "owner_approved": true,
    "owner_review_evidence": {
      "complete": true, "decision": "approved",
      "author_login": "project-author", "reviewer_login": "independent-reviewer",
      "reviewed_head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "submitted_at": "2026-10-14T01:00:00Z"
    },
    "files": ["scripts/example.py"],
    "previous_files": [],
    "files_evidence": {"complete": true, "total_count": 1, "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "rename_sources_complete": true, "previous_files_total": 0},
    "required_checks": ["unit"],
    "checks": [{
      "name": "unit",
      "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "conclusion": "success"
    }]
  }],
  "branches": []
}
```

The static `observed_at` in this example expires after five minutes; use a freshly generated UTC timestamp for a real offline fixture. **Never override the observation clock to make a stale real snapshot appear fresh.**

This is a **synthetic fixture**, not a verified GitHub snapshot. The tool intentionally does not fetch from the network or validate who produced these assertions. A caller must independently obtain and authenticate complete paginated live inventories, current main, run/check attempts, branch diffs, review decisions and protected environments. Never mark a real production release green using the sample data or this script alone.

## Coordination / integration boundary

- Existing read-only ownership, stale-branch and successor-aware planners #1098/#1099/#1108 retain their files and authority. This adds no rewrite, merge, release or recovery action and is file-disjoint from their active sources.
- Existing #580/PWQ-41, #1089, #1135 and the workflow owner retain exclusive authorization. The check is deliberately not a replacement for their release safeguards.
- Do **not** open a PR until the known #1135 PR-triggered privileged recovery risk has been independently closed, or a release owner confirms that PR workflows cannot perform mutating recovery.
- No service, Actions job cancellation, SQLite, browser, queue, runner or VPS manipulation is performed by this advisory.

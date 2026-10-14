# zCloud deploy-ops: exact-head review candidate preflight (2026-10-14)

Scope: isolated **offline-only** advisory for [issue #1009](https://github.com/Zennay/zCloud/issues/1009). Its input is an **untrusted, independently collected snapshot**, not authenticated live GitHub evidence. The script must never be treated as approval to merge, cancel Actions, dispatch a workflow, restart services or deploy.

## What it actually checks

- Current-main SHA is 40 lowercase hexadecimal characters and both PR and PR-less branch inventories are explicitly complete.
- Releases #580/PWQ-41, #1089 and #1135 have explicit known issue state (open/closed); even when all are closed the tool always emits `release_authorized=false`.
- Every PR has a unique numeric identity, exact immutable head and base SHA, explicit draft flag, owner approval signal and at least one uniquely named CI check bound to **that head** with a successful conclusion. Each must also declare a nonempty, unambiguous `required_checks` inventory; missing required checks cannot be replaced by a single convenient green check. The collector must derive this requirement from authenticated branch protection/rulesets, not user-supplied JSON alone.
- Every PR and PR-less branch reserves its changed paths. Draft and not-yet-reviewed PRs still own their files; any overlap rejects both candidates.
- Invalid JSON, duplicate keys, non-finite values, missing inventory, stale base, failed/pending/skipped tests, unknown gates, path traversal, conflicting owners and oversized input deny admission. Invalid snapshots are not echoed.
- Outputs `review_candidate` (a suggestion for **subsequent human review**, **not merge eligibility**), bounded reason codes and non-authorization flags.

## Running the isolated proof

```sh
python -m unittest discover -s tests -p 'test_zcloud_deployops_review_candidates_20261014_w1.py' -v
python scripts/zcloud_deployops_review_candidates_20261014_w1.py snapshot.json
```

The input JSON schema uses:

```json
{
  "main_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "inventory": {"prs_complete": true, "branches_complete": true},
  "gates": {"580": "open", "1089": "open", "1135": "open"},
  "prs": [{
    "number": 12,
    "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "draft": false,
    "owner_approved": true,
    "files": ["scripts/example.py"],
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

This is a **synthetic fixture**, not a verified GitHub snapshot. The tool intentionally does not fetch from the network or validate who produced these assertions. A caller must independently obtain and authenticate complete paginated live inventories, current main, run/check attempts, branch diffs, review decisions and protected environments. Never mark a real production release green using the sample data or this script alone.

## Coordination / integration boundary

- Existing read-only ownership, stale-branch and successor-aware planners #1098/#1099/#1108 retain their files and authority. This adds no rewrite, merge, release or recovery action and is file-disjoint from their active sources.
- Existing #580/PWQ-41, #1089, #1135 and the workflow owner retain exclusive authorization. The check is deliberately not a replacement for their release safeguards.
- Do **not** open a PR until the known #1135 PR-triggered privileged recovery risk has been independently closed, or a release owner confirms that PR workflows cannot perform mutating recovery.
- No service, Actions job cancellation, SQLite, browser, queue, runner or VPS manipulation is performed by this advisory.

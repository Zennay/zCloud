# Deploy-ops retry and replay boundary (review-only)

This procedure is a **non-authorizing** operational checklist. It neither grants release admission nor permits replaying a production deployment. Applies to zCloud deploy-ops evidence gathered for a specific repository, immutable commit, workflow run and attempt.

## Identity binding

Before comparing two observations, record **repository**, **target environment**, **main SHA**, **candidate/head SHA**, **workflow ID**, **run ID**, **run attempt**, **event**, **conclusion**, **observed UTC time**, and **receipt digest**. Missing or ambiguous values mean STOP. Never infer attempt from run ID alone. Do not copy unredacted secrets, runner tokens, cookies, hostnames, service environment files or private payloads into public PR comments.

## Repeat/retry decision table

| Observation | Safe reviewer outcome |
| --- | --- |
| Same run ID, newer attempt | STOP: prior successful check belongs to old attempt; independently verify all exact-head checks on new attempt. |
| Same head SHA, different main SHA | STOP: stale integration proof; require new exact-current-main comparison and full regression. |
| Same commit and run, receipt bytes/digest differ | STOP: contradictory evidence; investigate off-line before any action. |
| Same receipt, second delivery | Treat as duplicate evidence only; do not interpret duplicate as a second successful deployment. |
| One check succeeds, another remains queued/in progress | STOP: no all-green claim until every mandatory exact-head check is terminal successful. |
| Check success but missing trusted same-repository workflow provenance | STOP: do not infer deployment trust from a text label or user-submitted JSON. |
| Complete CI but missing production status or post-deploy activation receipt | STOP: build correctness is not production readiness. |
| Serialized production owner #580 or #1089 remains active, unclear, or replaced | STOP: fresh authoritative ownership inventory required; historic issue/PR numbers are not sufficient. |
| Partial production failure followed by a successful retry | STOP: document attempts separately; require authoritative current production state and post-deploy checks, not merely latest green job. |
| Unrelated deploy-ops PR has overlapping paths | STOP: contact that owner; never force push, merge, or alter its files. |

## Handoff record (advisory)

Use one bounded record for each observation, without credentials:

```json
{
  "repository": "Zennay/zCloud",
  "environment": "production",
  "main_sha": "<40-character commit sha>",
  "head_sha": "<40-character commit sha>",
  "workflow_id": "<workflow identity>",
  "run_id": "<run identity>",
  "run_attempt": 1,
  "observed_at_utc": "<ISO-8601 UTC timestamp>",
  "receipt_sha256": "<64-character lowercase hex digest>",
  "outcome": "STOP",
  "merge_authorized": false,
  "deploy_authorized": false,
  "mutation_performed": false
}
```

The template is illustrative, **not validated evidence**. A reviewer must reconcile it against live GitHub provenance and the authoritative production receipt. No offline data or published checklist can substitute for the serialized release owner or direct production verification.

## Mandatory acceptance before changing this document's status

1. Inspect complete open PR **and branch** inventory for overlapping ownership; this document must remain add-only and file-disjoint.
2. Compare the PR against *then-current* main and inspect its exact diff.
3. Require exact-final-head checks after any restack; old green workflow attempts do not count.
4. Keep the PR draft/unmerged while #580 / #1089 or successor serialized owners control production.
5. Never perform deploy, retry, rollback, PR metadata sweep, runner restart, SQLite/queue update or service/browser mutation based on this document.

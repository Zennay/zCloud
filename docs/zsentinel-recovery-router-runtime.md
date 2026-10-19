# zSentinel · Runtime incident router (product-only)

This is an **executable, local recovery/routing decision engine**. It consumes a trusted, fresh canonical zSentinel diagnosis snapshot alongside current queued work and live-verified active claim scopes; emits a compact three-line-per-incident operator view or machine-readable JSON, and optionally persists a private deduplicated incident ledger. It does not pretend to own the control-plane writer. This is a deliverable for [PWQ-433](https://app.notion.com/p/3fe9e19ac95581aab21ceae956954b08), guided by policy [PR #1562](https://github.com/Zennay/zCloud/pull/1562).

## Start (source-only, no production writes)

```bash
python3 scripts/zsentinel_recovery_router.py /secure/operator-observations.json --format text
python3 scripts/zsentinel_recovery_router.py /secure/operator-observations.json --state /secure/zsentinel-ledger.json --format json
```

For a browser-ready view without contacting a server, choose `--format html` and redirect stdout to a **private operator-owned local file** (e.g. `> /secure/zsentinel-status.html`). The cockpit shows incidents, P0/P1 GitHub issue/PR links, queue suggestions, source freshness, incomplete claims and exact-job reconciliation. It contains no JavaScript, tracking, network requests or privileged buttons. File handling/privacy of the redirected output belongs to the operator.

The optional `--state` ledger stores incident fingerprints and observed timestamps locally with permissions `0600`, atomic replacement and strict size/schema limits. Use a **trusted private directory**, never a live queue/SQLite, checked-in repo path, shared mount or an untrusted symlink. It does not call GitHub, Notion or the VPS, start any timer, enqueue work, acquire a task, rerun CI, alter any worker, or deploy. Feed it new complete observations only after upstream independent collectors/owners have gathered them. One invocation provides one decision snapshot. Actual activation as a production service awaits the serialized runtime owner.

## Optional automatic GitHub read-only refresh

A second executable entrypoint now refreshes the **exact already-known GitHub run/job IDs** before generating the same recovery decisions:

```bash
python3 scripts/zsentinel_recovery_refresh.py /secure/operator-observations.json \
  --registry projects.json --state /secure/zsentinel-ledger.json \
  --read-product-issues --issue-project zcloud --format html \
  > /secure/zsentinel-status.html
```

It reuses zCloud's existing `scripts/zsentinel_product_issue_inbox.py` canonical registry loader and hardened GitHub GET helper: HTTPS to `api.github.com` only, no redirects, bounded response bodies, 8-second timeouts, optional `GITHUB_TOKEN` from the environment and **no job reruns or billing changes**. Maximum eight explicitly selected jobs/two GETs each per invocation. With `--read-product-issues`, the same command also uses the existing canonical read-only GitHub issue/PR inbox for **up to three distinct canonical projects** selected with repeatable `--issue-project` (or the first three evidence projects). At most three additional GitHub GETs per selected project; missing/denied sources are degraded, never reported as zero open issues. A request flag never claims any issue. The received repository, run ID, job ID and pinned commit SHA must match the trusted input; otherwise that project's GitHub evidence becomes **degraded/unknown**, never an inferred runner outage. Job-created timestamps and actual runner assignment are required. An earlier billing attribution is deliberately cleared upon refresh: no-steps by itself does not prove failed payment. A separate independently verified billing annotation is necessary.

This is an **on-demand bounded live GitHub read**, not a daemon and not a complete Notion/queue/VPS data collector. Existing Notion/lease/runner observations are never re-timestamped by a successful GitHub GET, and a stale overall snapshot remains inadmissible. Do not schedule unlimited polling or run this command with an untrusted token. Explicitly selected jobs can be private; access denial or rate limits are reported as degraded rather than implying zero incidents.

## Input contract (JSON)

```json
{
  "schema_version": 1,
  "evidence": {
    "schema_version": 1,
    "observed_at": "2026-10-19T10:30:00Z",
    "projects": [{
      "id": "zcloud",
      "notion": {"posture": "incubate", "owner": "zCloud runtime owner"},
      "github": {
        "job_status": "queued",
        "queued_at": "2026-10-19T09:30:00Z",
        "observed_at": "2026-10-19T10:29:00Z",
        "runner_assigned": false,
        "run_id": 39000012345,
        "job_id": 117000123456,
        "head_sha": "0123456789abcdef0123456789abcdef01234567"
      },
      "runtime": {}
    }]
  },
  "claim_inventory_complete": true,
  "task_inventory_complete": true,
  "claim_observed_at": "2026-10-19T10:29:00Z",
  "task_observed_at": "2026-10-19T10:29:00Z",
  "issue_inbox": null,
  "active_claims": [{"project_id": "supa", "active": true, "paths": ["src/prices"]}],
  "tasks": [{
    "id": "supa-recipe-import",
    "project_id": "supa",
    "priority": "P1",
    "state": "queued",
    "eligible": true,
    "qa_only": false,
    "human_gate": false,
    "active_owner": false,
    "required_gates": ["none"],
    "depends_on_incidents": [],
    "paths": ["src/recipes"]
  }]
}
```

All dates must be timezone-aware. Underlying `zsentinel_diagnose.py` already rejects stale (>30 min) snapshots and unproven/future timestamps. The router further requires distinct positive `run_id` and optional `job_id` for GitHub incident identity; multiple jobs on a single SHA cannot be accidentally deduplicated into one incident. `waiting` / `pending` is **not** interpreted as a runner outage, even if an upstream legacy classifier thinks a job is unassigned.

A task suggestion requires **separate fresh (within 5 minutes)** `claim_observed_at` and `task_observed_at` timestamps plus explicitly true `claim_inventory_complete` and `task_inventory_complete`. Missing or stale timestamps suppress all suggestions, even when GitHub jobs are newly refreshed. A task suggestion requires every positive field: `queued`, `eligible=true`, `qa_only=false`, `human_gate=false`, `active_owner=false`, an explicit bounded file scope and an explicit set of required gates. Claim inventory must be declared complete and every active claim must have a known scope. A missing/unknown active claim scope suppresses **all** suggestions. Independently eligible suggested tasks must also have **non-overlapping scopes with one another**: the highest-priority candidate wins a collision; the operator must refresh claims when choosing actual work. Overlapping ancestor/descendant paths are not routed. Dependencies referencing currently observed incident IDs prevent routing. A runner outage prevents suggestions requiring `self_hosted`. Never infer a claim is free from a missing PR alone; proof of source completeness and fresh claims is the upstream operator/collector's responsibility.

## Product behavior and limitations

- **Exact-job lifecycle:** Re-observation of a previously blocked **same GitHub run + job + SHA** with a fresh canonical GET, actual non-empty executed steps, assigned runner and completed/success conclusion marks only **that specific job** `exact_job_completed_source_reconciled` in the private ledger. It never clears project-level approvals or proves a globally healthy listener, release or service. Unknown/vanished incidents remain blocked. Old incident entries without sufficient exact job identity cannot be automatically reconciled.
- **Incident identity:** Stable local SHA-256 key over project, kind, exact commit and GitHub run/job identity when present. A job that disappears from a later snapshot is marked `not_observed_needs_reconciliation`, **never automatically resolved**. Incoming optional prior state is schema-checked; ledger saturation refuses truncation.
- **Prioritization:** Verified GitHub-hosted billing admission (including explicit no-steps evidence) is separately P0/human-only and blocks suggestions requiring `github_hosted`. Verified PR owner-approval waits are human-only, never retried. Explicit `github.verified_approval_wait=true` on a fresh `waiting` or `pending` job creates a distinct human-approval card **without** misclassifying it as an unassigned runner; absent independent verification, a waiting/pending job remains a normal wait. P0 unassigned GitHub queued runner jobs receive a *human-action-required* review target 2 hours from initial observation, not an unapproved runner restart. P1 workflow failure or missing runtime progress receives a read-only diagnosis target 12 hours from first observation. Targets are advisory, not Notion deadlines or automatic claims.
- **Issue-first inspection:** Pass a complete JSON result from the existing `scripts/zsentinel_product_issue_inbox.py` collector as optional `issue_inbox` (or omit/null to disable). Up to eight P0/P1 non-QA issues appear with canonical GitHub links and existing PR candidates to inspect *first*. The original collector's partial/degraded/unavailable/truncation/denial flags and observation age remain visible; no GitHub assignee/PR is treated as a live claim. This is an inspection list, not an automatic eligible queue.
- **Independent delivery:** At most five P0 → P3 file-disjoint candidate tasks are proposed, without modifying queue ownership. Required CI/review/merge/privacy/device/security/finance gates remain independent. A recommendation is **never** an authorization to edit an owner's files or dispatch a job.
- **Operator view:** Up to three bounded lines per active incident: *problem*, *action/owner*, *status and target review*. Machine output explicitly states no mutations/claims/CI retries/deployment. Historical absence and CI-green alone are never recovered.
- **Bounded work:** One 1 MiB securely opened, inode-bound UTF-8 JSON input with duplicate-key rejection, up to 250 tasks/claims, 150 sourced inbox issues, 200 incident ledger entries; no background job, automatic CI loop or runaway retry. Refuses malformed gates/paths, duplicate work IDs, unauthorized inputs and invalid/symlink local state.

### Release boundary

The script is useful offline immediately after a source checkout. It is **not running autonomously in production** until an authorized integration owner connects a complete read-only source collector, controls private state storage, coordinates conflict-free live claim acquisition, and promotes the reviewed, exact-head CI-approved change through the existing serialized zCloud release gate. Self-hosted runner [P0 #1326](https://github.com/Zennay/zCloud/issues/1326) and private hosted account admission [#1523](https://github.com/Zennay/zCloud/issues/1523) remain separate human-controlled incidents; neither is fixed or bypassed by this script. No QA suite was created or manually executed for this change.

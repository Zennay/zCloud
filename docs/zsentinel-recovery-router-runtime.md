# zSentinel · Runtime incident router (product-only)

This is an **executable, local recovery/routing decision engine**. It consumes a trusted, fresh canonical zSentinel diagnosis snapshot alongside current queued work and live-verified active claim scopes; emits a compact three-line-per-incident operator view or machine-readable JSON, and optionally persists a private deduplicated incident ledger. It does not pretend to own the control-plane writer. This is a deliverable for [PWQ-433](https://app.notion.com/p/3fe9e19ac95581aab21ceae956954b08), guided by policy [PR #1562](https://github.com/Zennay/zCloud/pull/1562).

## Start (source-only, no production writes)

```bash
python3 scripts/zsentinel_recovery_router.py /secure/operator-observations.json --format text
python3 scripts/zsentinel_recovery_router.py /secure/operator-observations.json --state /secure/zsentinel-ledger.json --format json
```

For a browser-ready view without contacting a server, choose `--format html` and redirect stdout to a **private operator-owned local file** (e.g. `> /secure/zsentinel-status.html`). The cockpit shows incidents, P0/P1 GitHub issue/PR links, queue suggestions, source freshness, incomplete claims and exact-job reconciliation. It contains no JavaScript, tracking, network requests or privileged buttons. File handling/privacy of the redirected output belongs to the operator.

The optional `--state` ledger stores incident fingerprints and observed timestamps locally with permissions `0600`, atomic replacement and strict size/schema limits. Use a **trusted private directory**, never a live queue/SQLite, checked-in repo path, shared mount or an untrusted symlink. It does not call GitHub, Notion or the VPS, start any timer, enqueue work, acquire a task, rerun CI, alter any worker, or deploy. Feed it new complete observations only after upstream independent collectors/owners have gathered them. One invocation provides one decision snapshot. Actual activation as a production service awaits the serialized runtime owner.

## Direct live cockpit: no hand-created evidence bundle

The new `scripts/zsentinel_live_cockpit.py` is a complete, **one-shot** CLI product. It now also **automatically samples recent GitHub CI runs and relevant queued/failed jobs**, so a specific job ID is not needed to obtain initial GitHub blocker observations. It reads the real existing SQLite `portfolio_queue` using `mode=ro` / `PRAGMA query_only=ON`, merges a bounded public GitHub issue/PR inbox and optional pinned GitHub jobs, then renders a responsive **Dutch offline dashboard** in one command:

```bash
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --project supa \
  --format html > /private/operator-only/zsentinel.html

# Optional exact existing workflow job to diagnose (not dispatch):
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud \
  --job cloud:39000012345:117000123456:0123456789abcdef0123456789abcdef01234567 \
  --format json

# Skip all automatic GitHub issue and CI discovery entirely:
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --skip-github-issues --skip-ci-discovery --format text
```

**These are examples, not claims that the example IDs/database exist.** The default live command uses a bounded read-only GitHub CI discovery source (`scripts/zsentinel_github_discovery.py`): two separate bounded GitHub Actions windows (queued jobs first, failed workflows second) across all workflow event types, each listing at most five runs and inspecting at most two of them with ten jobs per run. Only a *specific* queued unassigned job older than 30 minutes or a completed failed/timed-out/action-required job becomes a candidate for the source-bound router. Job/run/commit identities must match GitHub. Incomplete/permission-limited sampling is **not** interpreted as zero CI incidents; successful completed jobs are not treated as evidence of live VPS recovery. Use `--skip-ci-discovery` to disable this GET source; `--job` pins an exact run/job to fetch independently. Discovery does not start, rerun, cancel, label, assign or restart anything.

 Use a private, existing operator-owned output directory and existing readable DB file, without changing VPS services. `--project` is repeated up to three times, must exist in canonical `projects.json`. Optional `--job` is limited to one pinned existing run/job/commit per project; GitHub details must match exactly. The issue collector is read-only, credential-safe, no-redirect, capped at three API calls per selected project. The CI discovery reader uses up to six GitHub GETs per project (two status-filtered run windows plus at most two job pages for each). The exact pinned-job collector uses up to two additional GETs per pinned or discovered job. The default combined bound for three projects is at most 33 GitHub GETs per invocation (18 discovery + 9 issue/PR + 6 exact-job reads), with no background polling. Credentials are never printed. The command does **not poll, daemonize, create a watch notification, or dispatch GitHub Actions**.

The queue observer in `scripts/zsentinel_queue_observer.py` also runs standalone:

```bash
python3 scripts/zsentinel_queue_observer.py --database /path/to/existing/zcloud.sqlite3
```

It reads one bounded SQLite transaction (up to 2,500 active rows), groups queued/claimed/running/verifying items by project and priority, and surfaces up to 20 of the worst claim-churn/possibly expired owner-lease observations with opaque hashed references. It never returns raw queue identifiers, credentials, lease tokens or task criteria, and it never changes `attempts` or an active lease. **Claim attempts count claims, not failures.** Any malformed or oversized active inventory is incomplete, never an all-clear or retry permit. The HTML cockpit shows queue pressure as a separate section; **stale (>5 minutes) or incomplete coverage suppresses individual queue risk cards**. The source is not sufficient to prove which repository paths a current worker owns.

The direct command deliberately leaves Notion, live worker heartbeats, complete file-level claims, and actionable queue eligibility as **unknown** rather than inventing them. Therefore it can display real GitHub and queue evidence but it cannot propose new owned work or assert worker/service recovery. Add independently obtained canonical fresh observations via `zsentinel_recovery_router.py` when a privileged scheduler owner has approved the reader and supplied complete reliable provenance. The offline output is not a deployment.

## Optional automatic GitHub read-only refresh

A second executable entrypoint now refreshes the **exact already-known GitHub run/job IDs** before generating the same recovery decisions:

```bash
python3 scripts/zsentinel_recovery_refresh.py /secure/operator-observations.json \
  --registry projects.json --state /secure/zsentinel-ledger.json \
  --read-product-issues --issue-project cloud --format html \
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
  "resources": {
    "source": "trusted_readonly_vps_snapshot",
    "observed_at": "2026-10-19T10:29:00Z",
    "vps": {
      "cores": 6,
      "load_5m": 1.2,
      "available_memory_mib": 4096,
      "available_swap_mib": 1024,
      "free_disk_gib": 20
    }
  },
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
    "requires_vps": false,
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
- **Runtime claim integrity:** Missing runtime heartbeat incidents require both `runtime.claim_identity_verified=true`, a bounded `runtime.claim_id` and an integer `runtime.claim_epoch`. Without them, the incident is counted as unattributed; no cross-claim dedup, worker ownership assumption or automated recovery is permitted.
- **Incident identity:** Stable local SHA-256 key over project, kind, exact commit and GitHub run/job identity when present. A job that disappears from a later snapshot is marked `not_observed_needs_reconciliation`, **never automatically resolved**. Incoming optional prior state is schema-checked; ledger saturation refuses truncation.
- **Prioritization:** Verified GitHub-hosted billing admission (including explicit no-steps evidence) is separately P0/human-only and blocks suggestions requiring `github_hosted`. Verified PR owner-approval waits are human-only, never retried. Explicit `github.verified_approval_wait=true` on a fresh `waiting` or `pending` job creates a distinct human-approval card **without** misclassifying it as an unassigned runner; absent independent verification, a waiting/pending job remains a normal wait. P0 unassigned GitHub queued runner jobs receive a *human-action-required* review target 2 hours from initial observation, not an unapproved runner restart. P1 workflow failure or missing runtime progress receives a read-only diagnosis target 12 hours from first observation. Targets are advisory, not Notion deadlines or automatic claims.
- **Real SQLite pressure observation:** Existing database schema and active work are read in one read-only query transaction. The result explicitly separates claim counters from failures, possibly expired leases from authorized cleanup, and partial/stale source coverage from a zero count. This includes operational evidence for open [zCloud issue #677](https://github.com/Zennay/zCloud/issues/677) without taking its writer scope.
- **Issue-first inspection:** Pass a complete JSON result from the existing `scripts/zsentinel_product_issue_inbox.py` collector as optional `issue_inbox` (or omit/null to disable). Up to eight P0/P1 non-QA issues appear with canonical GitHub links and existing PR candidates to inspect *first*. The original collector's partial/degraded/unavailable/truncation/denial flags and observation age remain visible; no GitHub assignee/PR is treated as a live claim. This is an inspection list, not an automatic eligible queue.
- **Capacity governance:** Every self-hosted/VPS-dependent candidate requires a trusted **at most 3 minutes old** read-only VPS snapshot including cores, 5-minute load, available memory, swap and disk. It is withheld when 5-minute load exceeds 1.5× cores, available memory falls below 1 GiB, swap below 256 MiB, free disk below 2 GiB, or evidence is missing/stale. `vps_resource_admission` explicitly distinguishes unknown, stale, overloaded and *advisory* headroom. Suggestions not requiring self-hosted compute must explicitly set `requires_vps=false` instead of relying on unknown destination. Resource headroom never authorizes a worker claim, runner restart or deploy.
- **Independent delivery:** At most five P0 → P3 file-disjoint candidate tasks are proposed, without modifying queue ownership. Required CI/review/merge/privacy/device/security/finance gates remain independent. A recommendation is **never** an authorization to edit an owner's files or dispatch a job.
- **Wear OS escalation feed:** Machine JSON also includes at most five P0-first `watch_alert_candidates` **only** for incidents with `human_escalation=true` (e.g. verified billing admission, genuine unassigned runner, verified owner approval). Stable incident-derived `alert_id` supports downstream idempotency. `delivered=false` and `watch_delivery_performed=false` are deliberate: this CLI never contacts a watch, schedules a notification or marks alerts delivered. Only the separately authorized watch integration may perform and acknowledge push.
- **Operator view:** Up to three bounded lines per active incident: *problem*, *action/owner*, *status and target review*. Machine output explicitly states no mutations/claims/CI retries/deployment. Historical absence and CI-green alone are never recovered.
- **Bounded work:** One 1 MiB securely opened, inode-bound UTF-8 JSON input with duplicate-key rejection, up to 250 tasks/claims, 150 sourced inbox issues, 200 incident ledger entries; no background job, automatic CI loop or runaway retry. Refuses malformed gates/paths, duplicate work IDs, unauthorized inputs and invalid/symlink local state.

### Release boundary

The script is useful offline immediately after a source checkout. It is **not running autonomously in production** until an authorized integration owner connects a complete read-only source collector, controls private state storage, coordinates conflict-free live claim acquisition, and promotes the reviewed, exact-head CI-approved change through the existing serialized zCloud release gate. Self-hosted runner [P0 #1326](https://github.com/Zennay/zCloud/issues/1326) and private hosted account admission [#1523](https://github.com/Zennay/zCloud/issues/1523) remain separate human-controlled incidents; neither is fixed or bypassed by this script. No QA suite was created or manually executed for this change.

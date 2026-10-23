# Read-only worker diagnostic endpoints

**Issue:** [#1341](https://github.com/Zennay/zCloud/issues/1341)

The following GET endpoints are observations only:

- `GET /api/worker-debug`
- `GET /api/runner-targets`
- `GET /api/task-claims`
- `GET /api/worker-preflight`

The first two use `runner_worker_targets(..., reconcile=False)`. The default `reconcile=True` path can create missing `runner_workers` rows and update a provider field, so it must not be used from a GET diagnostic handler. Their responses explicitly report `read_only_observation: true`, `sqlite_reconciliation_performed: false`, and `queue_mutations_performed: false`.

`GET /api/task-claims` is also read-only: it calls `task_claims(..., prune_expired=False)`, whose SELECT filters expired leases without deleting their rows. It reports `read_only_observation: true`, `sqlite_mutations_performed: false`, and `expired_claims_pruned: false`. Expired-lease cleanup remains with the scheduler/control paths, not a diagnostic GET. Its existing trusted-client authorization remains required.

The worker/claim diagnostic paths now use `connect_readonly()`: a SQLite URI with `mode=ro` plus `PRAGMA query_only=ON`. This applies to `runner_worker_targets(..., reconcile=False)`, the event/command lookups inside `worker_debug_report()`, and `task_claims(..., prune_expired=False)`. It rejects a missing database instead of creating one, and denies accidental SQL writes even if a read helper changes in the future. It does **not** use `immutable=1` because the operator must observe live SQLite/WAL changes. The normal `connect()` path and explicit reconciliation/pruning for authorized control/scheduler calls are unchanged. A diagnostic GET can still report unavailable SQLite when the database is absent or its read-only/WAL prerequisites are not satisfied; do not silently retry through a writable connection.

The claim-fingerprint code used by `GET /api/worker-preflight` also had a hidden `DELETE FROM task_claims` in `_coordination_claim_rows_locked()`. Its observational path now passes `prune_expired=False`, filters leases with `WHERE lease_until>?` and uses `connect_readonly()`; the authorized atomic preflight/acquisition path retains the explicit `prune_expired=True` default. Creating/updating a worker preflight is still a separate authorized POST write. Active-claim fingerprints are computed from the same ordered **live** rows in both modes, without GET-side expiration cleanup. Malformed/missing source state fails rather than switching to a writable GET. The HTTP GET response declares `read_only_observation: true`, `sqlite_mutations_performed: false`, and `expired_claims_pruned: false`; the existing `ok` field continues to mean the recorded preflight is still usable, **not** that this GET granted new ownership or authorized work.

The shared pure-SELECT read-models also now use this connection: `runner_targets`, `runner_status`, `runner_worker_statuses`, `improvement_loop_state`, `_current_global_slot_map`, `portfolio_queue_allocation`, `portfolio_queue_items`, `portfolio_queue_current_for_slot`, `portfolio_queue_has_project_assignment` and the DB quick-check in `coordination_vps_health`. This covers the normal status/runner-live/queue observer helper chain, while leaving `connect()`, claim acquisition, scheduler allocation, queue administration, runner reconciliation and authenticated configuration writes intact. These are read-model protections, **not** permission to restart workers, clear claims or deploy.

A follow-up extends the same hard read-only connection to the remaining pure-SELECT helpers reached by worker and admin status views: `_worker_recovery_hold_until`, `_autonomy_runtime`, `_latest_autonomy_signal`, `_feature_flag_row` (when no caller transaction is supplied), `config_audit`, and `watch_last_run`. The existing feature-flag writes, runtime-hold updates, autonomy scheduler and configuration administration still use their separate writable paths. Only read helpers were changed; errors from these reads do not grant claim/release authority.

The remaining read-only queue and worker polling paths now follow the same connection contract: `portfolio_attention_items`, `portfolio_execution_lanes`, `GET /api/runner-commands` (pending browser commands), `GET /api/activity` and `GET /api/host-history`. Their response shapes and existing access restrictions do not change. Browser polling no longer opens a write-capable SQLite connection merely to SELECT command/queue or activity rows.

**Do not generalize to every GET endpoint:** `GET /api/snapshot` is a separate, legacy localhost-only command that intentionally persists monitor data; it is not a read-only diagnostic and is not covered here. Its method/authorization must be migrated with its callers if changed. Neither that stateful route nor `GET /api/resource-governor` is claimed fixed by this PR.

Project watch and dashboard history reads are covered as well: `live_receipt_status` (including the complete receipts/coverage pair), `history`, `watch_activity` and `compact_history` use the same read-only SQLite connection. `GET /api/project-state-receipts` now reads project receipts through `connect_readonly` too. These changes preserve response data, status checks and authentication; they do not write or renew a project receipt. Receipt creation/updates remain separate authorized paths.

The existing `GET /api/worker-scaling` report opens telemetry through a hard read-only SQLite URI. Its `source_status`, `source_complete`, `source_reasons`, `source_coverage`, `telemetry_rows` and `window_empty` fields distinguish an observed empty time window from an unavailable/incomplete source. A missing `runner_events` table/database is `unavailable`, not an empty healthy fleet. Missing `runner_targets` or a project target row sets desired worker count to unknown and disables scaling recommendations instead of inferring the configured fleet from observed slots. If the `alerts` table is unavailable, `duplicate_work_prevented` is `null`, not a false zero. Telemetry timestamps must include an explicit timezone; naive timestamps fail closed rather than being interpreted in the host's local timezone.

**Scope boundary:** this document does not assert that every GET route in the server is read-only. On the current main branch, `GET /api/resource-governor` still calls the default cleanup-capable `resource_status(connection)`. Its separate non-mutating display option and route call are present in [PR #1549](https://github.com/Zennay/zCloud/pull/1549), which remains an independent, unmerged integration candidate. Do not treat that endpoint as fixed on main or live until its own required CI, review and release gates complete.

This does **not** make a GET response proof of a live queue lease, file ownership, successful work completion, or runner recovery. Control paths that legitimately reconcile state retain their existing behavior; this change does not disable those write paths or change authorization.

No queue/claim rows are changed, no worker is dispatched or restarted, and no CI workflow is manually retriggered by the diagnostic endpoints.


## Source-backed alert and live-runner diagnostics

A standalone `runner_worker_statuses(...)` lookup now defaults to
`runner_worker_targets(reconcile=False)`. Earlier, only aggregate
`runner_statuses()` explicitly opted out, while the standalone status helper
could still INSERT/UPDATE `runner_workers` when used from a diagnostic path.
`GET /api/runner-live` preserves its existing runner and Firefox fields and
additionally reports `read_only_observation=true`,
`sqlite_reconciliation_performed=false`, and
`expired_claims_pruned=false`. Authorized POST runner-control and preflight
writers retain their explicit reconciliation behavior.

`GET /api/status` constructs the operator incident panel from
`enhancements.incident_center()`. The alert list and stale-claim
verification inside that panel previously opened the generic commit-capable
SQLite connection. The incident/alert observers now use their own existing-file
SQLite URI (`mode=ro`) plus `PRAGMA query_only=ON`; they close the
connection without committing. The incident emission and sampler paths retain
`_db_connect()` with authorized writes.

If the alerts table/database is missing, unreadable or corrupt, the operator
incident response exposes `alerts_source_status="unavailable"`,
`alerts_source_complete=false` and an explicit recovery instruction; an
unavailable alert source is **not** a clean zero-alert observation.
`GET /api/v1/alerts` exposes the same source status and completeness fields
while preserving the existing `time` and `alerts` fields. A normal empty
source is `observed` with an empty array. Both the incident panel (max 80
recent alerts) and v1 alert API (max 20 important alerts) also disclose
`alerts_window_has_more` and `alerts_window_complete`. They obtain one
extra row to detect an incomplete bounded window; a missing source reports
`null` for `has_more` and `false` for completeness rather than fabricating
an empty complete snapshot. These fields describe the **returned alert
window**, not all historical alert evidence or a live worker recovery.
The legacy `GET /api/alerts`
remains an array for backward compatibility and does not carry a source
receipt; use the incident panel or v1 route when completeness matters.

Claim-conflict incidents receive a separate bounded evidence disclosure:
`claim_conflict_source_status` is `observed` when all relevant canonical
task-claim lookups were readable, `partial` for legacy or unverifiable claim
identities/clocks, `not_applicable` when the observed alert window contains no
claim-conflict alert, and `unavailable` if the alert source itself cannot be
read. `claim_conflict_source_complete` is false whenever the alert source or
a relevant claim observation is unknown. An unverifiable conflict stays visible
and a fixed Dutch owner action explains that the claim must be checked before
closure. These are observations, never proof of task ownership or permission
to release a claim.

No GET here renews, expires or releases a claim, authorizes a worker, or writes
an incident. These changes are source-only on this PR until independent review,
exact-head mandatory CI, protected-main integration and authorized deployment
are completed.


### One coherent incident/claim read window

The incident panel now samples up to 80 alerts and checks the matching
claim-conflict leases under **one deferred read transaction** on a single
existing-file, query-only SQLite connection. This prevents a claim expiring
or being replaced between separately opened alert and claim database reads
from silently mixing two source moments. The connection is closed without a
commit and is never used to claim, renew, expire or release task ownership.

`list_alerts()` and `_claim_conflict_is_active()` accept an optional
already-established read connection for this snapshot; their independent
callers continue to use separate read-only connections. If the snapshot
fails, any partially collected alert incidents are discarded and source
status is `unavailable`, while independently observed worker and service
incidents remain visible. Bounded alert truncation and unknown/legacy lease
evidence retain their own explicit warning fields. This is a local database
snapshot, not a synchronized GitHub, Notion or runner claim receipt.


## Worker-scaling coverage and heartbeat freshness

`GET /api/worker-scaling` remains read-only, but a throughput window is not proof that every configured worker is currently observable. Each worker now reports `last_heartbeat`, `last_heartbeat_age_seconds` and `heartbeat_freshness` (`fresh`, `stale`, `future`, `unknown` or `no_heartbeat`). A heartbeat is fresh only when it is no more than 150 seconds old at the report's fixed observation time. Desired worker capacity is bounded to 64 slots for this report; an out-of-range value produces `insufficient_data` without allocating or enumerating an unbounded slot range.

Each project exposes `worker_coverage`: desired versus observed slots, missing slots, stale heartbeat slots and unknown heartbeat slots. The scaling assessment returns `insufficient_data` rather than `single_worker`, `useful_scaling` or `diminishing_returns` when any configured slot is absent from the selected window or any desired worker lacks a fresh heartbeat. Historical throughput metrics remain visible, but they cannot turn incomplete or stale worker evidence into a current scale recommendation. Malformed heartbeat intervals are excluded from observed time; an unparseable latest heartbeat remains unknown.

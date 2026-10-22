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

The existing `GET /api/worker-scaling` report opens the telemetry database through SQLite `mode=ro`; it remains an observation-only report.

**Scope boundary:** this document does not assert that every GET route in the server is read-only. On the current main branch, `GET /api/resource-governor` still calls the default cleanup-capable `resource_status(connection)`. Its separate non-mutating display option and route call are present in [PR #1549](https://github.com/Zennay/zCloud/pull/1549), which remains an independent, unmerged integration candidate. Do not treat that endpoint as fixed on main or live until its own required CI, review and release gates complete.

This does **not** make a GET response proof of a live queue lease, file ownership, successful work completion, or runner recovery. Control paths that legitimately reconcile state retain their existing behavior; this change does not disable those write paths or change authorization.

No queue/claim rows are changed, no worker is dispatched or restarted, and no CI workflow is manually retriggered by the diagnostic endpoints.

# Read-only worker diagnostic endpoints

**Issue:** [#1341](https://github.com/Zennay/zCloud/issues/1341)

The following GET endpoints are observations only:

- `GET /api/worker-debug`
- `GET /api/runner-targets`

Both use `runner_worker_targets(..., reconcile=False)`. The default `reconcile=True` path can create missing `runner_workers` rows and update a provider field, so it must not be used from a GET diagnostic handler. The endpoints return explicit `read_only_observation: true`, `sqlite_reconciliation_performed: false`, and `queue_mutations_performed: false` metadata.

The existing `GET /api/worker-scaling` report opens the telemetry database through SQLite `mode=ro`; it remains an observation-only report.

This does **not** make a GET response proof of a live queue lease, file ownership, successful work completion, or runner recovery. Control paths that legitimately reconcile state retain their existing behavior; this change does not disable those write paths or change authorization.

No queue/claim rows are changed, no worker is dispatched or restarted, and no CI workflow is manually retriggered by the diagnostic endpoints.

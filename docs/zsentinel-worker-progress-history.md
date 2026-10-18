# zSentinel worker progress history

The zCloud worker dashboard used to expose only the latest browser runner event; later heartbeats could hide an earlier `generation-progress` signal. [Issue #1342](https://github.com/Zennay/zCloud/issues/1342) documents the diagnostic gap. This feature adds a **dedicated, authorization-gated, SQLite read-only endpoint** without changing runner telemetry ingestion, claims, allocation, queue, services, or user prompts.

## GET endpoint

`GET /api/worker-progress-history?project=cloud&slot=1&limit=24`

Access uses the existing `/api/worker-debug` policy: a local loopback request, or an already authorized remote request. `project` must be a known canonical project; `slot` is 1–8; `limit` is 1–40. HTTP 400 is returned for invalid selections and HTTP 403 for unauthorized remote requests. There is no new unauthenticated public read route. The response uses `Cache-Control: no-store` through the normal JSON response handler.

The endpoint presents separate **latest event**, **latest prompt**, **latest generation-progress** and **latest completion** receipts. It includes a bounded recent history, SQLite monotonic event IDs, UTC timestamp validity, reported assistant character count (not text), and distinctions between progress after the latest prompt and progress after the last completion. A normal heartbeat no longer erases the stored progress reading.

The report **never** includes conversation URLs, tab IDs, raw titles, generated text, free-form reasons, error messages, browser tokens, runner arguments or claim metadata. Unknown browser event names become `other_browser_event`; timestamps lacking UTC offset or far in the future are explicitly marked invalid/future instead of silently treated as fresh.

All database reads use a separate `mode=ro` SQLite connection and `PRAGMA query_only=ON`. They do **not** use `server.connect()`, which may perform schema reconciliation or expiry cleanup. Missing/unavailable database is reported as `available: false`, not as an empty successful stream. The event history has a hard bounded result size and fixed project/worker scope.

## Offline operator view

```sh
python3 scripts/zcloud_worker_progress_history.py --db history.db --project cloud --slot 1 --limit 24
```

This is read-only and emits compact JSON. Exit status 3 means the database or source is unavailable.

## Trust boundary

These are **browser-declared telemetry observations only**. `claim_bound=false`, `claim_epoch_verified=false`, `browser_progress_is_independent_proof=false`, `worker_resumed_verified=false` and `deployment_verified=false` are always returned. A progress event after the latest prompt **does not establish** that it belongs to the current portfolio queue claim. The actual browser producer does not durably record an authenticated queue claim ID on each event. This implementation intentionally does not fabricate a binding from timestamps or force any run.

Future authorized work may introduce signed claim generations at telemetry ingestion. Until then, these histories are operator diagnostics only, not an automated basis for merging, worker health admission, or restarting. [zCloud issue #1341](https://github.com/Zennay/zCloud/issues/1341) about other GET endpoints performing DB writes remains separate.

No QA, regression, or test suite was added or manually executed. Existing protected-main, independent owner review, GitHub-hosted CI and serialized VPS release gates continue to apply. Code committed ≠ merged, and a merge ≠ deployed.

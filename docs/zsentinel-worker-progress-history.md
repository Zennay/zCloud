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
# Mobile-friendly, self-contained offline HTML report (no external assets)
python3 scripts/zcloud_worker_progress_history.py --db history.db --project cloud --slot 1 --format html > /tmp/worker-progress.html
```

The default format is compact JSON; optional `--format html` creates a standalone mobile-readable operator cockpit with separate event, prompt, progress and completion cards, timestamp validity and a bounded recent event table. All rendered dynamic values are HTML-escaped; it contains no external scripts, fonts or network requests. Exit status 3 means the database or source is unavailable.

## Existing Activity source #1519 integrated into the live read-model (#1518)

The legacy GET /api/activity originally selected the latest 80 local SQLite events globally. One noisy project could occupy the entire response and hide activity from quiet projects. This branch integrates the **production source only** from existing [PR #1519](https://github.com/Zennay/zCloud/pull/1519), not its QA or validation files, and wires its bounded round-robin per-project selector into the existing Activity backend while preserving the old array-of-full-events response shape.

It also adds GET /api/activity-page with source-limited metadata, per-project keyset cursor and explicit unsynchronized-GitHub coverage. One project can be selected or the canonical registry can be used; unknown projects and malformed cursor/page scope are denied. The backend creates one SQLite deferred read snapshot per request, reads sorted per-project event streams and closes the transaction before writing the HTTP response. The composite event index (project, ts DESC, id DESC) is created only by the **normal authorized database initialization**, not by a GET. Legacy malformed timestamps cause the existing Activity array route to fall back to the old bounded behavior, whereas the explicit page route refuses corrupt time evidence.

The paged metadata-only route now publishes only the four currently observed producer
event kinds (`commit`, `milestone`, `firefox-recovery`, `firefox-crash`).
Unexpected future, invalid or free-form database kind values are emitted as
`other_local_event` while preserving the same source timestamp, ID cursor and
pagination position. This prevents a raw string in a legacy `kind` column
from being reflected as user-facing event text and does not infer that the
event disappeared. The event IDs used as keyset cursors now also require
bounded canonical ASCII characters (`A-Z/a-z/0-9/_ . : / -`, without spaces);
a malformed/free-form ID produces an explicit unavailable page rather than
returning uncontrolled ID text. Direct read-only VPS observation found the
existing ~5,000 event IDs within this format, without reading event content.
The older full-event legacy endpoint is unchanged.

This is a genuine local-feed fix, not proof of GitHub PR/CI synchronization. The independently open [Activity browser UI PR #1550](https://github.com/Zennay/zCloud/pull/1550) covers opt-in GitHub evidence, still subject to its normal gates. Original PR #1519 remains unchanged and unmerged. No QA files were copied or manually run; no production SQLite/service/runner has been modified.

## Atomic queue allocations and swap-safe new worker admission (#588)

The portfolio allocation read model previously assembled worker positions with one independent SQLite connection **per global slot**, followed by separate connections for persisted ordinal mappings and queued candidates. When a scheduler released a worker in slot 1 and claimed another in slot 2 between those reads, an operator could see both at once even though no committed SQLite state contained both assignments. The allocator now opens **one SQLite deferred read transaction** and collects all active worker slots, the persisted ai_global_slots map and queued candidate rows from one coherent database snapshot. It then preserves the existing latest-updated owner-per-slot semantics, project-local ordinal reservation and the same candidate ordering; no claim or SQLite state is mutated while rendering this read model.

A second admission risk was observed on the connected host on 18 October: multiple Firefox content processes consumed >1 GiB each and the configured 4 GiB swap was almost full. The existing memory guard could still report positive new-worker capacity if MemAvailable later rose while that configured swap remained exhausted. It now sets new_worker_capacity=0 when **configured swap** of at least WORKER_SWAP_MIN_TOTAL_MB has less than WORKER_SWAP_MIN_FREE_MB free, and surfaces swap_exhausted=true in existing worker memory status payloads. Hosts that deliberately run without sufficient swap keep their existing extra 512 MiB RAM reserve and are not automatically prevented from admitting workers indefinitely. Already claimed/running/verifying jobs are **not cancelled**, and no service or browser process is killed; the existing critical-RAM, pinned-claim and recovery-hold policies remain authoritative.

The already-authorized `GET /api/worker-debug` response now includes a compact `new_worker_admission` record explaining why **fresh** browser slots are blocked. It reports remaining RAM and swap (MiB), a boolean `swap_exhausted`, recovery-hold deadline if any, active admission capacity and one bounded reason code (`configured_swap_exhausted`, `oom_recovery_hold` or `insufficient_memory_headroom`). It explicitly says `existing_workers_preserved=true`; this is advisory status, never a manual kill/restart instruction. Invalid/unreadable /proc metrics expose `swap_exhausted=null` rather than accidentally reporting healthy swap. No new remote management route or credentials are introduced.

This resolves the **torn allocation read** aspect of [issue #588](https://github.com/Zennay/zCloud/issues/588), **not** its independently reported allocator-underfill/reconvergence hypothesis. That latter issue requires a separate release-owner investigation before declaring #588 complete. Current source commits are not proof of production effect: changes require the regular current-main CI, review and serialized release.

## No-write diagnostic endpoints (zCloud issue #1341)

The same delivery branch repairs existing GET behavior that previously wrote state while an operator merely inspected the control plane:

- `GET /api/runner-targets` and `GET /api/worker-debug` now pass `reconcile=False` to the existing `runner_worker_targets` read model. Worker startup/provider synchronization remains available in existing authorized writer paths; diagnostics no longer create `runner_workers` rows or replace provider/conversation values.
- `GET /api/task-claims` uses `task_claims(prune_expired=False)`: only nonexpired claims are shown, but old rows are not deleted by an HTTP GET.
- `GET /api/worker-preflight` now computes the same active-claim fingerprint without its previous implicit `DELETE FROM task_claims`, using `prune_expired=False` through the existing reusable read path. Authorized writer preflight/claim cleanup behavior is not removed.
- `GET /api/resource-governor` now calls `project_runtime.resource_status(...,prune_expired=False)`; expired resource leases remain excluded from the response, while cleanup stays in explicit transaction-aware lease admission/recovery paths.

This change separates **observing** from **reconciling**; GET routes still retain their previous access controls and payload shape. Scope is the specific GET-side state mutations identified, not a blanket claim that every endpoint or unrelated diagnostic is read-only. The underlying `project_runtime.py` single optional flag is disjoint from the receipt and lease admission hunks of the independent runtime consolidation PR #1547; reconcile it with that branch under the usual serialized source/review gate. No production SQLite has been modified as part of this source PR.

## Management-request authorization: no forgeable browser headers (#1266)

The production backend's existing `action_request_allowed(handler)` now accepts management actions **only** from the actual local loopback peer (`127.0.0.1` / `::1`) or an IP present in the pre-existing `.action-allowed-ips` administrator-managed allowlist. A nonallowlisted remote peer is denied regardless of `Host`, `Origin`, `Referer` or `Sec-Fetch-Site`; these headers may be sent by arbitrary non-browser clients and must never constitute authorization. No new token, secret, forwarded-address trust, special bypass, or permission grant was introduced.

This is a **source-level trust fix**, not a claim of the actual internet ingress path. A reverse proxy that forwards unauthenticated internet clients to backend loopback may still cause the server to observe a loopback peer; that ingress configuration must be reviewed independently before calling the deployed management surface secure. Existing remote dashboard controls that relied only on the spoofable same-origin-header fallback will be denied until their real peer is explicitly trusted by the authorized operator or a separately reviewed authenticated transport exists. This is intentional fail-closed behavior. All preexisting admin/session/release boundaries continue to apply; no live ingress or firewall configuration was modified.

## Trust boundary

These are **browser-declared telemetry observations only**. `claim_bound=false`, `claim_epoch_verified=false`, `browser_progress_is_independent_proof=false`, `worker_resumed_verified=false` and `deployment_verified=false` are always returned. A progress event after the latest prompt **does not establish** that it belongs to the current portfolio queue claim. The actual browser producer does not durably record an authenticated queue claim ID on each event. This implementation intentionally does not fabricate a binding from timestamps or force any run.

Future authorized work may introduce signed claim generations at telemetry ingestion. Until then, these histories are operator diagnostics only, not an automated basis for merging, worker health admission, or restarting. [zCloud issue #1341](https://github.com/Zennay/zCloud/issues/1341) about other GET endpoints performing DB writes remains separate.

No QA, regression, or test suite was added or manually executed. Existing protected-main, independent owner review, GitHub-hosted CI and serialized VPS release gates continue to apply. Code committed ≠ merged, and a merge ≠ deployed.

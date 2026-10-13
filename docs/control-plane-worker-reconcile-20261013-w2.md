# zCloud control-plane worker roster reconciliation (read-only)

This operator diagnostic fills the gap between **allocated scheduler slots** and
**observed `/api/runner-live` worker activity**. It does not reuse an active
control-plane writer, modify Firefox, create tabs, send prompts or restart runners.

Run only on the host that has access to the authoritative zCloud SQLite database:

```bash
python3 scripts/zcloud_control_plane_worker_reconcile.py \
  --db /home/ubuntu/zennay-cloud/history.db
```

For safe offline regression fixtures:

```bash
python3 scripts/zcloud_control_plane_worker_reconcile.py \
  --db /tmp/isolated-history.db --runner-live-fixture /tmp/runner-live.json
```

The CLI returns **one bounded JSON object**. Exit code `0` means observed
allocation is internally consistent; `2` means attention, degraded, or unknown.
`authorizes_mutation` and `browser_generation_verified` are **always false**.
The `evidence_mode` field is `offline_fixture` when supplying a JSON fixture and
`loopback_http` for the live localhost route, on both success and failure.
A green `offline_fixture` report is **not proof of live worker continuity**;
a green live report is not proof that ChatGPT accepted a submitted prompt.
This is a triage aid, **not** a deployment or force-start approval.

## Sources and classification

- Each bounded `BEGIN` read transaction gets `ai_global_slots`,
  `runner_workers`, `runner_targets` (configured worker_count), pending
  worker commands, and active `portfolio_queue` claim ownership using SQLite
  read-only mode.
  It does not initialize, delete, vacuum, migrate, checkpoint, commit or claim.
  The API GET is **bracketed by two coherent SQLite read snapshots**; if the
  roster, command set, or queue claim ownership changes mid-request, the report
  becomes `unknown` instead of falsely reporting a missing tab during normal
  reassignment. Snapshot equality also includes internal slot `assigned_at`,
  private queue `queue_id`/`updated_at`, and pending-command sequence watermarks
  to expose an A→B→A reassignment during the HTTP observation. These internal
  identity/watermark fields are **never emitted in the diagnostic JSON**.
- A localhost-only HTTP GET (or supplied fixture) loads at most 256 KiB of
  `/api/runner-live` JSON. Parsing explicitly bounds nesting to 64 levels
  before Python JSON decoding (escaped quotes/brackets remain legal). Redirects,
  duplicate JSON keys, exponent-overflow/non-finite numbers, unpaired Unicode
  surrogate escapes, missing identities, non-boolean activity flags and
  inconsistent/missing schemas fail closed. The urllib opener explicitly disables
  process-environment HTTP proxies so local runner status cannot be relayed to
  an unintended external HTTP_PROXY endpoint. For real HTTP observations,
  `/api/runner-live` must also carry a timezone-aware server `time` no
  more than 30 seconds from the local host clock; stale/replayed, future,
  missing, or naive timestamps return `unknown` instead of green. Explicit
  `--runner-live-fixture` observations bypass the freshness gate because
  fixture results are **offline**, not live-VPS evidence.
- `active_worker_not_observed`: selected worker not in the observed roster.
  **Does not prove a Firefox tab is missing.** Check browser tabs and the
  extension-to-worker binding before creating a new tab.
- `worker_activity_unknown`: missing identity/activity proof; cannot safely
  trigger a retry.
- `allocation_without_target` / `allocated_but_not_runnable`: control-plane
  consistency issues that require ownership-aware operator review.
- `active_observation_without_persisted_slot`: the HTTP read-model reports
  `active=true` for a worker omitted from the persisted global slot map.
  This is **attention**: manual/bootstrap allocation can be temporarily valid;
  it does not prove a tab is present or justify closing a browser session.
- `queue_claim_allocation_mismatch`: a current live queue claim points at
  a *different project* than the project assigned to the same global slot.
  This is stronger evidence than an absent browser heartbeat: queue and
  scheduler disagree even without any Firefox tab observation.
- `multiple_live_queue_claims`: more than one current claimed/running/
  verifying queue item occupies a global worker slot. No task IDs or titles
  are included in output.
- `live_queue_claim_without_global_slot`: a current claimed/running/
  verifying task points at a global slot absent from `ai_global_slots`.
  This is a strong lead when queue work is claimed but no worker starts,
  but may be an ordinary scheduler transition; classify as attention only.
  More than one current claim on that orphan slot is a degraded collision.
- `allocation_without_live_queue_claim`: queue claim absent or expired for
  a persisted global slot. This is **attention**, not definitive failure;
  bootstrap/manual worker slots can temporarily have no queue claim.
- `queue_claim_deadline_unverifiable`: malformed/naive claim expiry is
  degraded, not silently treated as an active claim.
- `allocation_without_project_target` / `allocation_outside_projected_worker_count`:
  a global scheduler slot is selected for a missing project or local ordinal
  outside the runtime projector's effective per-project count. The runtime
  expands the configured count to the **number of allocated project workers**,
  but iterates worker ordinals `1..effective_count`. For example, the only
  allocation being `cloud::w2` with count 1 still disappears, whereas both
  `w1` and `w2` allocated with configured count 1 are projected correctly.
  Do **not** interpret the omission as proof of a missing Firefox tab.
- `worker_count_expanded_by_allocation`: scheduler allocations require the
  runtime to project more workers than the configured dashboard count. This is
  **attention only**, not an incorrect/missing worker or restart permission.
- `worker_not_live`: selected worker is reported offline or stale even if
  its configured `active=true` flag says it is allocated. A `generating=true`
  flag **cannot** override a stale/offline classification.
- `generation_stalled`: the real API explicitly declares a stalled generation,
  even if `generating=true`. It is not healthy busy evidence.
- `worker_state_unknown`: missing/unexpected live state fails closed; a live
  state carrying >90 seconds of event age is contradictory and similarly denied.
- Live worker ID and slot number must match exactly: `cloud::w2` claiming
  `worker_slot=1` is rejected rather than silently counted as `cloud::w1`.
- `pending_dispatch_over_two_minutes`: a start/push/force/new-chat command
  remains `pending` past 120 seconds; output counts commands but never prints
  command results or actors. `pending_age_unverifiable` denies invalid/future
  timestamps instead of treating them as recently healthy.
- Verified `live`+fresh busy generation or sending is counted only **after**
  explicit stalled/offline/inconsistent state checks. Neither this count nor a
  quiet `live` state proves real progress in a ChatGPT browser.

Output deliberately omits `conversation_id`, prompts, SQLite metadata,
claim keys, raw command/error bodies and raw HTTP responses. When API, schema
or SQLite evidence is incomplete, the command returns `state=unknown`.

## GET read-only storage boundary

The tool uses SQLite `mode=ro` and `PRAGMA query_only` and only performs a
localhost `GET /api/runner-live` outside offline-fixture mode. On current
`server.py`, this specific route passes `reconcile=False` to the worker
projection; **not every other GET endpoint is read-only** (tracked by
[issue #1341](https://github.com/Zennay/zCloud/issues/1341)). This tool
does not query `/api/runner-targets` or `/api/worker-debug`. Static code
review does not replace an exact-head temporary SQLite or permanent-VPS
write-denial proof; neither this diagnostic nor success status can authorize
DB/service/browser mutation.

## Known boundary and handoff

This runs independently of PR #580/PWQ-41's serialized `server.py` writer and
does not overlap active Worker-v2 Firefox scripts. It cannot establish exact
Firefox tab counts, browser `generating` DOM state, authenticated ChatGPT send,
or release readiness. A zCloud self-hosted job was observed on 13 October
([noted in the Notion handoff](https://app.notion.com/p/3e79e19ac955819e9ccee5bec98bbb9c));
issue #1326 still tracks remaining queue routing uncertainty. Live acceptance
requires an independent, exact-head, read-only VPS run of **this** tool; other
VPS job success is not evidence that its output is correct.

The initial snapshot establishes an observable baseline for the future
control-plane overview (#789) and worker-attention routing (#942), without
implementing a competing mutable runtime or modifying their owners' branches.

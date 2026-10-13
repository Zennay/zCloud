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
allocation is consistent; `2` means attention, degraded, or unknown.
`authorizes_mutation` is **always false**. This is a triage aid, **not** a
deployment or force-start approval.

## Sources and classification

- Each bounded `BEGIN` read transaction gets `ai_global_slots`,
  `runner_workers`, `runner_targets` (configured worker_count) and pending
  worker commands using SQLite read-only mode.
  It does not initialize, delete, vacuum, migrate, checkpoint, commit or claim.
  The API GET is **bracketed by two coherent SQLite read snapshots**; if the
  roster or command set changes mid-request, the report becomes `unknown`
  instead of falsely reporting a missing tab during normal reassignment.
- A localhost-only HTTP GET (or supplied fixture) loads at most 256 KiB of
  `/api/runner-live` JSON. Parsing explicitly bounds nesting to 64 levels
  before Python JSON decoding (escaped quotes/brackets remain legal). Redirects,
  duplicate JSON keys, missing identities, non-boolean activity flags and
  inconsistent/missing schemas fail closed.
- `active_worker_not_observed`: selected worker not in the observed roster.
  **Does not prove a Firefox tab is missing.** Check browser tabs and the
  extension-to-worker binding before creating a new tab.
- `worker_activity_unknown`: missing identity/activity proof; cannot safely
  trigger a retry.
- `allocation_without_target` / `allocated_but_not_runnable`: control-plane
  consistency issues that require ownership-aware operator review.
- `allocation_without_project_target` / `allocation_outside_worker_count`:
  a global scheduler slot is selected for a missing project or an impossible
  per-project worker index (e.g. `cloud::w2` while target worker_count=1).
  The real runtime target projector may omit this slot; do **not** interpret
  the absence as proof of a missing Firefox tab.
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

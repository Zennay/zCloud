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

- A single `BEGIN` read transaction gets `ai_global_slots` and
  `runner_workers` using SQLite read-only mode. It does not initialize,
  delete, vacuum, migrate, checkpoint, commit or claim anything.
- A localhost-only HTTP GET (or supplied fixture) loads at most 256 KiB of
  `/api/runner-live` JSON. Redirects, duplicate JSON keys, missing identities,
  non-boolean activity flags, inconsistent or missing schemas fail closed.
- `active_worker_not_observed`: selected worker not in the observed roster.
  **Does not prove a Firefox tab is missing.** Check browser tabs and the
  extension-to-worker binding before creating a new tab.
- `worker_activity_unknown`: missing identity/activity proof; cannot safely
  trigger a retry.
- `allocation_without_target` / `allocated_but_not_runnable`: control-plane
  consistency issues that require ownership-aware operator review.
- `idle_over_five_minutes`: **attention only**. An idle worker may be awaiting
  its next task or still legitimately active elsewhere. No automatic force.
- Busy generation or sending takes priority over idle-age observations.

Output deliberately omits `conversation_id`, prompts, SQLite metadata,
claim keys, raw command/error bodies and raw HTTP responses. When API, schema
or SQLite evidence is incomplete, the command returns `state=unknown`.

## Known boundary and handoff

This runs independently of PR #580/PWQ-41's serialized `server.py` writer and
does not overlap active Worker-v2 Firefox scripts. It cannot establish exact
Firefox tab counts, browser `generating` DOM state, authenticated ChatGPT send,
or release readiness. Real acceptance still needs a safe on-VPS read-only
snapshot once zCloud's self-hosted listener is recovered (issue #1326).

The initial snapshot establishes an observable baseline for the future
control-plane overview (#789) and worker-attention routing (#942), without
implementing a competing mutable runtime or modifying their owners' branches.

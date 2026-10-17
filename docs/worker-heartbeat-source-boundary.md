# Worker generation heartbeat evidence boundary (offline foundation)

This draft validator is a **deny-only fixture**, not an authorization or a production
health monitor. It does not read live VPS SQLite, verify attestation signatures,
authenticate workers, or establish where its input originated.

Inputs are JSON snapshots supplied by a separate, trusted observation collector.
A self-declared `source: vps_sqlite_observed` is **not** proof that the snapshot
really came from VPS SQLite. Until a future trusted producer binds these bytes to
an authenticated runner invocation and immutable observation identity, a
successful validator result must not trigger recovery, restart, dispatch,
permission escalation, merge, deploy or queue mutation.

Run locally:

```sh
python3 -m unittest discover -s tests -p 'test_zcloud_worker_heartbeat_boundary.py'
python3 scripts/zcloud_worker_heartbeat_boundary.py /path/to/observation.json
```

Fields: `source`, `kind`, `worker_id`, `assignment_id`,
`generation_id`, `observed_at` (UTC `Z`), and `generation_started`
(boolean `true`). A passing sample must be at most 180 seconds old relative
to the checking host clock. Clock accuracy is a separate prerequisite.

An absent heartbeat means **unproven generation**, not proof the worker has
stopped. Notion assignments, browser prompt-sent acknowledgements, and GitHub
checks must be reported independently; they must never be merged into a
single synthetic active-worker signal.

This capability is intentionally file-disjoint from serialized control-plane
integration (#580 and #1089) and from existing freshness/claim owners.

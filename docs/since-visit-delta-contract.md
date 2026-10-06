# Since-visit delta contract

Status: control-plane read-model foundation. This change intentionally does not
wire the dashboard or mutate the runtime writer while the serialized
`server.py` / scheduler window is owned elsewhere.

## Goal

Support the roadmap item **“Wat is er veranderd sinds mijn laatste bezoek?”**
with evidence instead of inferred prose. A caller supplies a timezone-aware
visit timestamp and may optionally scope one project.

## Evidence sources

The reporter reads only:

- `project_state_receipts.observed_at`, CI status and validated commit identity
  for evidence-backed state transitions;
- a fixed allowlist of meaningful `runner_events` such as completed
  generations, queue results, recovery and blocker events.

Heartbeats and unknown event types are ignored.

## Safety and privacy

- SQLite is opened with `mode=ro` and `PRAGMA query_only=ON`.
- Timestamp comparisons use SQLite `unixepoch(...)`, so equivalent instants
  with different timezone offsets are classified by absolute time.
- The query window is closed on both sides: rows before the visit cursor and
  rows after the current observation time are excluded.
- Lookback is bounded to 31 days.
- Each source is capped at 2,000 rows and output at 50 projects.
- Project filters use a strict lowercase identifier grammar.
- Raw action, phase, blocker, next-gate, reason, error, evidence and prompt text
  is never selected into the output.
- CI status is emitted only through a small allowlist.
- Commit evidence is only emitted as a 12-character prefix when the source is a
  valid 40-64 character hex SHA.
- Missing required runtime tables/columns and naive/future/out-of-bounds caller
  timestamps fail closed.

## Output

`since-visit-v1` returns per-project categorical counts, the latest change
category/timestamp and a bounded latest-receipt CI/commit summary. It is a read
model, not a new source of truth.

Dashboard integration should persist a user/browser visit cursor separately and
pass that cursor to this reporter. Integration must preserve existing project
state and must not reinterpret noisy heartbeats as user-visible change.

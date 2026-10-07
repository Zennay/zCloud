# Queue seed reseed audit contract

`server.py` hydrates missing `portfolio_queue` rows from `portfolio_queue.seed.json` with
`INSERT OR IGNORE`. SQLite remains the live source of truth while a row exists, but an absent
runtime row can therefore be recreated from the seed on a later initialization.

## Audit semantics

- `present_runtime_row`: the seed queue ID already exists in live SQLite; `INSERT OR IGNORE`
  cannot replace its current status.
- `absent_reseed_risk`: the runtime row is absent while the seed would recreate it as
  `status=queued` and `eligible=true`.
- `absent_non_runnable`: the runtime row is absent but the seed would not recreate runnable work.
- The audit opens SQLite with `mode=ro` and `PRAGMA query_only=ON`.
- Output never includes completion criteria, source URLs, prompts or command payloads.
- Malformed/duplicate queue IDs and malformed project IDs fail closed.

`--require-safe` is an optional validation gate. Exit 3 means at least one currently absent seed
row would become runnable again if the seed hydration path runs. The audit itself never edits the
seed, SQLite, scheduler or queue.

Remediation is deliberately separate: either keep the terminal runtime row or retire/update the
seed entry through the serialized queue/writer lane. This read-only slice does not take ownership
of that writer authority.

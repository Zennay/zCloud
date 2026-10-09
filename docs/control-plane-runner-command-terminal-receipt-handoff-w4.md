# Runner-command result fencing — integration handoff

Status: **observed defect, not fixed**. Scope: `/api/runner-command-result` in
`server.py`; owned by serialized #580/PWQ-41 + #576 integration lane.
This document and PR #1221 do **not** authorize server, worker or live database changes.

## Exact evidence

PR #1221 predecessor head `67724046360259a1f0db5175ab03b50d5110d303`
was tested by GitHub Actions regression run
[37859691920](https://github.com/Zennay/zCloud/actions/runs/37859691920):
644 tests, 2 failures, both in the source-contract file. The failures are:

* callback UPDATE lacks `AND status='pending'` (terminal replay may overwrite prior outcome);
* callback converts the command ID with `int(... or 0)` without positive-ID prevalidation.

CPU diagnostic 37859692047 succeeded; dashboard access recovery
37859691946 failed separately. None of this proves a live attack or a
successful server endpoint integration.

## Owner acceptance contract

1. Authenticate/authorize the callback as currently required; do not
   weaken the existing loopback restriction.
2. Validate integer command IDs, rejecting null, booleans, negatives, zero,
   malformed strings and out-of-range values with a controlled 4xx response.
3. Accept only `completed` and `failed` states.
4. Atomically update only a matching **pending** command; report non-match
   without changing rows or leaking whether an ID exists.
5. Keep result truncation and existing storage constraints. Decide the
   response status for stale/duplicate receipts explicitly (e.g. a bounded
   conflict, or idempotent non-mutating success).
6. Test duplicate same-status reports, opposite-status late reports,
   scheduler-staled commands, unknown IDs, and concurrent attempts against
   the actual endpoint, not only the in-memory reference model.
7. Once the production change is implemented, remove the two
   `@unittest.expectedFailure` annotations in
   `tests/test_runner_command_terminal_receipt_boundary_w4.py`, make both
   tests green, and add behavioral endpoint assertions for the HTTP contract.
8. Require exact-head regression and owner review before integration; do
   not bypass #580/#576 gates or deploy from this draft branch.

Current PR #1221 contains only add-only offline reference/contract checks
and this handoff. It does not establish production readiness.

## Proposed minimal endpoint implementation (owner-only; not deployed)

The integration owner can preserve the existing loopback restriction and status
allowlist while replacing the ID parse and unconditional UPDATE with the following
explicit validation and atomic transition. This is illustrative integration
code, **not** an applied patch to `server.py`.

```python
raw_id = payload.get("command_id")
if type(raw_id) is not int or raw_id <= 0:
    return self.reply({"error": "Ongeldig command_id"}, 400)
command_id = raw_id

status = str(payload.get("status") or "")
if status not in ("completed", "failed"):
    return self.reply({"error": "Ongeldige status"}, 400)

with connect() as c:
    changed = c.execute(
        "UPDATE runner_commands SET status=?,updated_at=?,result=? "
        "WHERE id=? AND status='pending'",
        (status, now(), str(payload.get("result") or "")[:300], command_id),
    ).rowcount

if changed != 1:
    # Same bounded response for an unknown command and any already-terminal ID.
    # No second read is necessary; do not disclose command existence.
    return self.reply({"error": "Command niet pending"}, 409)
return self.reply({"ok": True})
```

The above chooses strict JSON integer IDs rather than coercing digit strings.
If existing runner clients send IDs as strings, the owner must first verify and
migrate that contract; do **not** silently deploy this stricter behavior.
The code also assumes `connect()` commits context-manager writes exactly as the
current endpoint does.

### Acceptance matrix for owner HTTP tests

| Request / database before | Expected HTTP | Database after |
| --- | --- | --- |
| localhost, integer ID 1 pending, completed | 200 | ID 1 completed, result capped at 300 |
| localhost, same receipt replay to completed ID 1 | 409 | Prior result and timestamp untouched |
| localhost, failed after completed ID 1 | 409 | Prior result and timestamp untouched |
| localhost, completed after scheduler marks ID 1 failed | 409 | Scheduler result and timestamp untouched |
| localhost, unknown positive ID | 409 | No rows added or changed |
| localhost, ID 0 / -1 / true / null / string | 400 | No rows changed |
| localhost, unsupported status | 400 | No rows changed |
| remote source IP (even with valid ID) | 403 | No rows changed |
| two competing terminal reports to pending ID | one 200, one 409 | Exactly one winner; no lost update |

Do not modify `updated_at` on rejected or duplicate receipts. Preserve the
300-character Python string slicing policy unless the owner explicitly revises
the public result contract. Repeat the race test with independent SQLite
connections and the real HTTP handler; the offline reference tests only
demonstrate intended SQL semantics.

## Producer compatibility audit (default-branch source inspection)

Two known callback producers have now been inspected on the default branch:

* `public/zcloud-worker.user.js`, `commandResult(commandId, resultStatus, result)`,
  posts `{command_id: commandId, status: resultStatus, result}` through `gmRequest`.
  It treats any rejected HTTP request as `false`. Thus a 409 duplicate is
  **not automatically treated as an idempotent success** by this producer.
* `firefox-extension/background.js`, `commandResult(commandId, status, result)`,
  serializes `command_id` with `JSON.stringify` into a `fetch` using
  `mode: "no-cors"` and ignores transport failures. The client cannot reliably
  inspect the 409 body/status under this mode.

Neither producer converts the supplied ID to a numeric type at the immediate
callback callsite. Before integrating the strict `type(raw_id) is int` guard,
the owner must trace the command-fetch/dispatch paths in both producers and
verify the command ID type received from the server (including legacy
commands). A mismatch could reject legitimate receipts and cause stale
commands. The offline fixture does not establish this compatibility.

### Retry and observability decision

If the owner retains the proposed `409` for stale/duplicate receipts, ensure
workers do not respond with unbounded retry loops or falsely classify an
already-final command as an operational failure. An alternative is a **non-mutating
200** for terminal/unknown IDs, but only if the API deliberately specifies this
behavior and limits disclosure. Either policy must preserve the atomic
`WHERE id=? AND status='pending'` update and must be tested through both
producers. Do not change producer retry behavior or the live runner protocol from
this draft test-only PR.

## Producer ID type trace (verified 2026-10-09)

Tracing the consumer logic beyond the immediate callback callsites resolves part
of the earlier compatibility question:

* **Violentmonkey/userscript**: `handleCommands()` fetches
  `/runner-commands`, filters and sorts using `Number(command.id || 0)`,
  then explicitly assigns `const id = Number(command.id || 0)` before passing
  `id` to `commandResult(id, ...)`. Normal delivered finite IDs therefore
  serialize as JSON numbers. This path is compatible with a strict JSON
  integer contract for ordinary IDs; the owner must still reject
  fractional, unsafe, nonfinite or overflowed values.
* **Firefox extension**: `pollCommands()` fetches the same route and forwards
  `command.id` directly to `pushProject`, `startProject`,
  `pauseProject`, `drainProject`, or `newProjectChat`, which invoke
  `commandResult`. There is **no ID coercion** in this path: compatibility
  depends on the JSON type emitted by the server's `/runner-commands`
  endpoint. Require a real fetched response fixture/test before enforcing
  strict server-side integer-only validation.
* `Number(...)` in the userscript does **not** prove a safe integer:
  `Number.isSafeInteger(id)` and `id > 0` should be asserted for incoming
  commands before dispatch. That producer change is separately owned and
  must not be silently folded into this draft branch.

This trace is a **static source audit**, not live production evidence. The
integration owner should pair it with a real `/runner-commands` JSON sample
and a POST callback contract test on the same exact-head build.

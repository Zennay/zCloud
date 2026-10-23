# zCloud state receipts: lossless source validation (#1276)

A project receipt may declare the current delivery phase, next action, exact
commit identity, blocker and source provenance. These strings are *durable
evidence*, not truncated UI previews. Silently truncating one of these fields
would record an incomplete next step or source identifier and could mislead
the portfolio controller.

## Shipped source contract

`project_runtime.validated_receipt_text()` normalizes the same optional
empty values as the prior writer, and enforces the existing character limits
**before any INSERT**:

| Field | Maximum Unicode characters |
| --- | ---: |
| `phase` | 240 |
| `action` | 1,000 |
| `commit_sha` | 80 |
| `blocker` | 1,000 |
| `next_gate` | 1,000 |
| `source` | 300 |

At the precise limit, the complete string persists unchanged. Above the
limit, or with embedded NUL or invalid Unicode, a `ValueError` is raised.
Errors contain only the field name and limit, never untrusted field content.
The stored receipt format, `ci_status` choices and existing JSON evidence
budget stay compatible. No existing receipt rows are rewritten, migrated or
deleted.

`scripts/zcloud_runtime.py receipt` uses the shared validator **before
opening or initializing SQLite**. It also checks project contract,
`ci_status`, JSON root type and serialized evidence size in the same
pre-admission stage. Other direct callers are guarded in
`project_runtime.record_receipt` itself, even if they bypass the CLI.

## Operator expectations

- Reduce oversized free-text details at the producer; keep the precise full
  source pointer and specific next action within the documented limits.
- For large evidence, store an approved separate evidence artifact and
  provide a bounded reference, never assume the receipt auto-shortens it.
- Treat rejection as *no new receipt*, not as a successful state update.
- Do **not** change production SQLite, retry stalled jobs, force workers,
  restart the self-hosted runner or deploy as a consequence of this source
  patch. Real release still depends on zCloud serialized `#580/#1089`,
  GitHub protected-main, the existing full CI/review gate, runner `#1326`
  and hosted billing `#1523`.

## Integration coordination

This is an independent fix to the `record_receipt` insertion block, plus
CLI pre-admission. Open historic PR `#1270` also changes
`project_runtime.py` but owns `receipt_coverage` and pool slot validation.
Keep both branches separate; inspect exact patches at integration time and
merge only once current owners and mandatory gates permit.

This note documents product behavior; it is **not** a QA plan or test suite.


## Observation timestamp integrity

`observed_at` is validated by `project_runtime.validated_receipt_timestamp()` before a receipt INSERT. Inputs must be bounded (at most 48 characters), timezone-aware ISO-8601 timestamps without NULs. Valid offsets are normalized to canonical UTC with a trailing `Z`; naive, malformed or more than 120 seconds future-dated observations are rejected. Historical timestamps remain valid for explicitly dated evidence, but are not proof of current state.

The CLI accepts `receipt --observed-at 2026-10-22T19:30:00Z`. When omitted, it captures the current UTC time during pre-admission and passes that exact normalized value to the core writer. Invalid timestamps are rejected before SQLite is opened or initialized. Direct callers receive the same core validation, so bypassing the CLI cannot insert a malformed or future-dated observation.

# Bounded retry/backoff control-plane contract

zCloud's VPS-first standard requires known transient failures to retry automatically within safe bounds, while exhausted or novel failures escalate instead of looping indefinitely.

This slice defines the deterministic policy before the production queue writer is changed.

## Policy v1

The contract distinguishes three bounded failure classes:

- `transient`: retry while budget remains, using exponential backoff capped at a maximum delay;
- `novel`: do not retry automatically; transition to `NEEDS_AI`;
- `terminal`: do not retry; transition to `FAILED_FINAL`.

Default retry budget is three consumed attempts. A transient failure with budget remaining produces `FAILED_RETRYABLE`; when the budget is exhausted it produces `NEEDS_AI`. The default backoff starts at 30 seconds and caps at 900 seconds.

The policy returns only a disposition, retry boolean, delay and bounded reason code. It does not carry logs, blocker text or decision payloads; those belong to the separate NEEDS_AI packet contract.

## Read-only readiness audit

`scripts/zcloud_retry_policy_contract.py` audits current `portfolio_queue` attempt counts and the real `portfolio_queue_finish()` source without modifying either.

The live report exposes only aggregate attempt buckets, active rows at/over the proposed retry budget, and bounded source-contract gap codes. It never emits queue IDs, titles, evidence or blockers.

The permanent-VPS proof opens `history.db` read-only with `PRAGMA query_only=ON`, verifies size + nanosecond mtime immutability and runs on the exact PR head behind the canonical VPS runner guard.

## Later writer integration

This branch does not touch `server.py`, queue scheduling, SQLite schema/state, browser automation, dashboard code or live services.

Once PWQ-41/#580 releases the serialized runtime-write window and durable correlation is available, the runtime writer should:

1. classify only explicitly known transient failures for automatic retry;
2. persist retry disposition and a not-before/backoff boundary;
3. refuse dispatch before the retry boundary;
4. escalate novel or exhausted failures through the versioned NEEDS_AI packet;
5. preserve `FAILED_FINAL` for explicitly non-retryable terminal failures;
6. prove migration/rollback and then switch the live proof to `--require-writer`.

The retry budget must never be inferred from timestamps or repeated chat messages; durable state and correlation remain authoritative.

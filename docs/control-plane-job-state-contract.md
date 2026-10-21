# Canonical control-plane job states

zCloud's VPS-first runtime standard names one canonical project-job vocabulary:

- `QUEUED`
- `RUNNING`
- `DONE`
- `BLOCKED`
- `NEEDS_AI`
- `FAILED_RETRYABLE`
- `FAILED_FINAL`

The current `portfolio_queue` writer predates that contract. It stores `queued`, `claimed`, `running`, `verifying`, `done`, and `dropped`; `portfolio_queue_finish()` accepts only `DONE`, `BLOCKED`, and `CONTINUE`. In particular, a BLOCKED completion is persisted as the legacy `dropped` state and there are no native queue transitions for `NEEDS_AI`, `FAILED_RETRYABLE`, or `FAILED_FINAL`.

## What this slice adds

`scripts/zcloud_job_state_contract.py` is an executable, read-only compatibility contract. It maps the bounded legacy queue states onto the canonical vocabulary, rejects unknown states, distinguishes retryable/final legacy failure by attempt count, inspects the real `portfolio_queue_finish()` source, and reports machine-readable gap codes without exposing queue IDs, titles, blockers, evidence, prompts, or other free-form production text.

The permanent-VPS proof opens `history.db` with SQLite `mode=ro` and `PRAGMA query_only=ON`, verifies size + nanosecond mtime immutability, runs focused regressions on the exact PR head, and emits only aggregate state counts plus bounded contract metadata.

The proof intentionally does **not** use `--require-native` yet. It fails closed on unknown live storage states, but it reports the known native-writer gaps rather than pretending they are already implemented.

## Integration order

This branch does not modify `server.py`, scheduler/allocator code, browser automation, SQLite schema/state, or live services. PWQ-41 / PR #580 still owns the serialized control-plane runtime-write window.

After that window is free, the next writer slice should:

1. persist native `blocked`, `needs_ai`, `failed_retryable`, and `failed_final` states (with a backward-compatible migration/rollback path);
2. make retry disposition explicit and bounded, preserving attempts and durable correlation rather than inferring failures heuristically;
3. attach a compact NEEDS_AI packet containing the task, last good step, revision, retries, bounded diagnostics/evidence, blocker, and precise decision needed;
4. switch the permanent-VPS proof to `--require-native` only after exact-main compatibility and migration evidence are green.

The canonical mapping is a compatibility layer, not permission to collapse these states indefinitely. The acceptance target is a native lifecycle whose storage, API/read-model, receipts, retry policy, and operator UI all preserve the same semantics.

# NEEDS_AI escalation packet contract

zCloud's VPS-first execution standard says routine failures should be retried automatically within bounded policy, while exhausted or novel failures should escalate to AI with a compact, self-contained decision packet. This contract makes that packet executable before any runtime writer is changed.

## Schema v1

A `needs_ai_packet` object contains:

- `schema_version`: exactly `1`;
- `task`: the bounded task/job that needs intervention;
- `last_good_step`: the last verified successful step;
- `revision`: exact commit/build/branch revision identifier;
- `retries`: bounded retry count;
- `logs`: a short bounded list of relevant log references or excerpts;
- `evidence`: bounded structured metrics/evidence;
- `blocker`: the concrete failure/blocker;
- `decision_needed`: the precise AI/human decision required.

The validator caps packet size, text lengths, log count/length and evidence size/key count. Validation errors are machine-readable codes only; packet contents are never echoed.

## Read-only production baseline

`scripts/zcloud_needs_ai_packet_contract.py` audits recent `project_state_receipts` whose CI status is `failure` or whose blocker is non-empty. It looks only for a nested `evidence.needs_ai_packet`, validates any packet it finds, and emits aggregate counts. It never emits project IDs, actions, blockers, logs, queue IDs, evidence values or decisions.

The permanent-VPS proof opens `history.db` with SQLite `mode=ro` + `PRAGMA query_only=ON`, verifies byte-size and nanosecond-mtime immutability, and runs on the exact PR head behind the canonical VPS runner guard.

The proof is intentionally report-only for **missing** packets until a runtime writer owns this contract. It does fail if an existing packet is present but invalid, preventing silent schema drift.

## Writer integration gate

This slice adds no production writer and does not touch `server.py`, `project_runtime.py`, queue scheduling, browser automation, dashboard code, SQLite schema/state or live services.

After the serialized runtime-write window is free and durable queue/run correlation is available, a writer slice should:

1. create the packet only after bounded retry policy decides the job needs AI;
2. attach it to the durable receipt/state transition that becomes `NEEDS_AI`;
3. preserve exact revision + retry count and bounded diagnostic references;
4. make the packet visible through the sanitized operator read-model without logging raw private payloads;
5. switch this proof to `--require-complete` only when the writer transition and migration/rollback path are proven on exact main.

A packet is evidence for a decision handoff; it is not permission to bypass write claims, scope rules, safety gates, or human approval for consequential actions.

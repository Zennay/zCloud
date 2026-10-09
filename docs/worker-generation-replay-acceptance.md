# Worker generation replay — acceptance contract

This is a non-authorizing acceptance checklist for the zCloud browser-worker no-generation recovery path introduced by PR #1103 (userscript 1.3.17). It does not authorize queue, runner, service, or deployment mutation.

## Invariants

1. A successful `prompt-sent` acknowledgement **does not** count as `generation-started` or task completion.
2. When no generation starts by the existing 120-second generation deadline, recovery must target the **same assignment identifier and worker slot**. It must not allocate a second assignment or widen project/focus.
3. An active generation must not be replayed because of a delayed or missing secondary UI signal; successful detection cancels the no-generation recovery timer.
4. Repeated recovery attempts must not create concurrent submissions. A single worker slot has at most one in-flight prompt submission.
5. The fallback Firefox extension and primary Violentmonkey path use equivalent semantics. An extension-version mismatch must not silently downgrade the active 1.3.17 path.
6. A recovered session must continue to honor existing cooldown, assignment ownership, and anti-spam limits.
7. No recovery action marks a task Done, releases a claim, merges a PR, or changes production state without its separate existing authorization/gates.

## Deterministic test matrix

| Case | Setup | Expected evidence |
| --- | --- | --- |
| A | Prompt sent; generation begins before deadline | One submission, timer cancelled, no replay |
| B | Prompt sent; no generation by 120 s | Recovery runs; same assignment/slot replayed once at a time |
| C | Generation signal arrives at deadline boundary | No concurrent duplicate; serialized winner only |
| D | Recovery opens UI but composer is unavailable | Retry remains bounded; no false completion |
| E | User manually starts generation during recovery | Existing generation wins; no duplicate send |
| F | Browser reloads after prompt-sent | Rehydrated state does not create an unbounded replay loop |
| G | Primary userscript absent; Firefox fallback active | Same-assignment recovery, no stale version downgrade |
| H | Assignment is revoked/expired before replay | Replay suppressed; never revive expired ownership |

## Evidence required before acceptance

- Capture the exact PR head SHA and CI run IDs; distinguish head-bound checks from stale-base runs.
- Record `prompt-sent`, `generation-started`, timeout, and recovery events with assignment ID and slot, redacting prompt contents and credentials.
- Verify **zero parallel sends**, **zero cross-assignment replay**, and **zero unauthorized task-state changes** for cases A–H.
- Execute on the existing permanent runner route; avoid restarting an otherwise healthy zCloud service merely to run this test.
- Keep production rollout separate from test acceptance. Existing serialized control-plane gate owners (#580/PWQ-41 and #1089 at time of writing) retain authority.

## Scope

This document is an executable test specification for an independent regression owner. It does not claim tests were executed or production acceptance was granted.

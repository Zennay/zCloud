# Control-plane partial-observation merge contract

Status: proposal; offline reasoning only. This document grants **no** deploy, retry, worker-start, queue-write, or production authority.

## Problem

A dashboard can receive individually truthful but incomplete observations from GitHub, SQLite, VPS telemetry, and browser workers at different times. Treating absent fields as negative observations, or combining the freshest field from each source into an apparently authoritative global state, can incorrectly present a worker as stopped, a queue as drained, or a release as safe.

## Deny-first contract

1. Preserve a distinct observation envelope per source: `source_id`, `observed_at_utc`, `collected_at_utc`, `subject_id`, `revision_or_cursor`, `field_presence`, `status`, and `trust_class`. Reject missing identities, impossible timestamps, and contradictory duplicate source records from the *same* revision.
2. Represent each field using three states: **present with value**, **explicitly unavailable**, and **not observed**. Do not translate `not observed` into `false`, `0`, `idle`, `success`, or `stopped`.
3. A full snapshot may replace only fields it explicitly claims to cover. A partial snapshot may update only named, present fields; previous values for other fields remain historical and must retain their original timestamps. Never extend their freshness using the partial snapshot timestamp.
4. Source-specific monotonic cursors may reject older records *within the same source and subject*. Do not impose an artificial total order between independent sources, clocks, or entities.
5. Contradictory statuses across sources remain **conflicting observations** with both provenances visible. Do not select the more optimistic status or infer which observer is authoritative from recency alone.
6. For freshness expiry, downgrade the affected field to **unknown/stale**, not an invented terminal state. A missing heartbeat is not proof that a worker has stopped; an old successful CI run is not proof that current main is release-ready.
7. Evidence may support an operator-facing diagnostic display, but a proposed action must pass its own current head-bound, identity-bound, permission-bound, owner-bound gate. Joining sources, clearing a conflict, and successfully parsing a response **never** confer authority.

## Reference cases

| Input | Permissible read-only projection | Forbidden conclusion |
| --- | --- | --- |
| GitHub check succeeds for SHA A; main advances to SHA B | Check success belongs to A | B passed |
| SQLite assignment exists; VPS heartbeat omitted from partial response | Assignment recorded, heartbeat unknown | Worker stopped; safe to redispatch |
| VPS CPU sample at T2, worker status at T1 | Each value with its own timestamp | Composite snapshot fresh at T2 |
| Browser claims `running`, SQLite says `pending` | Conflict with both source identities | Running proved; mutation allowed |
| Partial response contains `queue_depth=0` but no cursor/subject | Reject or quarantine depth observation | Queue drained |
| Previously observed worker heartbeat expires | Last-seen stale/unknown | Worker failed; automatic restart authorized |
| GitHub PR checks green but PR ownership conflicts | Green checks and ownership conflict | Merge or writer window released |
| HTTP 200 with missing fields | Explicit present values only, absent fields unknown | Missing safety predicates satisfied |

## Acceptance checks for future consumers

- Fixture tests must distinguish absent, null/unavailable, and explicit false/zero.
- Property tests must verify that a partial update never increases freshness of an untouched field.
- Reordering independent source arrivals must not cause an authorization transition.
- Replay of an older same-source cursor must not overwrite newer evidence.
- On ambiguous provenance, duplicate keys, conflicting owner claims, or clock skew, actions stay disabled while diagnostic views remain useful.
- Test successful read-only rendering separately from any privileged action gate; all synthetic fixtures must keep `mutation_performed=false`.

## Ownership and integration

This is an add-only design contract. It does **not** modify the runtime, queue, deployment workflows, serialization owners (#580/#576), or existing evidence-gate PRs. Any implementation in shared runtime paths requires a fresh owner check and review of the currently open control-plane PRs before editing.

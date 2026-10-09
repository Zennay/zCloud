# Control-plane status provenance — integration matrix (offline, 2026-10-09)

> **Scope:** read-only coordination artifact. Not a status implementation, owner claim, deploy approval, CI attestation or assertion of current production health. Do not use this document to admit a deploy, restart a worker, modify SQLite, or infer green from missing data.

## Authoritative sources and consumers

| Signal | Authoritative producer | Consumer | Missing / stale evidence |
| --- | --- | --- | --- |
| Current source revision | exact `main` SHA at check time | release / regression preflight | unknown; do not substitute a green result for an earlier SHA |
| CI verification | workflow run + attempt + head SHA + conclusion, with verified provenance | read-only observability, separately gated release preflight | unknown; no implicit retry or approval |
| VPS production state | environment-bound status for exact deployed SHA, when available | dashboard indicator | `unverified`, not `healthy` |
| Worker activity | timestamped runner/worker observation and bounded source freshness | operator overview | stale/unknown; configured slots are not active workers |
| Task-claim ownership | SQLite `(project_id, claim_key)` lease with current owner and expiry | task admission / read-only owner display | unknown; never silently release or steal |
| Operator admission | explicitly recorded approval from serialized deployment owners | privileged action gate only | denied; dashboard activity is not approval |

## Non-equivalence rules

1. A GitHub job marked `success` is **not** a production-health signal. Compare the exact commit, run ID, attempt and intended environment before reporting even read-only verification.
2. A locally rendered or mocked `allow` case does **not** grant production authority. Keep observation, simulated decision, and privileged admission separate.
3. A previously good production status does not establish freshness after a moving `main`, new deployment attempt, restart or missing status response.
4. A dashboard worker slot, queue entry, branch, claim name or recent heartbeat alone does not prove a worker performed a material action.
5. A missing claim or claim-list failure does not establish a free work item. Require an authenticated, atomic acquire response before ownership-dependent work.
6. Never expose raw commands, tokens, prompts, conversation IDs, SQLite evidence JSON, raw errors or claim metadata in overview responses.

## Deterministic offline acceptance fixtures

Each case must exercise a bounded fixture and assert both the displayed state and the absence of privileged actions:

| Fixture | Expected read-only state | Forbidden inference |
| --- | --- | --- |
| exact-main CI green; production status absent | CI verified; production unverified | deploy green |
| status SHA differs from current main | stale or mismatch | current release healthy |
| run ID matches but attempt differs | evidence mismatch | latest attempt verified |
| worker configured, no observed action | configured / activity unknown | worker productive |
| worker activity timestamp older than permitted freshness | stale | currently running |
| claim API unavailable during admission | ownership unknown / deny | claim available |
| same claim key in distinct project IDs | separate scopes | cross-project ownership |
| approval data unavailable | admission denied | operator approval assumed |
| API/JSON malformed, timeout or ambiguous null | unknown / deny | green by default |

**No fixture in this table authorizes network access to production**. Exercise fake IDs and isolated fixtures only.

## Coordination and exit criteria

- Live runtime/server writer: #580/PWQ-41; do not edit `server.py` or mutable control-plane paths while serialized ownership is active.
- Central eventstream: #776/#777; dashboard overview and action ratio consumer: #789. Reuse existing projections; do not add a duplicate writer or claim another owner's UI files.
- Task claims: #1207 (lease/concurrency), #1228 (identifier equivalence). Do not reinterpret their canonical contracts here.
- Production status and deploy evidence: #1204 and #1216; any privileged release remains blocked pending independent explicit owner authorization.
- Before integration: enumerate all open PRs and **paginate the unfiltered branch list**, compare changed paths, obtain ownership handoffs, validate the *exact final head* through the permanent self-hosted runner, then refresh main SHA and source evidence. Documentation alone is never an acceptance gate.

**Current result:** provenance requirements and negative fixtures documented; no test was executed, runtime changed or integration approved by this file.

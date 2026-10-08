# Independent deploy-gate witness protocol (2026-10-08)

This document is an **evidence-only operator handoff** for zCloud deploy-ops. It does not authorize deployment, repairs, PR promotion, metadata writes, workflow dispatch, or live service changes. It is intentionally separate from the ownership and code paths in #580/PWQ-41, #1089, #1108, #1110, #1111, #1115, and the dashboard-recovery owner (#581).

## Why a separate witness is useful

A green workflow on a historical SHA, a successful external probe, a stale Notion queue snapshot, and an offline receipt schema check are four different facts. None alone attests that **current canonical main** is the **candidate**, that a trusted permanent VPS runner executed an accepted gate, or that the **deployed** revision equals the approved revision. Treat absent, inconsistent or stale evidence as UNKNOWN, never as PASS.

## Read-only witness record

Capture one immutable, reviewable record per proposed release. A witness is **not** a release decision.

| Field | Required evidence | Fail-closed behavior |
| --- | --- | --- |
| observed_at_utc | UTC ISO-8601 time of read | Reject missing/ambiguous timezone |
| canonical_main_sha | Fresh remote main commit, full 40-hex SHA | Reject abbreviated, local-only, or previously cached identity |
| candidate_sha | Full immutable PR/release head SHA | Reject moving branch name as identity |
| candidate_base_sha | Fresh base SHA used in actual comparison | Reject stale-base without renewed comparison |
| changeset | Exact changed-path set plus ownership exclusions | Reject overlap with active owners, unknown paths, or absent inventory |
| acceptance_runs | Workflow name, run URL/id, event, conclusion, run head SHA, finish time | Reject pending, canceled, skipped-as-pass, and SHA mismatch |
| permanent_vps_runner | Verified labels [self-hosted,zcloud,vps] and runner provenance for applicable tests | Reject generic self-hosted identity or no positive proof |
| live_gate_snapshot | Fresh #580/PWQ-41 and #1089 state, mergeability, ownership, and timestamp | UNKNOWN or open means no production mutation |
| production_status | Fresh commit-status context on canonical main, distinct from PR checks | Reject historical success as new deploy proof |
| authorization | Explicit owning operator decision, exact SHA and permitted action | Default DENY; witness author cannot self-authorize |

Include only links, SHA identities, result categories and bounded timestamps; do not copy tokens, cookies, environment variables, workflow secrets, full PR bodies, logs with credentials, browser profile data, private queue records, or SQLite contents into the witness.

## Independent verification sequence

1. Read canonical `main` again immediately before producing the record. Bind all subsequent checks to that exact SHA.
2. Read open branches/PRs and the authoritative queue claims. Exclude paths and capabilities already owned by active workers, including PWQ-258 / Worker 2.
3. Compare the candidate to the freshly observed main; capture ahead/behind and the exact changed-path list. Any later main advancement invalidates the comparison.
4. Verify each accepted run's `head_sha`, event, workflow name, conclusion, and completion state against the **final candidate SHA**. A prior green commit does not transfer to a restacked head.
5. For any permanent-VPS assertion, use a trusted run's actual runner labels/identity; a job merely waiting in queue is not proof of execution.
6. Re-read serialized gate owners #580/PWQ-41 and #1089 just before a release decision. If either is open, stale, unknown or uncoordinated, record DENY and exit read-only.
7. Even after all checks are green, stop at witness output. Only the explicit serialized release owner may approve a bounded mutation and collect post-operation deployed-SHA/health/rollback receipts.

## Negative cases to exercise in review

- Historical success on SHA A presented for candidate SHA B → **DENY**.
- Healthy external API but failed privileged recovery workflow → record both observations; **do not infer outage or authorize restart**.
- Generic `self-hosted` label without permanent VPS identity → **UNKNOWN**.
- Accepted PR with 1 ahead / 3 behind and no fresh exact-main compare → **DENY**.
- Exact-head regression green while #580 or #1089 remains open → **DENY production mutation**.
- Dashboard API returns transient 503 during settling → capture bounded retry evidence, **never convert ambiguity into restart permission**.
- Missing ownership proof, incomplete run pages, stale queue metadata or unavailable API → **UNKNOWN / DENY**, not an invented PASS.
- Offline receipt validator passes its schema → **still DENY** until trusted live gate and post-deploy receipts are present.

## Handoff template

```text
witness_type: read_only_non_authorizing
observed_at_utc: <UTC>
main_sha: <40 hex>
candidate_sha: <40 hex>
candidate_base_sha: <40 hex>
changed_paths: <bounded paths or evidence link>
ownership_conflicts: <none | list | unknown>
exact_head_checks: <run-id:workflow:head-sha:conclusion>
vps_identity_evidence: <run link | unknown>
gate_580: <state/timestamp/source>
gate_1089: <state/timestamp/source>
production_status: <context/run/sha or unknown>
main_unchanged_on_recheck: <true/false/unknown>
release_authorized: false
merge_authorized: false
deploy_authorized: false
mutation_performed: false
next_owner: <named serialized gate owner>
```

This is a human-readable verification contract, not executable evidence collection or a substitute for exact-head regression. An operator must independently verify every source before making any release decision.

# Deploy-ops: observation versus authorization (2026-10-08)

This is a **non-authorizing**, operator-facing evidence contract. It changes no runtime, workflow, runner, service, queue or VPS state.

## Separate the signals

A public endpoint's HTTP status, internal API readiness, service-manager state, and SQLite health are *different observations*. Do not infer a public outage from one failed self-hosted recovery job; do not infer a need to restart from a transient 503 while the API is settling. Record the probe origin, monotonic start/end, timeout, exact URL class (public/internal; avoid query secrets), observed status category, and correlation to the candidate commit and run attempt.

## Triage and denial contract

| Observation | Classification | Allowed action |
| --- | --- | --- |
| Public read-only probe healthy; internal recovery step failed | Recovery regression, not demonstrated public outage | Read-only diagnostics; no restart |
| Internal API returns a single transient 503 while service/DB health is green | Ambiguous settling | Recheck read-only within bounded window; no restart |
| External network times out while internal probe is healthy | External-path ambiguity | Capture bounded redacted evidence; no privileged action |
| Both independent probes persistently fail | Unhealthy candidate, still not permission | Require explicit serialized owner approval, exact-current-main SHA, complete owner inventory, safety gates, rollback plan and witness |
| Probe origin, attempt ID or SHA missing/mismatched | Untrusted evidence | DENY privileged action |
| Any untrusted pull_request triggers recovery path | Untrusted trigger | DENY all VPS mutations; external read-only verification only |

## Evidence to retain

- Immutable canonical candidate/main SHA and checked-at timestamp; never accept historical main as current.
- Trigger event, repository, actor, ref, workflow run ID and attempt ID, verified independently from trusted context.
- External and internal probe results recorded independently; never collapse them into a single boolean.
- Explicit current statuses of #580/PWQ-41 and #1089, both serialized production gates.
- Redacted observations only; no tokens, cookies, environment dumps or full authenticated URLs.
- A decision of **DENY** if any field, owner, gate or runner proof is absent or ambiguous.

## Acceptance boundary

Issue #1135's privileged PR-triggered recovery workflow remains unresolved pending owner-specific fixes and exact-final-head validation. The established #581 owner controls healthy-dashboard fast-path logic. #1108 controls its nine planner/preflight/intent paths. This note grants no merge, deploy, recovery, dispatch or production authorization. Review against the exact current main and a fully paginated PR + PR-less branch inventory before adoption.
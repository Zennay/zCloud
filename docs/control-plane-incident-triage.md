# zCloud control-plane incident triage (operator-only)

> Scope: diagnostic decision support. This document does **not** authorize deployments, merges, Actions writes, service restarts, queue edits, claim releases, or resource-policy changes.

## Distinguish observation from authorization

Every triage record must capture **observed_at UTC**, exact repository **commit SHA**, relevant **run ID and attempt**, affected component, and source of the observation. A green run for a different SHA or attempt is not proof for the current release. Never label missing, stale, ambiguous or permission-denied evidence as healthy.

| Symptom | Classification | Safe first action | Explicit escalation gate |
| --- | --- | --- | --- |
| Dashboard API unreachable or stale | Availability / observability degraded | Inspect read-only API and current deployment evidence; check clock and sample freshness | Runtime change requires current serialized writer owner and approved deploy procedure |
| Worker reports prompt sent but never generation started | Dispatch stalled | Correlate exact assignment with generation-start/recovery telemetry; check current replay owner | Do not manually replay or alter assignments without current owner and dedupe proof |
| Two workers claim same capability | Ownership collision | Snapshot durable claim owners, lease expiry and timestamps without exposing tokens | Do not force-release a live claim; resolve through claim-owner coordination |
| Production check green on an older SHA | Evidence invalid | Mark release admission **unknown/deny**; request exact-head proof | No promotion until SHA, run attempt and environment all match |
| Host CPU pressure / control-plane slow | Capacity contention | Review read-only host pressure and scheduler projections | No unilateral systemd, priority, pool or browser mutations |
| PR ownership ambiguous or integration gate open | Coordination blocked | Enumerate open PRs, changed paths and PR-less branches; preserve owner | No shared-file edits, merges or deploys until serialized gate explicitly released |

## Read-only triage order

1. Identify the **current** main SHA and the precise deployed SHA independently. Record drift rather than assuming they match.
2. Obtain paginated open-PR inventory, changed-file ownership, and PR-less branches. A branch name alone is not sufficient file-ownership evidence.
3. Confirm the live serialized window (#580/PWQ-41 and current deploy successor) from current GitHub/Notion sources. Historic IDs in handoff notes are not authorization.
4. Compare matching run **SHA + ID + attempt + environment**. Treat cancelled, queued, timed-out, missing, or mismatched proof as **not verified**.
5. Correlate read-only telemetry by stable redacted identifiers; exclude raw prompts, chat IDs, tokens, credentials, and task payloads from incident reports.
6. Classify: `observed`, `unverified`, or `confirmed`. Only a current matching proof can support `confirmed`. Record owner, next safe action, and check timestamp.

## Escalation / rollback boundaries

- Severity **P0**: evidence of unauthorized production mutation, cross-project claim interference, or control-plane compromise. Stop further *proposed* promotions and immediately escalate to the active authorized owner; do not self-authorize rollback.
- Severity **P1**: control-plane unavailable, worker dispatch persistently stalled, or current release evidence invalid. Preserve observations; request owner-led remediation.
- Severity **P2**: read-model freshness gaps, UX degradation, or incomplete non-critical diagnostics. Open a narrowly scoped issue with exact evidence.

Rollback is itself a write to live state. Require the same explicit ownership and change authority as deploy; a diagnostic document, successful offline test, or historic CI success is never sufficient.

## Evidence handoff template

```text
observed_at_utc:
component:
symptom:
classification: observed | unverified | confirmed
main_sha:
deployed_sha: unknown | <sha>
run_id_attempt_environment: unknown | <id>/<attempt>/<environment>
source_urls:
redactions_applied: yes
current_owner_and_gate:
next_safe_read_only_check:
mutation_authorized: false
```

This runbook intentionally contains **no executable remote commands** and grants **no mutation permission**.
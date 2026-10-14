# Deploy-ops: offline rollback evidence checklist

This is an **operator review aid only**. It does not authorize a deployment, a rollback, a merge, a runner action, or modification of production. The canonical live release owner and current guarded runbooks remain authoritative.

## Before considering rollback

Record a bounded, immutable evidence bundle, without secrets:

- Candidate and previously deployed commits as exact 40-character Git SHAs; repository, environment, timestamp, and responsible operator.
- The **current** serialized deployment owner and any concurrent recovery/deploy incident; stop if ownership is ambiguous.
- The triggering regression and VPS verification run URLs, exact candidate SHA, final status, and execution attempt number.
- The deployment receipt URL, exact deployed SHA, and independent external health observation. Missing evidence means *unknown*, not *healthy*.
- Changes to service units, environment, database schema, state, and external dependencies since the previous release. Never assume code rollback reverses stateful migrations.
- An explicit decision whether restoring the earlier artifact can be done without destructive data loss.

## Decision boundary

A rollback should only be attempted through the existing approved, serialized, guarded live-release process after the current release owner has accepted the incident. An offline receipt or a successful unit test is never permission to execute. Do not fall back to unguarded SSH, manual root writes, direct SQLite mutation, browser restarts, or an alternative runner when the approved gate refuses.

If the previous artifact, identity, data compatibility, or current owner cannot be established, mark the decision **hold for human review** and preserve the evidence; do not guess.

## Evidence to collect after an authorized rollback

Capture immutable links and timestamps for:

1. Authorization/incident and release ownership transfer.
2. Exact workflow/run attempt and permanent VPS runner identity, with its prewrite checks.
3. Original deployed SHA, target rollback SHA, and observed resulting SHA.
4. Transaction outcome, including which changes were restored, skipped, or failed.
5. Independent post-change health checks and any remaining degraded functionality.
6. Whether queued autonomous work and worker activation remain paused or explicitly reauthorized.

Keep logs redacted (no API tokens, environment values, cookies, or personal information). Prefer evidence URLs and exact SHA values over pasting raw journal output.

## Deterministic offline decision examples

These examples are **review classifications, not automation inputs**. They deliberately
cannot produce a `deploy`, `rollback`, `restart` or `merge` decision.

| Observed evidence | Classification | Required next action |
| --- | --- | --- |
| Run succeeded but its head SHA differs from the candidate | STALE | Obtain exact-candidate evidence; no mutation |
| Run number matches but attempt number differs | MISMATCH | Reconcile exact run attempt; no mutation |
| Runner identity or production environment is absent | UNKNOWN | Escalate to release owner; no mutation |
| Previous artifact's digest or provenance is unverifiable | UNKNOWN | Hold for review; do not restore |
| Previous deployment predates a non-reversible schema migration | INCOMPATIBLE | Obtain data-safe recovery plan and human sign-off |
| External URL is healthy, but VPS service state is unverified | INCOMPLETE | Collect independent VPS proof; do not claim recovery |
| Candidate is verified but #580/PWQ-41 or #1089 has not released the serialized gate | GATE_CLOSED | Preserve evidence; no manual bypass |
| Rollback command exited zero but observed deployed SHA differs | FAILED | Escalate incident and record actual state |
| Both gates are released and every record is complete | REVIEW_REQUIRED | Only the current authorized production owner may decide through the guarded process |

### Evidence identity and replay checks

For every referenced GitHub run, record repository identity, workflow identity,
event type, immutable commit SHA, run ID, **run attempt**, terminal conclusion,
UTC observation timestamp and evidence URL. Link the artifact identifier and digest
to that same run attempt; reject evidence reused from a different attempt,
repository, workflow, environment or commit. A missing field, ambiguous owner,
expired receipt or later superseding run is an unresolved mismatch, even when
another dashboard says green.

Never copy unredacted CI output into incident evidence. A timestamp on its own
does not establish freshness; the live gate must bind its decision to a trusted
clock and the current exact-main revision. A successful review of this checklist
cannot open either serialized gate.

## Closeout requirements

- All required fields are recorded, cross-checked, and attached to the incident.
- A fresh regression and release-owner decision support any subsequent forward deployment.
- A failed rollback is escalated with the actual observed state; never label it as success merely because a command returned zero.
- Any missing evidence, mismatch, ambiguous source or runner, or nonterminal workflow remains an **open gate**.

This document deliberately includes **no runnable commands** and does not supersede production deploy workflows, receipt validators, or the existing recovery runbook.

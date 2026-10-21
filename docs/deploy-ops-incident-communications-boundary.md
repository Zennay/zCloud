# Deploy-ops incident communication boundary

This template is **communications-only**. It must not be used to approve a production change, conclude service recovery, or instruct a rollback. During an incident, the serialized production owners retain authority. Coordinate with their current issue/PR before publishing a customer-facing update.

## Internal status update (draft)
- Recorded UTC time:
- Incident reference and named communications owner:
- Named technical incident/production owner (independently verified UTC):
- Current observation: investigating / impact observed / monitoring / resolved **only with independently verified external health**.
- Affected service or capability (no speculation):
- First observed UTC and source:
- Confirmed customer-facing impact and evidence:
- Unknown impact or contradictory observations:
- Next planned communications checkpoint UTC:
- Link to incident evidence worksheet (no secrets):
- Source references, their capture times, and known limitations:

Separate **observed facts**, **hypotheses**, and **actions proposed** explicitly. Never claim recovery based only on a green GitHub Actions run, a successful deploy workflow, or a stale receipt.

## External status update (requires communications-owner review)
> We are investigating [verified customer-visible symptom] affecting [verified scope]. We first observed this at [UTC]. We will provide another update by [UTC]. We do not yet have a confirmed resolution time.

Only fill verified fields. If verification is missing, say what is unknown; do not speculate about cause, scope, security impact or resolution time. Strip internal hostnames, sensitive operational details, customer identifiers, tokens, session information, and links to private artifacts.

## Resolution message (not a deployment gate)
Publish only after an authorized operator independently verifies external production health and the communications owner reviews the wording:
- Verified scope and observation window (UTC):
- Exact externally observed healthy behavior and evidence capture UTC:
- Open residual risk and monitoring owner:
- Next follow-up and customer support channel:

A message marked “resolved” is **not** approval for deployment, merge, rollback, worker restart, queue edit, or any other mutation.

## Fail-closed escalation
1. Recheck the named live serialized production owners (currently coordinated through #580/PWQ-41 and #1089; numbers can change).
2. If ownership, evidence, or external health is ambiguous, leave the incident status as **investigating** and escalate to the active owner.
3. Preserve historical statements; issue timestamped corrections rather than silently rewriting published messages.
4. Require separate explicit authorization for any operational action in the existing deployment workflow.

**Non-authorizing defaults:** `release_authorized=false`, `merge_authorized=false`, `deploy_authorized=false`, `rollback_authorized=false`, `mutation_performed=false`.

This document does not alter runner, workflow, production, PR metadata, SQLite/queue, or browser state.

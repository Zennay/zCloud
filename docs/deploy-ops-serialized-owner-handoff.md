# Deploy-ops: serialized owner handoff (non-authorizing)

This procedure covers **coordination only** when a zCloud deployment or metadata gate changes hands. It is not a deploy approval, merge approval, rollback instruction, or a substitute for the production owner’s current runbook.

## Invariants

- Exactly one explicitly named human/worker owns the live serialized release gate at any instant. An owner may prepare independent read-only evidence while the gate is held by somebody else, but must not execute a competing production write.
- Owner intent is scoped to a repository, PR/issue, branch, **exact 40-hex head SHA**, target environment, and operation class (metadata edit / merge / deploy / rollback). A handoff cannot silently expand scope.
- Neither a green historical check nor a locally valid offline receipt grants authority. Evidence must be rechecked at the exact current head and current main immediately before any operation.
- Explicit `release_authorized=false`, `merge_authorized=false`, `deploy_authorized=false`, and `mutation_performed=false` remain defaults throughout a read-only handoff.

## Handoff protocol

1. **Freeze the outgoing lane**. Record the outgoing owner, gate identifier, pending operation, target head/base SHAs, workflow run URLs/IDs, runner identity, production receipt reference and outstanding dependencies. Stop any scheduled or queued write from the outgoing lane; do not terminate an unrelated worker.
2. **Verify absence of concurrent control**. Inspect active deploy workflows, open PRs/branches, existing owner claims and Notion/queue state. If two owners claim a gate, leave it closed; escalate for explicit arbitration rather than racing a write.
3. **Create a bounded handoff record**. Include old owner, proposed new owner, gate ID, exact repository + SHA, permitted operation class, remaining negative gates and timestamp. Redact secrets, tokens, environment files and sensitive production payloads.
4. **Require explicit acceptance** from the new owner before the old owner releases control. Silence, an expired lease, a merged PR or a passing workflow is *not* acceptance.
5. **Revalidate from live sources**. The accepting owner independently checks latest main, exact candidate head, required CI and permanent VPS proof, current production health and the current serialized gate holder. Any changed SHA, workflow, runner, target environment or gate claim invalidates previous evidence and returns to step 2.
6. **Only after external authorization** may the new owner follow the applicable protected production runbook. This document itself never authorizes a write. Preserve pre/post receipts and rollback ownership in the final handoff log.

## Stop conditions

| Condition | Safe outcome |
| --- | --- |
| Outgoing owner still has active write | Keep gate closed; no takeover |
| Head/base SHA changed | Invalidate prior checks and revalidate |
| CI incomplete, cancelled, stale, or wrong head | Keep gate closed; no deploy |
| Runner identity or VPS health unverified | Keep gate closed |
| Receipt offline-only or without verified external health | Evidence insufficient |
| Owner conflict or ambiguous acceptance | Escalate; no mutation |
| Metadata update references legacy gates | Require fresh line-scoped preview and compare-and-swap against exact live body |
| Rollback ownership unassigned | Keep gate closed |

## Handoff record template

```text
repository: Zennay/zCloud
gate: <issue/PR identifier>
operation_class: <metadata|merge|deploy|rollback>
target_environment: <environment>
outgoing_owner: <identity>
incoming_owner: <identity>
outgoing_freeze_confirmed: false
incoming_explicit_acceptance: false
main_sha: <exact 40 hex>
candidate_head_sha: <exact 40 hex>
verification_runs: <IDs and exact-head results>
permanent_vps_runner_verified: false
live_gate_owner_rechecked: false
rollback_owner: <identity or unassigned>
production_receipt: <reference or none>
release_authorized: false
merge_authorized: false
deploy_authorized: false
mutation_performed: false
```

This record describes evidence and ownership; it does not change permissions. Avoid automatically flipping authorization fields from a completed checklist.

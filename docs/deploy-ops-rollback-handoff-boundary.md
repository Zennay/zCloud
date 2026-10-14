# Deploy-ops rollback handoff boundary

This is an **operator handoff contract**, not a rollback procedure, a deployment receipt, or authority to mutate production. It is deliberately separate from the live release owners (#580/PWQ-41 and #1089), the evidence validation owners (#1111/#1113/#1115), and the operator decision record (#1114).

## When the handoff is required

Start a new handoff whenever post-deploy health fails, the observed deployed commit differs from the approved exact SHA, a required evidence source becomes unavailable, a live gate owner changes, or the previously recorded evidence expires due to head/base/workflow/runner drift. A timed-out check is **unknown**, never success. An unverified rollback target is **unknown**, never a default.

## Minimal handoff record (no secrets)

| Field | Required invariant |
| --- | --- |
| Incident ID and UTC observation time | Identify the failed observation; never reuse an earlier run's timestamp. |
| Approved candidate SHA and observed deployed SHA | Both complete 40-hex commit IDs; mismatch is a stop condition. |
| Exact deployment run URL and attempt | Must identify the actual deployment, not a validation or preview run. |
| Health source and observation | Record independent HTTPS evidence plus the exact UTC observation interval; missing evidence is unknown. |
| Current serialized release owners | Re-read live #580/PWQ-41 and #1089 state before considering any new change. |
| Last-known-good identity | Exact commit SHA, immutable artifact identity and **independent verified provenance**; a label or branch name alone is insufficient. |
| Rollback authorization owner | Named accountable human/operator approval tied to incident, target SHA and time; blank means not approved. |
| Production preflight | Explicit fresh result, runner identity and safe-idle confirmation; do not infer from a prior deployment. |
| Disposition | `hold`, `investigate`, or `escalate-for-explicit-approval` until every live gate authorizes any action. |

## Hard stop / escalation

1. Freeze further promotions and mark the incident unresolved when any identity, authorization or evidence field is missing, contradictory or stale.
2. Preserve the existing deployment logs and nonsecret proof references; redact credentials, tokens, customer payloads and browser session data.
3. Reconcile exact main, proposed rollback target and currently deployed SHA; require a fresh complete blocker inventory. A historical green CI run cannot override an open serialized gate.
4. Escalate for explicit human approval and production-runbook review. A rollback is a **new production mutation** requiring its own admission, safe-idle, serialization, guarded prewrite and post-change proof. Do not automatically dispatch it from this document.
5. Record the observed result and independent external health proof after any separately authorized operation. If proof is unavailable, keep the incident open and escalate; never declare successful recovery from a workflow exit code alone.

## Failure-mode review matrix (manual, no execution)

Use these as negative acceptance cases when reviewing a proposed incident handoff. In each case the only permissible outcome from this guide is **hold and escalate**, not rollback execution.

| Scenario | Evidence that invalidates the handoff | Required disposition |
| --- | --- | --- |
| CI succeeded for an earlier head | Current candidate SHA differs from the head in the successful run | Hold: rerun exact-head checks under current gate owners. |
| Deploy workflow exited successfully but health timed out | No independent external health observation for the deployed SHA | Hold: record health as unknown; never record recovery. |
| Rollback artifact has a plausible tag but no immutable digest | Artifact identity cannot be independently tied to the named last-known-good SHA | Hold: no target selected; require provenance review. |
| Main advanced after a receipt was signed off | Receipt's base SHA is no longer current main | Hold: recheck owner window and fresh blocker inventory. |
| A release owner or runner changed during preflight | Ownership / runner binding differs from recorded evidence | Hold: reject old preflight and request new approval. |
| Two concurrent production transitions are observed | Safe-idle or serialized single-writer exclusivity is unproven | Hold: do not dispatch competing deployment or rollback. |
| A human approved a different incident or target | Approval is not bound to this incident ID and exact rollback target | Hold: explicit new decision required. |
| Browser or dashboard appears healthy but deployed SHA is unknown | No verified exact SHA from production and independent status | Hold: UI appearance is not proof of release identity. |

### Reviewable acceptance conditions

- A reviewer can trace incident ID → deployed SHA → run attempt → observed health evidence, without relying on a branch/tag alias.
- Every proposed last-known-good target has both immutable artifact identity and external provenance.
- A missing value, a stale timestamp or a conflicting owner always yields a non-authorizing disposition.
- An operator can identify the human who must approve **a separate** rollback action; this document contains no command, dispatch hook or mutation switch.
- Independent production health after any separately authorized recovery must be verified before the incident is marked resolved.

## Non-authority statement

```json
{
  "rollback_authorized": false,
  "deploy_authorized": false,
  "merge_authorized": false,
  "metadata_write_authorized": false,
  "mutation_performed": false
}
```

This guide makes **no** API calls, state changes, workflow dispatches, queue changes, deployments, rollbacks or PR-body edits. Any actual release or rollback follows separately owned, live-reviewed production controls.

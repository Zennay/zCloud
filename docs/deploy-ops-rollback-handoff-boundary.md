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

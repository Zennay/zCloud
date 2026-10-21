# zCloud post-gate deploy receipt invariants

Status: **verification-only handoff**. This document does not authorize a merge, metadata rewrite, workflow dispatch, deploy, or runtime mutation.

## Serialized ownership boundary

The active serialized control-plane/deploy window remains owned by #580 / PWQ-41 and successor #1089. Keep read-only deploy-ops candidates (#1100, #1104, #1105, #1106, #1107) separate; none grants a write lease. Worker 2 owns PWQ-258. Never treat a successful audit, planner, batch preflight, or compare-and-swap intent as permission to update PR descriptions or production state.

## Admission receipt to collect after the owner releases the gate

Require a single coherent receipt set, evaluated at the moment of release:

1. **Ownership:** recorded termination or explicit release from the serialized owner, and no competing active writable owner for the same paths or capability. If uncertain: STOP.
2. **Source:** canonical `main` SHA captured fresh; deployment candidate is the very same commit. Reject a stale candidate or a moving branch reference.
3. **Evidence:** exact candidate-head full regression and dedicated permanent VPS `[self-hosted,zcloud,vps]` validation are terminal SUCCESS; record run IDs, conclusions, SHA, and runner identity. “Queued”, “in progress”, and a green run against another head are not success.
4. **Admission:** fresh blocker inventory from #920 plus #866/#924 checks, not just the abbreviated named #580/#1089 pair. Reject any missing, contradictory, or expired evidence.
5. **Production prewrite:** verify the current deployed SHA and runtime health **before** any mutation; confirm destination/rollback provenance and ownership. A changed observation invalidates the receipt.
6. **Deploy:** only the established guarded VPS-first lane may deploy exact approved SHA. This document performs no dispatch.
7. **Post-deploy:** collect exact deployed SHA, workflow run ID, production health/canary, service status, and independent externally observable response; do not mark DONE on workflow success alone.
8. **Failure:** stop forward promotion, preserve the immutable receipt and follow the existing recovery runbook; do not silently retry a partial writer or alter SQLite/queue/browser/service state from this checklist.

## Fail-closed decision record

A future reviewer should produce this small machine-readable shape, *without secrets, raw PR bodies, or tokens*:

```json
{
  "candidate_sha": "<40-char git sha>",
  "canonical_main_sha": "<40-char git sha>",
  "owner_released": false,
  "exact_head_regression": {"run_id": null, "conclusion": null},
  "permanent_vps_proof": {"run_id": null, "conclusion": null, "runner_labels": []},
  "blockers_fresh_and_clear": false,
  "prewrite_state_fresh": false,
  "deploy_authorized": false,
  "deploy_run_id": null,
  "postdeploy_exact_sha_verified": false,
  "postdeploy_health_verified": false
}
```

Default all gates to false. The record cannot set `deploy_authorized=true` unless **all** prior gates are independently evidenced and the serialized owner has explicitly released the window. If a later candidate SHA changes, invalidate every SHA-bound prior receipt.

## Separation of responsibilities

This document is a **downstream operational acceptance checklist** only. It does not alter or supersede #1101 release runbook, #1107 metadata remediation matrix, #1104 planner, #1105 preflight, #1106 CAS intent, #920 blocker inventory, or the #580/#1089 serialized owner window. Reviewers must use the authoritative implementation and runbooks; this page merely names the evidence that must appear in the final deploy receipt.

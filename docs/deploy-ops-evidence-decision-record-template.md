# Deploy-ops evidence decision record template

This is an **operator worksheet**, not a release approval or a live GitHub status snapshot. Create one record per candidate SHA and never copy a previous candidate's `PASS` answers forward.

## Identity and provenance

| Field | Value |
| --- | --- |
| Review timestamp (UTC) | UNKNOWN |
| Reviewer / active deploy-ops owner | UNKNOWN |
| Repository | Zennay/zCloud |
| Canonical branch and freshly observed main SHA | UNKNOWN |
| PR number, branch and exact candidate SHA | UNKNOWN |
| Comparison (ahead/behind, changed-path inventory) | UNKNOWN |
| Serialized gate owners (#580 / #1089) | UNKNOWN |
| Outstanding blockers, PR claims and handoff link | UNKNOWN |

Do not include credentials, raw environment variables, access tokens or confidential payloads in this record.

## Exact-head checks

Each entry must be populated independently from a freshly queried run. **A prior head's successful check is not evidence for a new head.**

| Proof | Run ID / attempt | Actual head SHA | Status / conclusion | Required runner verified? | Decision |
| --- | --- | --- | --- | --- | --- |
| Full repository regression | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT PROVEN |
| Permanent-VPS integration proof | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT PROVEN |
| Deployment gate/planner proof | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT PROVEN |
| Metadata batch preflight proof | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT PROVEN |
| Immutable-intent proof | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT PROVEN |
| CPU/dashboard checks where required by owner | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT PROVEN |

**Validation:** the check set comes from the release owner's **current** policy, not from this example table. Required success means completed, successful, matching exact head, valid workflow revision and correct runner; anything else is unproven.

## Decision

- Evidence consistent for captured head: **UNPROVEN**
- Active gate released by each owner: **UNPROVEN**
- Authorized to rewrite PR metadata: **NO**
- Authorized to merge: **NO**
- Authorized to deploy: **NO**
- Runtime, queue, service or runner action performed: **NO**

### Mandatory invalidate-and-repeat scenarios

- Candidate changed after CI: re-run affected head-bound checks before further acceptance.
- Canonical `main` changed: update comparison, blockers and integration proof.
- Owner or serialized gate changed: reacquire explicit release from the **current** owner.
- Deployment environment or workflow changed: reacquire matching proof.
- Pending/missing/wrong-runner check: leave decision **UNPROVEN**.
- Proof evidence contradicts external health or deployed SHA: reject deployment receipt; notify owner for rollback decision.

## Handoff

Record the exact remaining failing, queued or absent proof by run URL and SHA, plus the next **safe, non-conflicting** action. Recheck the live GitHub state before executing that action.

This worksheet never substitutes for the serialized production gate, release owner's explicit authorization, guarded prewrite, fresh proof or post-deploy receipts. See [exact-head freshness policy](deploy-ops-exact-head-evidence-freshness-policy.md).

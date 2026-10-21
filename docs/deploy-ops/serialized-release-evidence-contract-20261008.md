# Serialized deploy-ops release evidence contract

This document is a non-mutating preflight for zCloud release operators. It does **not** grant deployment authorization, and it must not be used to bypass the active serialized owner.

## Required evidence for a production decision

| Field | Required interpretation |
| --- | --- |
| `repository` | Exactly `Zennay/zCloud`; cross-repository success is not proof. |
| `main_sha` | Full 40-hex commit currently fetched from `refs/heads/main`. |
| `workflow_run_id` | An actual GitHub Actions run, not a pasted URL or manually asserted green status. |
| `run_head_sha` | Must equal `main_sha`; a successful run on an older main is stale. |
| `run_event` | Must match the release gate's expected trigger. |
| `run_conclusion` | Exactly `success`, with terminal `completed` status. |
| `environment` | Explicit target; never infer production from the branch name. |
| `production_receipt` | Produced by the authorized serialized release lane, after successful eligible run. |
| `postdeploy_activation` | Separately verified and recorded; a deployment receipt is not proof of worker activation. |

## Non-negotiable admission rules

1. Check open PRs and recent unmerged branches for exact-file and semantic ownership before changing release paths. A similarly named document or tool can still conflict.
2. Record the single active serialized deployment owner and the head SHA at the start of each admission. If owner identity cannot be established, **do not mutate** production or the control plane.
3. Re-fetch `main` after any wait, review, rerun, or PR merge. Prior successful evidence is invalid once `main_sha` changes.
4. Do not accept artifacts or approvals from forked/untrusted contexts as trusted release proof.
5. A run's `success` does not imply that VPS deployment occurred. Preserve the distinction between CI, production receipt, and worker activation evidence.
6. Fail closed if evidence is missing, truncated, time-inconsistent, superseded, or references a retired gate/PR.
7. Never dispatch duplicate deployments solely because evidence is delayed. First reconcile the existing run ID and the current production receipt.
8. Preserve rollback identity: record both the previous deployed commit and the new target before an authorized deployment. Do not automatically roll back without a separate guarded operator decision.

## Read-only operator sequence

- **Ownership:** identify current writer/deploy PR and verify no other worker owns the files or workflows proposed for change.
- **Repository:** fetch `refs/heads/main`; capture the exact SHA and retrieval time.
- **Workflow:** inspect the run's repository, workflow ID, event, actor, head SHA, status and conclusion against the release policy.
- **Freshness:** fetch `main` again just before admitting any state-changing release command.
- **Production:** require the serialized lane's signed/traceable production receipt tied to the exact SHA; otherwise leave production unchanged.
- **Activation:** check post-deploy service readiness and the separate worker activation receipt, with bounded waits and explicit failure states.
- **Handoff:** preserve links and identities (PR, run ID, exact SHA, receipt), a concise failure explanation, and the next safe read-only check.

## Negative cases that must be rejected

- Green CI from the prior `main` head.
- A green run in another repository or from a fork.
- A currently queued/running/cancelled run presented as successful.
- A workflow run with no trustworthy provenance or a missing expected event.
- A production health response with no deploy receipt or no exact deployed SHA.
- A worker tab open or a worker count displayed without a post-deploy activation receipt.
- A superseded gate treated as the active serialized owner.
- A stale run retrigger requested while an equivalent authorized deployment is still in progress.

This contract intentionally makes **no** GitHub Actions, VPS, scheduler, worker, or production change.

# Deploy-ops review queue capacity gate (2026-10-08)

This checklist is **advisory only**. It does not authorize a release, a merge, workflow dispatch, runtime mutation, or a change of serialized owners.

## Intake contract

For every candidate deploy-ops pull request, capture repository, PR number, exact head SHA, current main SHA, changed paths, declared worker/owner, draft status, and exact-head CI outcomes. If any value is missing, stop: do not infer it from the title or a previously green run.

Before admitting **any** new integration:
1. Re-read current main and enumerate **all** open PRs plus PR-less branches, including pagination. If enumeration is partial, STOP.
2. Identify duplicate intent and changed-path overlap. If unresolved, STOP and request owner consolidation; never close another worker's PR on its behalf.
3. Verify that serialized production gate #580 / #1089 has explicitly released ownership and that metadata remediation #1108 and Worker 2/PWQ-258 are not in the candidate's write set. An unknown owner or gate state is STOP.
4. Verify checks on the **exact proposed head**, not a previous SHA. A pending, cancelled, failed or missing required check is STOP.
5. Re-read main immediately before review/merge; drift invalidates collected comparison evidence. Obtain independent review.
6. Preserve a bounded handoff entry with PR, immutable SHAs, check URLs, reviewer, owner, disposition and timestamp; omit credentials, service secrets, raw logs and customer data.
7. Even when these checks pass, require independent production-specific authorization and verified post-deploy receipt before marking a live deployment complete.

## Capacity rule

When there are many overlapping deploy-ops drafts, prioritize finishing or consolidating existing owner-approved slices over opening more parallel integration work. Draft PR existence, review notes and offline validators are **not** proof of production readiness.

## Negative acceptance fixtures

| Case | Expected result |
| --- | --- |
| Open PR inventory truncated after first page | STOP / inventory incomplete |
| PR-less branch overlaps candidate file | STOP / owner collision |
| CI green only for previous head | STOP / stale evidence |
| Main changed after comparison | STOP / rebase and revalidate |
| #580 or #1089 status unknown or active | STOP / serialized gate owned |
| #1108 path overlap or PWQ-258 worker ambiguity | STOP / ownership unresolved |
| Missing post-deploy receipt | STOP / cannot claim deployment completed |

This document itself changes no service, workflow, scheduler, runner, VPS, queue, or production state.

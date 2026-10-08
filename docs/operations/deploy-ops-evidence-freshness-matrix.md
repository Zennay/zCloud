# Deploy-ops evidence freshness matrix (read-only)

This note is a non-authorizing review aid for the serialized zCloud deployment window. It does not permit merges, deployment, queue mutation, GitHub metadata rewriting or runner operations. The current serialized owners are #580/PWQ-41 and #1089; the retired #576 is not a gate release receipt.

## Evidence acceptance matrix

| Evidence | Required identity | Reject when |
| --- | --- | --- |
| Baseline | exact main commit SHA at admission | base moved, only branch name recorded, or stale snapshot |
| Candidate | PR number, immutable head SHA, changed-file list | head moved, files unknown, or a parallel PR/PR-less branch owns the same path |
| CI | successful checks tied to the candidate's **latest** head SHA | green on previous head, check queued, skipped, neutral, cancelled, or failed |
| Runner | immutable workflow identity and expected permanent VPS runner labels where applicable | job executed on wrong host, logs or provenance incomplete |
| Serialized gate | fresh status of both #580 and #1089 plus inventory of other writers | either owner open, owner status unknown, or the inventory is incomplete |
| Metadata remediation | exact PR number, head SHA, body digest and successor marker | body changed after approval, digest mismatch, retired #576 mistaken for successor |
| Integration | fresh all-open-PR and PR-less branch overlap scan against one common main SHA | missing pages, concurrent file ownership, changed main or absent explicit handoff |
| Production | exact post-integration guarded receipt on exact merged main | pre-integration green run used as deployment receipt |

## Negative review cases (must fail closed)

1. A candidate was accepted at head H1, then amended to H2; H1 checks are green and H2 checks queued. **Reject.**
2. Main advanced after a successful baseline audit. **Re-snapshot and re-run admission.**
3. #580 closed while #1089 is still open. **Gate remains closed.**
4. #576 closed and #1089 is not checked. **Gate remains closed.**
5. Open PR file scan is complete but PR-less branch scan is unavailable. **Reject.**
6. A metadata body digest changes between preview and write intent. **Reject and reauthorize; never reuse old intent.**
7. A permanent-VPS workflow says success without proving the expected runner identity. **Do not treat as a trusted VPS receipt.**
8. An exact-head test passes but production post-deploy evidence is missing. **Do not call the release complete.**

## Required human-readable review record

Capture UTC observation time; exact main SHA; candidate PR/head SHA; the complete status and run IDs for relevant checks; complete owner and PR-less branch scans; both serialized gate states; the explicit action authorised (if any); and a separate guarded production receipt after integration. State `merge_authorized=false` and `deploy_authorized=false` until every corresponding gate is independently proven. Do not include secrets, raw prompts, auth headers or sensitive runner logs.

This matrix is intentionally documentation-only. It cannot replace the executable gates or the owner-controlled deploy runbooks, and does not change #1108's nine-path ownership.

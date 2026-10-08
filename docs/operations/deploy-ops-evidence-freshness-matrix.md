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

## Failed-check classification and safe escalation

Keep distinct the **public endpoint health** and **recovery mechanism health**. A successful external dashboard probe is not proof that a failed listener-recovery step succeeded. Never convert a failed recovery receipt into an accepted deploy or integration receipt based solely on external reachability.

When a check fails:

1. Pin the exact candidate head and workflow run/job/step identifiers; capture only bounded non-secret outcome fields.
2. Classify as `product_regression`, `runner_or_infra`, `recovery_mechanism`, or `unknown`. Treat `unknown` as not accepted.
3. Require the current owner of the affected workflow or listener to investigate. Do not restart services, re-run a potentially mutating workflow, or overwrite a different owner's branch in this documentation lane.
4. Record whether independent external probes succeeded without downgrading the failed check.
5. Revalidate on the same exact head after an owner-controlled correction. If the candidate head changes, discard earlier-head acceptance and rerun relevant checks.

### Example: PR #1124 initial dashboard evidence (2026-10-08 UTC)

- Candidate head: `a2336e10bffeb6993feb7878104691b5d7d2568f`.
- Dashboard workflow run `37709595499`: overall **failure**.
- Job `113092118410` / step `Recover zCloud dashboard listener`: **failure**.
- Job `113092118585` / step `Probe public zCloud dashboard`: **success**.
- Classification: `recovery_mechanism` suspected; exact cause **unknown** pending owner investigation and logs.
- Decision: **not accepted** as dashboard-recovery proof; no listener/service restart or retrigger authorized by this note.

These are observational receipts for the original head, not permanent claims about the latest CI or live service state.

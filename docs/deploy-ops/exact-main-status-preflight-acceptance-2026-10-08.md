# Deploy preflight: exact-main production-status acceptance matrix

Status: review-only specification (2026-10-08). This document grants **no** deploy, merge, workflow-dispatch, service or queue authority.

## Why this is separate

The current `.github/workflows/zcloud-vps-deploy.yml` preflight requests the combined commit status at `/repos/${repo}/commits/main/status`. The response includes a `sha`; simply printing that SHA does not prove that the returned contexts belong to the exact main commit accepted by the deploy gate. PR #1089 currently owns this workflow. **Do not edit or restack its workflow while #580/PWQ-41 and #1089 remain open.**

## Proposed read-only verification contract

1. Resolve main once to an immutable 40-hex commit SHA before consulting any status result; preserve this as `expected_main_sha` in the preflight output.
2. Fetch statuses by immutable SHA rather than moving `main`. Reject HTTP, parse, missing-field or permissions errors; do not interpret them as success.
3. Require `payload.sha == expected_main_sha`; reject absent, malformed or mismatching SHAs even if `state == success`.
4. Filter status contexts to the exact `zcloud/vps-production` name. Reject missing, pending, failed and ambiguous status evidence; never infer production green from unrelated contexts.
5. Before enabling any production-affecting step, re-read the main ref; if it no longer equals `expected_main_sha`, fail closed and require a fresh preflight.
6. Any terminal success claim must name the immutable SHA and the exact status evidence used. A green status for a prior main commit is not admission for a new head.
7. Preserve every existing independent production admission and serialization gate; a valid exact-SHA status is necessary, not sufficient, for deploying.

## Test matrix for the eventual workflow owner

| Case | Expected outcome |
| --- | --- |
| SHA matched, exact production context successful, main unchanged, other gates green | Eligible to continue under existing gates |
| Combined status success but response SHA mismatched | Stop: stale/wrong commit |
| Production context green on prior SHA while main advanced | Stop: ref changed |
| Missing SHA or malformed SHA | Stop: incomplete proof |
| HTTP failure, empty JSON, invalid JSON | Stop: indeterminate |
| Only unrelated context successful | Stop: missing production evidence |
| Production context pending or failed | Stop |
| Multiple contradictory production contexts | Stop until unambiguous latest evidence is demonstrated |
| Exact status good but serialized #580/#1089 gate still active | Stop: ownership/deploy gate remains closed |

## Ownership and acceptance

This is a standalone review artifact; it neither modifies the workflow nor claims tests were run. The workflow owner must compare it against actual GitHub combined-status semantics, add focused regression tests, check current open PRs and **all pages** of PR-less branches, and prove the implementation against the latest exact head. Do not promote based on stale CI, an incomplete branch inventory, or documentation alone.

Related coordination: #580 (PWQ-41), #1089 (serialized deploy workflow), and the deploy-preflight exact-main-SHA tracking issue.

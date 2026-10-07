# Deploy-ops serialized-gate metadata remediation: acceptance matrix

This is a **review-only** dependency and evidence ledger, not an instruction to deploy, merge, update PR metadata, or manipulate a production queue. It deliberately does not replace the existing deploy release runbook or any active owner implementation.

## Identity and ownership

- Live serialized production owners: [#580](https://github.com/Zennay/zCloud/pull/580) / PWQ-41 and successor [#1089](https://github.com/Zennay/zCloud/pull/1089).
- Retired #576 is historical context, **not** a release gate.
- #1100 owns the read-only drift inventory. #1104 supersedes #1102 as planner owner. #1105 owns time-of-action batch preflight. #1106 owns immutable body-hash remediation intent.
- PWQ-258 / Worker 2 is independent and must not be claimed or modified by this lane.
- Search live PRs, their exact heads, changed paths, the current Notion handoff, and any PR-less branches before choosing further changes. Never infer current eligibility from this historical matrix.

## Required acceptance order (all fail closed)

| Gate | Input identity | Required evidence | If missing, inconsistent or stale |
| --- | --- | --- | --- |
| 1. Live ownership | Open PRs, latest Notion/queue assignment, live gate PR identities | No conflicting writer/deploy or file/path owner | Stop; no writes or deploy |
| 2. Current main | Canonical main commit SHA read immediately before an operation | Exact main SHA recorded, commit comparison repeated after intervening merges | Discard candidate proof; rebase/restack and revalidate |
| 3. Audit (#1100) | Fresh bounded open-PR snapshot plus #576-to-#1089 succession evidence | Read-only artifact; no title/body leaks; successor provenance not misclassified | Discard audit |
| 4. Plan (#1104) | Exact audit artifact and current open successor gate | Deterministic unique PR numbers, bounded batches; explicit non-authorizing output | Discard plan |
| 5. Batch preflight (#1105) | Plan SHA, current main, fresh open PR state | Every target still open, unchanged, eligible, not successor-aware; no duplicates | Discard batch |
| 6. Body-hash intent (#1106) | Fresh preflight and exact observed target head/body | Expected target head and SHA-256 of the exact observed body; raw body excluded from artifact | Discard intent |
| 7. Future metadata writer (not authorized here) | Newly fetched PR body and target head immediately before each prospective update | Explicit **separate** human/queue authorization; compare-and-swap body hash and head match; narrow allowed target set | Do **not** write; restart from fresh audit/preflight |
| 8. Production release | Independent current deployment runbook plus live serialized-gate release proof | Exact head, trusted permanent-VPS runner, regression, rollback, authorization and safe-idle gates all green | No merge, dispatch, service action or deploy |

The existence of a green upstream artifact **never** implies authorization for a downstream mutation.

## Expected invariant flags

Every report produced by the current audit / plan / preflight / intent chain must remain read-only and report `mutation_performed=false`. For planner, preflight and intent, require `metadata_write_authorized=false`, `merge_authorized=false` and `deploy_authorized=false`. In addition, preflight requires `requires_fresh_revalidation=true` and intent requires `compare_and_swap_required=true` and `requires_fresh_body_hash_match=true`. Any unexpected true, missing or malformed flag fails closed.

## Proof to collect before declaring an owner PR accepted

1. Immutable candidate commit SHA and current canonical main SHA.
2. Exact ahead/behind comparison and full changed-file path list, proving this owner's scope does not overlap another active worker.
3. Dedicated permanent `[self-hosted,zcloud,vps]` exact-head proof with recorded run ID and terminal conclusion.
4. Full zCloud regression run ID and terminal conclusion for **the same head**.
5. Redacted report content and explicit negative authorization flags.
6. Fresh check that both the upstream/downstream gate identities and writer ownership remain correct.
7. Explicit record of whether a mutation occurred; a successful read-only proof is **not** a production receipt.

If any proof refers to an older candidate SHA, mark it historical and re-run acceptance rather than relabeling it green. Never silently close a predecessor until the replacement has passed these gates. Do not edit active Worker 2 / PWQ-258 queue state.

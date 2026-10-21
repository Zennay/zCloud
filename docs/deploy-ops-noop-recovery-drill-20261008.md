# Deploy-ops: evidence-only no-op recovery drill

Status: **review-only; no operational authorization**. This worksheet tests whether an operator can *identify* a safe recovery decision without issuing any recovery action. It does not exercise live deploy, rollback, queue, runner or production services.

## Preconditions

- Record UTC observation time, repository, immutable candidate and current `main` 40-character SHAs, PR head SHA and workflow run URLs. Re-read current main immediately before and after evidence collection.
- Obtain current **complete** ownership inventory: open PRs, PR-less branches, current worker/queue claims, and serialized deploy owners **#580 / PWQ-41** and **#1089**. An absent or stale inventory is an explicit **stop**.
- Keep Worker 2 / PWQ-258 and other active owners untouched. Treat all cross-worker ownership as unavailable until independently verified released.
- Use read-only published metadata. No secrets, raw tokens, credentials, service environment values or private log bodies in this worksheet.

## Three tabletop scenarios (no real mutations)

| Injected observation | Required decision | Evidence to retain |
| --- | --- | --- |
| CI is green for an old candidate head, while current PR head moved | Reject stale-green evidence; request exact-current-head tests | Both SHAs and immutable run URLs |
| GitHub Actions deployment succeeded, but independent external post-deploy health is absent | Do not assert deployed-and-healthy, restart workers or attempt rollback | Deploy run identity; missing-health annotation |
| An operator claims the serialized gate is clear, but either #580 or #1089 remains active or unverified | Stop all live deployment/recovery commands; refer to named serialized owner | Time-bounded owner snapshot and source URLs |

## Observer run sheet

1. Write the expected candidate SHA and separately collected current main SHA; mark `same_sha: true/false/unknown`.
2. List required exact-head tests and terminal statuses, including regression, CPU and dashboard-recovery evidence, with runner provenance. A queued/in-progress/cancelled/stale test is **not** a green acceptance.
3. Record any deployment receipt's immutable source, environment, body digest and externally observed health. A self-reported or hand-edited receipt cannot establish production truth.
4. Capture owner/branch inventory at the beginning **and** end. If it changed, invalidate previous admission evidence.
5. For each scenario, write `expected: STOP`; record `actual: STOP/UNVERIFIED` and the missing evidence. Do not convert a successful tabletop outcome into deploy permission.
6. Produce only a redacted review note; follow the existing incident evidence and privacy boundary before posting externally.

## Acceptance of this *drill document* (not the deploy)

- All three scenarios resolve to STOP and no unsafe operational action is suggested.
- The reviewer confirms overlap disjointness and latest-main status independently.
- The document is reviewed on its **exact PR head**, with required repository checks completed. Previous-head success is not valid.
- The reviewer records no production, runner, workflow dispatch, queue, browser, SQLite, PR merge or rollback mutations performed.

## Fail-closed output

```json
{
  "drill_complete": false,
  "release_authorized": false,
  "merge_authorized": false,
  "deploy_authorized": false,
  "rollback_authorized": false,
  "mutation_performed": false
}
```

Only `drill_complete` may become true after documentary review. Every authorization flag stays false. An actual deployment or rollback always requires a separate live ownership, exact-head, signed receipt, independent health and explicitly authorized gate review.

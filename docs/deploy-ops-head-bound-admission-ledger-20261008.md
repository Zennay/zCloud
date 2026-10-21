# Deploy-ops: head-bound acceptance ledger (non-authorizing)

This is an operator checklist, not a deploy permit. **Phase: Accelerate → Verifying.** Production/control-plane writes remain serialized by #580/PWQ-41 and successor #1089. Do not use legacy #576 as the active gate. PR #1108 owns the metadata-remediation planner/preflight/intent implementation; this document does not alter those paths.

## Evidence tuple required for each candidate

Record these fields together at the time of the final admission decision:

| Field | Required evidence |
| --- | --- |
| Candidate | PR number, current state/draft flag, owner, changed-path set |
| Base | exact live `main` SHA read immediately before decision |
| Head | exact PR head SHA **after** any restack |
| Compare | ahead/behind counts against the same live main SHA; stale behind count rejects admission |
| Checks | required check name, run ID, conclusion and run's **head SHA** for every proof |
| Gate | fresh state of #580 and #1089, plus inventory of any other serialized writer |
| Ownership | open-PR changed-path overlaps and PR-less branch overlaps checked at the same checkpoint |
| Authority | explicitly separate review-readiness, merge admission and live deploy approval |

A green check whose head SHA differs from the final PR head is historical evidence, **never** final-head acceptance. A valid head proof can become stale when main advances; re-read main and re-evaluate compare and conflicts. Missing, pending, cancelled or skipped required checks are not success.

## Stop / retry conditions

1. Abort a mutation if either the recorded main SHA or candidate head has drifted; collect a new tuple. Do not update someone else's claimed branch.
2. Keep #1108 draft until its **final** head has all required proofs and a new exact-main comparison. Its earlier b18a16e8... checks cannot validate later b76539d... code.
3. Keep all dependent PRs unmerged while either #580 or #1089 controls the serialized production window, or another writer remains active.
4. Never infer deploy authorization from green regression, CPU or dashboard checks. A release requires separate guarded production evidence, allowed actor, exact checkout identity and post-deploy receipt.
5. If runner capacity is constrained, do independent documentation or read-only evidence collection rather than cancelling unrelated jobs or writing to VPS services, browsers, SQLite, queue or runner controls.

## Minimal handoff receipt

```text
timestamp_utc:
candidate_pr:
owner:
main_sha:
head_sha:
ahead:
behind:
changed_paths:
required_checks:  # name | run_id | checked_head_sha | conclusion
serialized_owners:  # #580, #1089, and any other active writer
open_pr_overlap:
prless_branch_overlap:
review_ready: false
merge_authorized: false
deploy_authorized: false
mutation_performed: false
decision: deferred
reason:
```

Never set authorization flags to true merely because this template has been filled out. Only the owning live-gate procedure can grant them. No PR-body sweep, merge, deployment or live service mutation is authorized by this runbook.

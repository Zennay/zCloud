# Deploy-ops: negative evidence and fail-closed handoff

This document is an **operator review aid**, not a deploy gate, execution recipe, or authorization. It is intentionally independent of the serialized production writers in #580/PWQ-41 and #1089 and the metadata-remediation owner #1108.

## What *does not* authorize production

| Observed evidence | Permitted inference | Forbidden inference |
| --- | --- | --- |
| An old PR head has six successful checks | That exact old head passed those checks | A restacked head or newer main is tested |
| An offline receipt parses and its SHA fields match | Submitted receipt fields are internally consistent | The remote run happened, belongs to this repo, or deployed |
| Two offline receipts compare without drift | Compared fields were equal | A production deployment is healthy now |
| A draft PR has no conflicts | The merge preview is currently compatible | The PR is approved to merge |
| A deployment job is queued, skipped, or cancelled | No positive terminal deploy proof was observed | Production is at the candidate SHA |
| An external URL has HTTPS syntax | It looks like an HTTPS reference | The linked resource exists or is trustworthy |
| Notion queue item says Done | Documentation reports completion | Canonical GitHub runner checks or live receipts are green |

## Binding a post-gate decision

Before any human or separately authorized release controller treats a deploy as eligible, reacquire *fresh* evidence: current `main` SHA; exact candidate/head SHA; current PR ownership and blockers; regression results for that exact candidate; trusted permanent-VPS runner identity; same-repository workflow provenance; serialized gate release; and prewrite production state. Changes to any bound identity invalidate the decision and require re-evaluation.

After deployment, independently confirm the exact deployed SHA, job conclusion, external health, and rollback disposition. Record uncertainty as **unknown**, never as **success**. No receipt or audit described here implies `merge_authorized`, `deploy_authorized`, or `release_authorized`.

## Handoff when evidence is missing or contradictory

1. Preserve sanitized identifiers: PR number, immutable commit SHA, workflow run ID, relevant check conclusion, and observation timestamp. Do not paste credentials, cookies, tokens, or raw runtime logs.
2. Report the smallest missing fact explicitly, such as “exact-final-head regression absent” or “runner provenance unverified.” Distinguish **failure**, **pending**, and **not checked**.
3. Leave existing production and the live release owner untouched. Do not cancel a healthy deploy, restart Firefox, write SQLite/queue state, trigger recovery, or update a PR body to simulate acceptance.
4. Route evidence to the active serialized owner for review; reopen admission only after obtaining fresh head-bound proofs and ownership clearance.
5. If production health is uncertain, use the separately owned incident/rollback procedure; a documentation-only reviewer must not initiate rollback.

## Deterministic reviewer examples (non-authorizing)

| Scenario | Classification | Required next evidence |
| --- | --- | --- |
| Regression succeeded at SHA A; candidate was restacked to SHA B | **STALE** | Re-run regression and all required candidate-bound checks at B |
| Workflow completed successfully but runner labels/identity were not verified | **UNVERIFIED** | Obtain trustworthy runner provenance for the exact run |
| Candidate and deployed SHA differ, even when HTTP health returns 200 | **MISMATCH** | Establish deployed identity and escalate to the serialized deploy owner |
| Receipt is internally valid but has no independent post-deploy observation | **INCOMPLETE** | Fresh deployed-SHA and external health receipts |
| Main changes after release admission but before the guarded prewrite | **INVALIDATED** | Start admission again against current main; do not reuse approval |
| A required check is pending while another unrelated check is green | **PENDING** | Exact-head terminal result of the required check |

The classification names are descriptive operator vocabulary, **not** GitHub check conclusions or API fields. Missing information does not mean a failure occurred; it means authorization has not been established. Treat any contradictory positive/negative evidence as **UNVERIFIED** until independently reconciled, and never silently choose the favorable observation.

## Parallel-owner discipline

This slice modifies no workflow, script, CI, queue, deployment, metadata-remediation PR, or production resource. #1108 owns planner/preflight/immutable-intent changes. #1110 owns canonical deploy-trigger static tests. #1111/#1115 own offline receipt parsing/comparison. #580/PWQ-41 and #1089 hold the serialized live control-plane/deploy window. An unrelated green read-only check does not remove these ownership constraints.

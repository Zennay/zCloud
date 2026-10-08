# Exact-head deployment evidence freshness policy

Status: **review-only / non-authorizing**. Scope: deploy-ops evidence lifecycle, not deployment execution.

## Problem

A success receipt is evidence for **one exact candidate SHA and one exact test context**, not a transferable approval. Evidence can become stale after a restack, changed deployment environment, workflow update, renewed blocker, or release-owner turnover. A previous green run must never silently re-authorize a new head.

## Evidence identity

Keep an immutable record of:
- repository and canonical base branch; observed base SHA;
- candidate/head SHA and workflow file revision;
- workflow name, run ID, attempt, conclusion, and completion timestamp;
- runner identity/required labels where applicable;
- the specific assertion proved (unit regression, batch preflight, immutable intent, VPS readiness, health proof);
- applicable gate owner and blocker inventory at decision time.

Treat missing or ambiguous fields as **unknown**, not success. Compare all SHA fields literally against a fresh live GitHub snapshot, not an earlier copied PR description.

## Invalidation triggers

Invalidate acceptance and repeat the affected proof when any of the following happens:

1. Candidate/head SHA changes, including an apparently harmless restack.
2. Canonical base SHA changes before release authorization; recompute comparison and rerun integration-sensitive checks.
3. Run attempt or conclusion is absent, still pending, cancelled, skipped, neutral, timed out, or failed.
4. Required proof was produced by the wrong runner class, labels, repository, branch, or workflow revision.
5. Any dependency, blocker, ownership claim, or serialized release gate changes.
6. Deployment configuration, target environment, secret bindings, prewrite guard, or health criteria changes.
7. The exact-head decision snapshot can no longer be reconstructed.

Elapsed time alone is **not** an authorization signal. Evidence older than a configured policy window must be rechecked against the live state, but evidence younger than the window is not automatically valid. Freshness is a conjunction of identity, state, scope, owner and acceptance checks; a TTL is only an additional upper bound.

## Safe acceptance sequence

1. Capture fresh main SHA, candidate SHA, open PR and active-owner inventory.
2. Confirm all owned required checks completed successfully for that candidate SHA and appropriate runner.
3. Compare base/head and verify changed paths remain within the approved, non-conflicting scope.
4. Verify every existing serialized release owner has explicitly released its gate; otherwise stop.
5. Immediately before an authorized write or deploy, repeat the live SHA, gate and blocker checks and use compare-and-swap / expected SHA where supported.
6. Emit a new immutable receipt after the real deployment and independently check the deployed SHA and health evidence.

A read-only validator or preview can establish consistency; it **never** grants merge, metadata rewrite, production-deploy, runner mutation, or queue mutation authority. Treat no proof or conflicting proof as fail-closed.

## Incident and handoff examples

| Observation | Decision | Next action |
| --- | --- | --- |
| Old head's six workflows green, final restack created new head | STALE | Require final-head checks |
| Main moved by unrelated worker | RECHECK | Fresh comparison and integration-sensitive validation |
| Workflow queued behind VPS capacity | PENDING | Preserve queue; work on disjoint scope |
| One check successful but wrong runner labels | INVALID | Repeat on required runner |
| All static checks green, serialized gate still owned | NOT AUTHORIZED | Wait for explicit owner release; no deploy |
| Post-deploy receipt references a different deployed SHA | REJECT | Stop rollout and follow owner-controlled rollback path |

## Non-goals

This document does not define or initiate automated deployment, impose a global expiry duration, cancel a runner job, rewrite PR bodies, mark another PR ready, merge code, or replace the canonical release/runbook and receipt invariants. It supplements them with an explicit **evidence invalidation** checklist.

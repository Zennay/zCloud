# Deploy-ops PR saturation admission gate (2026-10-08)

## Purpose
When several deploy-ops workers are active concurrently, the number of draft proposals is **not** evidence of deploy readiness. This is an evidence-only coordination aid. It cannot release a serialized production owner, merge a PR, restart a runner, or deploy.

## Read-only admission procedure
1. Read current `main` SHA from GitHub and record UTC observation time.
2. Search **all open PRs** (not only title matches) and **all non-merged branches**, including PR-less branches; gather head SHA, changed paths, draft status, workstream owner and update time. Paginate to completion. Missing/partial results = STOP.
3. Assign each proposed change one exclusive ownership lane and enumerate overlapping paths against every live owner; do not infer non-overlap from a branch name or PR description. Any uncertain or overlapping path = STOP.
4. Inventory existing serialized #580/PWQ-41 and #1089 release ownership and #1108 metadata owner plus Worker 2/PWQ-258. A draft/retired/rebased PR does not demonstrate owner release.
5. For every contemplated integration candidate, verify fresh `main`, exact candidate head, trusted workflow source/event, terminal successful regression and permanent VPS proof tied to that same head. Historical green on another SHA = STOP.
6. Treat post-deploy activation as a separate gate: a trustworthy deployment receipt **and** observed worker activation on intended production VPS are required before stating production success.
7. If any evidence is absent or stale, keep the candidate draft. Select only a clearly disjoint, offline, non-authorizing improvement; otherwise record the absence of an unowned mutation surface rather than creating an overlapping PR.

## Saturation indicators (not automatic decisions)
- Many concurrent open deploy-ops PRs targeting stale `main`.
- Two or more active claims to a single file, workflow, runtime lock, or production gate.
- Unreviewed advisory tools that each reimplement similar checks.
- Duplicate offline validators producing contradictory admission messages.
- A production mutation proposed without an explicit living owner handoff.

When these occur, prefer review/consolidation of existing branches over generating another near-duplicate artifact. A clear owner must authorize any scope transfer.

## Evidence record
Record: observation time UTC; canonical repository; observed main SHA; complete paginated PR/branch inventory and retrieval provenance; claimed owner per path; ambiguity/overlap outcomes; exact-head checks and runner identity; serialized gate status; approval identity; production receipt and activation evidence (if applicable); reassessment timestamp.

Allowed conclusion values: `STOP_MISSING_EVIDENCE`, `STOP_OVERLAP`, `STOP_SERIALIZED_OWNER`, `REVIEW_ONLY_DISJOINT`. None grants deploy/merge permissions.

## Negative acceptance cases
- Open PRs fetched but PR-less branches not inspected -> STOP.
- A PR has no changed-path inventory -> STOP.
- `main` advances after evidence collection -> STOP and recollect.
- CI successful on an earlier commit -> STOP.
- #580 marked inactive but #1089 owner remains active or unknown -> STOP.
- A draft receipt claims deployment without production activation proof -> STOP.
- A worker claims a path already owned by #1108 or PWQ-258 -> STOP.
- This runbook itself treated as release approval -> STOP.

## Scope and limitations
This path contains documentation only; no executable code, workflow, queue, runner, browser, service, production or PR metadata mutation. All deployment and merge authorization remain false. Any later operational action requires independently verified fresh evidence and the existing serialized release process.

## Reviewer evidence worksheet (copy per candidate, never reuse between SHAs)

| Field | Recorded value | Failure condition |
| --- | --- | --- |
| Observation UTC | _unset_ | Missing timestamp |
| Canonical `main` SHA (first and last read) | _unset_ | Different SHA or missing reread |
| Candidate PR / branch / head SHA | _unset_ | Head changed after test |
| Complete open-PR pagination cursor/end | _unset_ | Incomplete inventory |
| Complete branch pagination cursor/end | _unset_ | PR-less ownership unknown |
| Proposed changed paths and current path owners | _unset_ | Any path unowned-by-proof or conflicted |
| #580, #1089, #1108 and PWQ-258 independent owner status | _unset_ | No positive owner release |
| Regression / permanent-VPS run ID, head and trusted event | _unset_ | Different head, untrusted event or nonterminal result |
| Authorized production receipt and independent activation observation | _unset_ | Missing or conflicting evidence |

Treat every `_unset_` value as **STOP_MISSING_EVIDENCE**. Do not fill a field from another PR's body without re-verifying its source and timestamp. A terminal CI result from an older head cannot be reinterpreted as a fresh result.

### Explicit release handoff invariants
1. Prior owner identifies the exact path set and a **positive handoff**; inactivity, stale timestamps, draft status, or an empty queue are insufficient.
2. Receiving worker confirms latest `main`, latest candidate head and file-disjoint ownership immediately before any write.
3. If another worker writes during the check, revoke the snapshot and repeat the ownership inventory.
4. Human/serialized release approval is separate from code review and from this worksheet; no worksheet state elevates authority.
5. After evidence collection, retain immutable run links/SHAs and avoid storing credentials or private runtime material in the PR description.

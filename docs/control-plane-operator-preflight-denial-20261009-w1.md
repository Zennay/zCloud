# Control-plane operator preflight: mandatory denial record

Status: **offline operational acceptance contract; not deployed or wired to runtime**.
Owner: isolated documentation only. Serialized writer/release integration remains with #580/PWQ-41 and #1089.

## Purpose

A human or automation operator must not infer permission to restart, merge, deploy, dispatch, release claims, change the queue, or alter resources merely because a dashboard, PR check, Notion claim, or GitHub Actions snapshot looks healthy. Capture the **exact proposed mutation** and its independently verified authorization before acting.

## Minimal decision record

For each *specific* attempted mutation, record:

| Field | Required evidence |
| --- | --- |
| Proposed operation | Exact action, target resource, repository/environment, and intended effect. |
| Current owner | Named active worker, PR/branch, serialized window, and timestamp of verification. |
| Canonical state | Fresh source-of-truth sample, immutable identity (commit SHA / run ID + attempt / SQLite key as applicable), time and provenance. |
| Positive authority | Explicit applicable policy, authenticated actor capability, required approval and valid scope. |
| Safety guards | All dependent gates passing **at decision time**, including resource/queue cap and tenant/project isolation. |
| Rollback | Defined revert action, known-good reference, and observation proving the revert can be performed. |
| Outcome | Allow or deny; proof URL/receipt, measured timestamp, and whether a mutation actually occurred. |

Absence of any required field means **DENY**. A previous successful workflow or a documentation entry is never a substitute for fresh authority. Record source disagreement as **UNKNOWN → DENY**, rather than choosing the most optimistic source.

## Mandatory denial cases

1. Another worker or PR owns the same file, capability, active lease, or serialized deployment window.
2. Main SHA or proposed PR head moved after preflight. A check for an older SHA is not acceptance for a new SHA.
3. Status is queued, in progress, skipped, cancelled, stale, partial, malformed, or observed only indirectly.
4. GitHub-hosted CI passed but mandatory permanent-VPS proof has not completed for the exact head.
5. Notion shows Running/Done but neither VPS worker-generation heartbeat nor canonical SQLite queue has been freshly corroborated.
6. GitHub Actions queue shrinks without individual successful run conclusions.
7. Healthy runtime receives a speculative restart request merely because a separate recovery job reported a transient 503.
8. Mutation crosses projects, tenants, permission scopes or approved resource ceilings.
9. A claimed break-glass exception lacks a recorded accountable approver, expiry and rollback target.
10. Source-of-truth connectors fail or return ambiguous observations; lack of visibility never implies lack of an owner.

## Safe disposition

- **DENY:** perform no production mutation; preserve the current owner and capture missing evidence.
- **UNKNOWN:** treat as DENY; re-sample only by an already trusted, read-only route.
- **ALLOW (eligible for separate execution):** all evidence fields are fresh and verified, but this document does *not* grant privileges and does not execute anything.

## Acceptance exercises (tabletop only)

| Scenario | Expected disposition |
| --- | --- |
| PR validation succeeds, but canonical main moved | DENY; rebase/revalidate exact final head. |
| Queue count drops, but no per-run conclusions | UNKNOWN → DENY. |
| Notion ownership claim is old, branch still exists | UNKNOWN → DENY; reconcile both before claim. |
| Dashboard says green, raw production /api/status returns 503 | UNKNOWN → DENY; check runtime provenance before recovery. |
| All checks green, but rollback reference is unavailable | DENY. |
| Same operation has evidence for a different project | DENY. |
| Full fresh evidence and explicit authorized release owner | Eligibility can be recorded; separate execution gate still required. |

## Handoff and boundaries

This is a documentation-only decision interface. It does not authenticate any observations, implement a policy engine, permit bypassing #580/#1089, or change queues, SQLite, workflows, services, browser workers, resource allocation or deployment. A future owner may turn these examples into tests **only after** checking current overlap with existing provenance/permission reference PRs. Record any proposed integration as a new scoped assignment.

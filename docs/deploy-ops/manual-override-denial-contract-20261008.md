# Deploy-ops manual-override denial contract (2026-10-08)

Status: **read-only acceptance specification; not a release approval**. This document owns no production workflow, SQLite state, dashboard recovery code, or release credentials.

## Problem

An operator-visible "force", "retry", "approve", or "override" control must never be interpreted as proof that the serialized deployment window is free. Human intent is not a substitute for machine-verifiable authorization. In particular, a green check from a superseded branch or an external dashboard health check does not prove that a privileged recovery is safe.

## Non-bypassable admission requirements

For **every** release, repair, restart, queued-run cancellation, or other VPS mutation:

1. Resolve the canonical `Zennay/zCloud` repository, trusted event and actor, and current `refs/heads/main` SHA **at admission time**. Do not trust caller-supplied repository, ref, branch, SHA or PR metadata.
2. Verify the actual candidate commit equals the authorized current-main revision; if main moves after CI validation, invalidate evidence and re-run admission from the new SHA.
3. Require a **complete, fresh, same-SHA** snapshot for both serialized owners: **#580/PWQ-41 and successor #1089**. Retired #576 cannot release the successor gate.
4. Require an explicit terminal/released result for *both* gates, no other active writer, and a complete open-PR plus PR-less branch ownership/overlap check. Unknown owner, missing page, timeout, stale lease or inconclusive status means **deny**.
5. Distinguish externally healthy, internally unhealthy and unproven dashboard states. A healthy dashboard must not be stopped to satisfy a CI run. PR-triggered verification must be read-only and must never reach a privileged recover step.
6. Apply the immutable runner identity and bounded operation allowlist; no generic self-hosted runner substitution. Enforce least privilege, a finite execution budget and an idempotency key scoped to operation + exact-main SHA.
7. Bind a fresh pre-mutation receipt to the approval decision, candidate SHA, gate snapshots and operation. Record a bounded post-operation outcome and explicit rollback result without secrets or unrestricted logs.

**Manual override is not an exception to items 1–7.** A user clicking a button, adding a label, re-running a check or dispatching a workflow can request a *new evaluation*, not force a green verdict.

## Deterministic negative acceptance cases

| Case | Expected result | Side effects |
| --- | --- | --- |
| `pull_request` event asks for recovery | deny mutation; read-only diagnostics may run | zero sudo, service, DB, runner writes |
| Human selects "force" while #580 is active | deny | zero mutation |
| #580 cleared but #1089 still active or unknown | deny | zero mutation |
| Evidence refers to #576 rather than successor #1089 | deny | zero mutation |
| CI validated commit A but current main is B | deny and require fresh A/B reconciliation | zero mutation |
| Incomplete paginated PR/branch overlap inventory | deny | zero mutation |
| Healthy public dashboard and recovery requested | deny repair; report healthy/no-op | zero mutation |
| Actor, runner identity, privileges or trust source missing | deny | zero mutation |
| Retry repeats an already admitted operation identity | deduplicate or return prior bounded receipt | zero duplicate mutation |
| Receipt is expired, has no SHA or mismatched operation | deny | zero mutation |
| Every admission input fresh and verified, unhealthy service | *eligible for separately owned serialized repair evaluation*; this document does not authorize execution | none from this contract alone |

## Ownership and release handoff

- Serialized production/control-plane gate: **#580/PWQ-41 + #1089**.
- Owner of read-only nine-path metadata/planner stack: **PR #1108**; do not edit or restack it.
- Dashboard privileged PR-recovery issue: **#1135**; coordinate with existing recovery branch ownership before touching workflow paths.
- Integration coordinator: **#1009**. Its post-gate batches only begin after the exact-current-main ownership and release evidence passes.
- This add-only document is intended for review and adoption into an owner-controlled implementation **after** an explicit free serialized window. It does not change CI, services, approvals, or gates.

## Reviewer checklist

- [ ] No production code, workflow or credential modifications in this proposal.
- [ ] Two live successor gates, not retired #576, are required.
- [ ] Human override affects scheduling only, never the security verdict.
- [ ] Every negative case guarantees **zero privileged side effects**.
- [ ] Existing owners have first right to implement their paths.

# Autonomous blocker recovery contract

This document specifies the intended scheduler behavior; it does not claim the runtime already implements it.

## Recovery lifecycle

1. Detect stalled work by CI terminal failure, unchanged failure fingerprint on repeated attempts, or expired worker heartbeat/lease. Distinguish ordinary queued CI from a failure.
2. Deduplicate incidents by project, root task, failing workflow and normalized failure fingerprint. Assign one owner and a recovery deadline. Do not steal an active lease.
3. Classify safe automatic action: retry transient failures with exponential backoff and a bounded budget; for deterministic code/configuration failures route a single root-fix task to an authorized owner. Never weaken CI or security checks.
4. Park only the dependent task while routing unrelated ready tasks to workers with healthy capacity. Avoid creating child PRs against blocked parent branches.
5. Reconcile the original task on terminal CI state: green means review/merge eligibility under existing gates, red means root-fix, not blind retries. Record resulting-main verification after merge.
6. Escalate to a human only for required permission, an irreversible/high-risk decision, or exhaustion of safe recovery routes. Never auto-grant sudo or bypass required review.

## Minimal incident payload

- **Problem:** project and one-sentence cause, with evidence link.
- **Action:** current automatic recovery step, owner and deadline.
- **Status:** recovering / waiting / needs your input / resolved.

The dashboard and audit show the same current incident state, highest impact first; Wear OS alerts only for actionable human escalations. Historic resolved incidents must not appear as active blockers. Suppress duplicate alerts and report recovery outcomes.

## Capacity guardrails

Throttle admission of heavy jobs on sustained memory pressure, swap saturation, or runner unavailability; do not terminate running jobs solely on a single load sample. Resume conservatively when resources recover. Preserve self-hosted runner boundaries and least privilege.

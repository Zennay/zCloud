# Parked dependency work in the portfolio queue

The portfolio queue distinguishes **blocked** work from **dropped** work.

- `DONE`: the assigned queue item is completed.
- `CONTINUE`: the assigned queue item returns to ordinary queue selection.
- `BLOCKED`: requires concrete dependency evidence (at least 12 characters). The exact original queue item is preserved as `status=blocked`, `eligible=0`; its worker slot and lease are cleared. The scheduler can then allocate unrelated executable work.
- `dropped`: an intentional terminal retirement. It is not the representation for a task waiting on a dependency.

A blocked result still creates an independent continuation under the existing queue lifecycle. The original task remains visible through `GET /api/portfolio-queue`, and the health response includes a separate `blocked` count. A read failure is not a zero count.

## Resume one parked task

Only a trusted admin client may send this action to `POST /api/portfolio-queue`:

```json
{
  "action": "resume_blocked",
  "queue_id": "the-exact-parked-queue-id",
  "resolution_note": "The dependency was resolved; the authorized owner checked the current source and confirmed the resolution."
}
```

The server requires an existing `blocked` row with `eligible=0`, a live non-human-gated project and a concrete operator note. It preserves the previous blocker and bounded evidence in the resume receipt, records the authenticated actor's attestation both on the queue item and in the project's state-receipt history, then returns the task to `status=queued`, `eligible=1`. The response explicitly says `operator_attestation_not_independently_verified`: this is an operator assertion, not automatic proof that the external dependency cleared.

Resume does **not** force a worker, reserve a slot, steal an active claim, bypass the memory guard, or skip normal scheduler lease/project-capacity checks. The task becomes a normal candidate and may remain queued until capacity is available. If the dependency is still unresolved, leave the task parked.

Billing/spending-limit holds, missing independent review, branch protection and owner-authorization blockers are mapped to specific human actions rather than generic alerts. Ordinary queued/pending CI is not classified as a failure solely because it is waiting.

The dashboard's **Dependency holds** panel displays parked tasks separately from human/external attention. The operator must confirm the dependency resolution and enter a note before using **Resume after confirmation**. Failed queue reads are shown as unavailable, never as “no blocked tasks”.

## Recovery integrity

- Preserve the original title, project, priority, source, dependency evidence and queue identity.
- Keep the task non-runnable while blocked.
- Continue independent work instead of waiting on the blocked task.
- Never infer that a blocked task is dropped, completed, or safe to resume from an old heartbeat, saved PR status, or historical CI observation.
- Do not create or run new QA/regression suites for this workflow. Existing required CI, review, authorization and release gates remain unchanged.

## Atomic human Attention lifecycle

When a worker reports `BLOCKED` for an issue requiring human action, the exact queue task becomes `blocked`/`eligible=0` and a `queue:<queue_id>` Attention card is inserted or updated in the **same SQLite transaction**. Either both writes commit or both roll back; a failed Attention write cannot silently strand an invisible blocked task. The queue item is retained with its dependency evidence and claim released, not dropped or reassigned. Unrelated work may continue under the existing independent-admission rules.

Human Attention classification matches explicit owner/review/permission/paired-device gates. Generic terms such as `watchdog` or `human-readable` alone do not establish human intervention. A physical-watch or paired-device gate still requires Attention.

Only an authenticated, authorized operator may use the existing `portfolio_queue_resume_blocked` flow after a concrete resolution note. That flow atomically restores this exact blocked task to `queued`/`eligible=1`, records an operator-attested (not independently verified) receipt and resolves only the associated `queue:<queue_id>` Attention card. The result includes `attention_resolved` to distinguish a previously open Attention card from none. It does not force a worker slot or claim the resumed task. Independent CI, review, permission and release gates still apply.

**Existing release HOLD:** [PR #1565](https://github.com/Zennay/zCloud/pull/1565) has a prior mandatory OOM-guard validation that expected the historic `dropped` state for a human-gated BLOCKED task; the actual product contract explicitly requires the durable `blocked` state with possible operator resumption. The stale expectation must be reconciled under the authorized review process. No QA/test source was modified or re-run as part of this change.

## Claim-safe periodic queue audit

The periodic queue audit may prune **only** `queued`, `eligible=1`, unassigned tasks that are no longer executable (for example, a newly human-gated project). Its UPDATE checks status, eligibility and `worker_slot IS NULL` again at write time. If a worker claims a task between selection and update, the audit cannot turn that active task into `dropped` or release its slot.

Already `claimed`, `running` or `verifying` tasks are **never silently dropped by audit**, even when their classification changes. Existing owners must finish or park them using normal claim-aware transitions. Both `portfolio_queue_audit(refill=False)` and the refilling mode return `protected_active` with the queue IDs they intentionally preserved. No claims, queue state or running workers are mutated simply by generating the report; the only mutation is the previously supported pruning of truly unassigned non-executable queued tasks. Refill continues to obey project modes and normal capacity/lease gates.

## Dashboard: an Attention card cannot hide its blocked task

For a queue-derived Attention (`attention_id=queue:<queue_id>`), the dashboard now presents **Confirm & resume** rather than an independent **Done** button. It requires the exact blocked task to be visible in the current queue read; if the queue read failed or the task is no longer blocked, the action is disabled. The existing resume dialog asks for a concrete resolution note and confirms that the operator is attesting, not independently verifying, the dependency. On a successful backend resume with `attention_resolved=true`, the dashboard removes both the parked task row and its Attention card before refreshing.

Ordinary, non-queue Attention notices retain their Done action. The click handler also refuses any stale queue-derived Attention Done event. This prevents an apparent green human-alert panel from hiding a task that remains `blocked`/`eligible=0`; it does not authorize release or preempt a worker.

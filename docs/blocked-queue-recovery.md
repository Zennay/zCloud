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

The server requires an existing `blocked` row with `eligible=0`, a live non-human-gated project and a concrete operator note. It records the authenticated actor's attestation and returns the task to `status=queued`, `eligible=1`. The response explicitly says `operator_attestation_not_independently_verified`: this is an operator assertion, not automatic proof that the external dependency cleared.

Resume does **not** force a worker, reserve a slot, steal an active claim, bypass the memory guard, or skip normal scheduler lease/project-capacity checks. The task becomes a normal candidate and may remain queued until capacity is available. If the dependency is still unresolved, leave the task parked.

The dashboard's **Dependency holds** panel displays parked tasks separately from human/external attention. The operator must confirm the dependency resolution and enter a note before using **Resume after confirmation**. Failed queue reads are shown as unavailable, never as “no blocked tasks”.

## Recovery integrity

- Preserve the original title, project, priority, source, dependency evidence and queue identity.
- Keep the task non-runnable while blocked.
- Continue independent work instead of waiting on the blocked task.
- Never infer that a blocked task is dropped, completed, or safe to resume from an old heartbeat, saved PR status, or historical CI observation.
- Do not create or run new QA/regression suites for this workflow. Existing required CI, review, authorization and release gates remain unchanged.

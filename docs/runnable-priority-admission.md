# Runnable-only scheduler priority

zCloud project priority is a preference **between work that is already runnable**.
It is not an admission override and it must never manufacture work, bypass a
dependency, or turn a blocked project into a scheduler candidate.

The executable contract is `scripts/zcloud_runnable_priority_admission.py`.

## Admission order

A future scheduler integration must apply these decisions in this order:

1. Determine whether the project has canonical runnable work from current queue /
   claim / dependency evidence.
2. Apply the existing safety and resource admission gates.
3. Only for rows where both `runnable=true` and `safety_admitted=true`, map the
   project's declared `compute.priority` to a relative ordering weight.
4. Break equal-priority ties deterministically without inventing urgency.
5. Keep excluded projects visible as excluded evidence with effective weight zero.

Current priority weights are deliberately simple and monotonic:

| Declared priority | Relative weight |
| --- | ---: |
| background | 10 |
| normal | 20 |
| high | 30 |
| turbo | 40 |

These weights have no meaning outside the already-admitted candidate set.

## Non-negotiable guardrails

Priority MUST NOT:

- create a runnable queue item;
- bypass a blocker, task claim, dependency or human/external gate;
- bypass memory, IO, load, pool or protected-capacity guards;
- start, resume or push a worker by itself;
- mutate queue, SQLite, project contracts or browser state;
- infer runnable state from priority, activity age, or desired worker count.

Malformed or ambiguous evidence fails closed. Unknown project IDs, unknown
priority values, loose booleans, duplicate rows and unknown evidence fields are
rejected instead of guessed.

## Evidence boundary

The v1 input is intentionally small and structured:

```json
{
  "schema_version": 1,
  "projects": [
    {
      "project_id": "ftmo",
      "runnable": true,
      "safety_admitted": true
    }
  ]
}
```

The policy does not accept task text, prompts, conversation IDs or arbitrary
reason strings. This keeps the output suitable for scheduler/debug observability
without copying sensitive or high-cardinality payloads.

## Integration boundary

This slice is read-only and side-effect free. It does not modify `server.py`,
the allocator, scheduler writers, SQLite, dashboard code, browser automation or
live VPS state while the serialized control-plane writer window is owned
elsewhere.

The roadmap item is only complete after a later serialized integration proves
that canonical runnable evidence is computed before priority, that blocked high
priority projects remain excluded in the live scheduler, and that rollback to
the previous scheduler behavior is bounded and tested.

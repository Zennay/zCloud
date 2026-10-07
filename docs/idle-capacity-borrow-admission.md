# Idle-capacity borrowing admission

zCloud may lend CPU capacity to other projects only when that capacity is
**actually idle after the control-plane reserve and already admitted
non-protected allocation are preserved**.

This slice defines the side-effect-free admission contract only. It does not
change the scheduler, resource governor, SQLite, browser automation, systemd
units or live VPS state.

The executable contract is `scripts/zcloud_idle_capacity_borrow_admission.py`.

## Decision order

A future serialized scheduler integration must:

1. establish total host CPU capacity;
2. subtract the protected control-plane reserve;
3. subtract already admitted non-protected allocation;
4. reject borrowing when memory/IO pressure or explicit backpressure is active;
5. consider only projects that are already runnable, safety-admitted and whose
   current project policy explicitly permits idle borrowing;
6. exclude protected/control-plane projects from the borrower set;
7. consume an explicit upstream scheduler rank rather than inventing priority;
8. grant at most the remaining idle capacity, allowing the last admitted project
   to receive a partial grant;
9. never preempt an existing allocation or spend protected reserve.

`compute_busy` by itself is not a blocker. This matches the existing zCloud
direction that high CPU utilization can be healthy compute rather than pressure;
memory/IO pressure and explicit backpressure remain hard stop signals.

## Evidence contract

The v1 input is intentionally small and structured:

```json
{
  "schema_version": 1,
  "host": {
    "capacity_cpu_cores": 6,
    "protected_reserve_cpu_cores": 1.5,
    "admitted_non_protected_cpu_cores": 2,
    "pressure_state": "healthy",
    "backpressure_required": false
  },
  "projects": [
    {
      "project_id": "ftmo",
      "scheduler_rank": 0,
      "runnable": true,
      "safety_admitted": true,
      "allow_idle_capacity_borrow": true,
      "protected": false,
      "requested_extra_cpu_cores": 2
    }
  ]
}
```

Unknown fields, loose booleans, non-finite or negative numbers, duplicate
projects, duplicate scheduler ranks and inconsistent capacity accounting fail
closed. Arbitrary task, prompt, conversation or reason text is not accepted.

## Composition boundary

This contract deliberately does not reimplement adjacent open control-plane
work:

- project intensity may later supply `allow_idle_capacity_borrow`;
- runnable/priority admission may later supply the ordered `scheduler_rank`;
- host-pressure/backpressure evidence may later supply the pressure fields;
- control-plane resource protection remains the authority for the protected
  reserve;
- requested-versus-admitted resource telemetry remains a separate read model.

Until those inputs are integrated in the serialized scheduler path, this policy
is a proofable foundation rather than live enforcement.

## Completion gate

The parent roadmap item "Idle capacity automatisch laten lenen door andere
projecten" remains open until the serialized runtime integration proves on
current main that:

- real runnable projects can consume otherwise idle capacity;
- protected zCloud/Firefox capacity remains available;
- memory/IO pressure immediately blocks new borrowing;
- rollback restores the prior scheduler behavior;
- requested versus actually admitted borrowing is observable.

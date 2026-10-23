# Resource scheduler composition contract

This slice prepares the **dynamic VPS resource scheduler** without entering the
serialized runtime writer window.

It is intentionally add-only and side-effect free. The executable contract is
`scripts/zcloud_resource_scheduler_composition.py`; it reads bounded evidence
and emits a plan, but does not modify SQLite, systemd, services, browser state,
GitHub settings, queue rows, or production allocation.

## Why this exists

The control-plane already has separate bounded foundations for:

- per-project minimum / target / maximum CPU profiles;
- idle-capacity borrowing;
- safe parallel expansion when work is already proven parallelizable;
- host/backpressure and GitHub Actions queue pressure.

The missing integration risk is **mixed evidence**: each signal can be correct
in isolation while a later scheduler combines them inconsistently. This
contract defines one deterministic composition boundary before live scheduling
is allowed.

## v1 input

The input has exact root keys:

- `schema_version`;
- `host`;
- `projects`.

Host evidence supplies total CPU capacity, protected reserve, currently
borrowable idle capacity, backpressure state, queue-pressure state and whether
the evidence snapshot is complete.

Each project supplies only bounded machine fields: canonical project ID,
scheduler rank, runnable/safety-admitted flags, borrow permission, protected
flag, min/target/max CPU profile, requested CPU, safe/current/capped parallel
job counts and CPU cost per whole job.

Unknown fields, booleans masquerading as numbers, NaN/Infinity, duplicate
project IDs/ranks, impossible profile ordering, over-cap requests and
incoherent protected reserve all fail closed.

## Planning order

1. Reserve every project's declared minimum.
2. If minimum demand exceeds host capacity, emit a bounded `blocked` result.
3. In scheduler-rank order, satisfy runnable + safety-admitted normal demand up
   to each project's target while capacity remains.
4. Opportunistic borrowing is considered only after the base plan.
5. Borrowing is disabled when the evidence source is incomplete, host
   backpressure is required, or queue pressure is not healthy.
6. When borrowing is allowed, grant only whole jobs, bounded by:
   - proven safe parallel jobs;
   - remaining project parallel cap;
   - remaining project CPU maximum;
   - host idle-borrowable CPU;
   - total host capacity.

A pressure hold is itself a valid scheduler decision: the normal base plan
remains usable, while opportunistic expansion is held. An incomplete evidence
snapshot is not decision-ready.

The result also exposes a bounded `planned_allocations` read model per project:
minimum/target/maximum, requested CPU, base CPU, borrowed CPU, final planned CPU
and whether the request is satisfied. Dashboard/telemetry consumers should use
this output directly rather than reproducing allocation math in JavaScript.

## Safety boundary

The output always reports `runtime_mutation=false`. This contract must not be
treated as live enforcement until the serialized control-plane writer/deploy
window is free and a separate integration change:

- binds every upstream evidence source to one fresh snapshot/revision;
- revalidates claims/dependencies/ownership immediately before dispatch;
- writes actual scheduler state transactionally;
- proves rollback;
- exposes requested versus admitted resources;
- passes exact-head full regression and permanent-VPS live evidence.

This slice therefore advances the roadmap from independent policies toward one
coherent scheduler decision contract without taking over any active runtime
owner.


## CLI contract

The command-line interface is intended for a later scheduler wrapper and uses
distinct fail-closed exit semantics:

- exit `0`: the evidence was valid and the result is decision-ready. This
  includes `status=ready` and pressure-driven `status=held`; a held plan may
  keep the bounded base allocation but must not perform opportunistic expansion.
- exit `2`: input/schema/coherence validation failed. The emitted JSON has
  `status=invalid` and remains non-mutating.
- exit `3` with `--require-ready`: evidence parsed but is not safe to use as
  a scheduler decision, for example `source_complete=false`.

Inputs must be regular files; symlink evidence is refused. Consumers should use
`--json --require-ready` and treat every non-zero exit as a denied scheduler
decision rather than falling back to permissive defaults.

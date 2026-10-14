# Safe parallel expansion

The P2 resource goal is to use free CPU aggressively **only when real work is
already proven safe to parallelize**. This contract deliberately separates that
decision from CPU-borrowing capacity, priority, worker-scaling telemetry and
runtime mutation.

The executable policy is `scripts/zcloud_safe_parallel_expansion.py`.

## Inputs

The policy consumes two kinds of upstream evidence:

- a proven `idle_borrowable_cpu_cores` budget after protected capacity and
  existing allocations have already been accounted for;
- per-project evidence that says exactly how many additional jobs are currently
  safe to parallelize, the current job count, a hard project cap, CPU cost per
  job and an already-resolved scheduler rank.

The policy does **not** infer that a task is safe merely because CPU is idle. It
also does not infer priority, runnable state, safety admission, job cost or the
project cap.

## Admission rules

Projects are processed in explicit upstream scheduler order. A project receives
extra jobs only when:

1. global backpressure is not active;
2. the project is runnable;
3. the project passed safety admission;
4. at least one additional job is explicitly proven safe to parallelize;
5. the project is below its hard parallelism cap;
6. enough idle CPU exists for at least one whole job.

The number admitted is the minimum of safe jobs, remaining project-cap room and
whole jobs that fit the remaining CPU budget. Fractional jobs are never created.
Unused CPU may remain when it is smaller than the cheapest next safe job.

## Composition boundary

This is an add-only foundation, not live enforcement. A later serialized
scheduler integration should compose:

- #839-style idle borrowing evidence for the available CPU budget;
- runnable/priority admission for scheduler order;
- worker-scaling/diminishing-return evidence for safe parallel job count;
- host-pressure/backpressure evidence;
- canonical project hard caps.

The runtime integration must revalidate all of these immediately before
dispatch. This policy must never be used as a reason to create runnable work or
to bypass claims, dependencies, human gates, resource protection or worker
limits.

## Completion gate

The roadmap item "Vrije CPU agressief benutten wanneer veilige
paralleliseerbare jobs bestaan" remains open until current-main integration
proves that extra real jobs are dispatched only under these conditions, resource
telemetry observes the resulting allocation, and rollback returns to the prior
scheduler behavior.

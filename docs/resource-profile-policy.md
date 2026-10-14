# Per-project CPU resource profile policy v1

This slice defines a **candidate-only** min/target/max CPU profile for every canonical zCloud project.

It deliberately does not change the live scheduler, allocator, SQLite state, systemd settings, browser automation, dashboard, or deploy path. The serialized runtime/deploy window remains owned elsewhere.

## Compatibility rule

During this foundation phase:

- `target_cpu_cores` must exactly match the project's existing `compute.cpu_soft_cores`;
- `minimum_cpu_cores <= target_cpu_cores <= maximum_cpu_cores`;
- protected projects may not set a minimum below their current soft CPU value;
- projects in the `disabled` pool must stay at 0/0/0;
- every canonical project must appear exactly once;
- malformed, unknown, non-finite, boolean-as-number, oversized, or symlinked inputs fail closed.

The initial policy therefore preserves existing behavior: non-protected projects use 0/target/target, protected projects retain target/target/target, and disabled projects use 0/0/0.

## Intended later integration

After the serialized control-plane writer/deploy lane is free, a separate change may migrate these fields into the canonical runtime contract and consume them in the real scheduler. That integration must compose with runnable-priority admission, intensity policy, host-pressure/backpressure, idle-capacity borrowing, requested-vs-admitted telemetry, and control-plane protection.

No roadmap checkbox should be considered complete until live scheduler enforcement and rollback are proven on current main.

# zCloud fault-recovery simulation matrix v1

This gate covers the roadmap requirement to simulate three control-plane failure modes together:

1. Firefox worker runtime disappears while an allocated worker is offline.
2. zCloud service/schema bootstrap is repeated after a worker conversation has been adopted.
3. A heavy-pool resource owner crashes and leaves an expired lease.

The matrix deliberately reuses existing deterministic simulations instead of killing live services. It never stops Firefox, restarts systemd, writes the production SQLite database, mutates queue state, or deploys code.

The Firefox scenario proves the watchdog asks the existing control API for bounded Firefox recovery when runtime is lost. The service-restart scenario proves repeated zCloud bootstrap preserves the worker conversation binding. The stale-lease scenario proves expired ownership is removed before a new heavy project is admitted. The extension recovery contract is also executed to guard tab-session recovery/handoff invariants.

A later production incident can justify separate live canary evidence, but this checklist item is a simulation requirement; production fault injection is intentionally outside this gate.

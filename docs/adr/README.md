# zCloud Architecture Decision Records

Use an ADR when a proposed change touches a high-blast-radius zCloud surface. The guard in `scripts/zcloud_high_blast_radius_adr_guard.py` determines whether the candidate diff requires one.

An ADR is evidence for a consequential architecture choice, not a substitute for tests, rollout safeguards, or human approval where those are already required. Keep each record specific to one decision and commit it in the same pull request as the risky change.

## Required format

Create a Markdown file under `docs/adr/` named with a bounded machine-friendly prefix and slug, for example `20261006-control-plane-state-writer.md`.

Each ADR must start with `# ADR` and contain all of these sections with substantive content:

- `## Context` — what problem or constraint makes the decision necessary.
- `## Decision` — the chosen approach and relevant boundaries.
- `## Affected Paths` — list every high-blast changed repository path covered by this decision as an exact backticked path, for example `server.py`.
- `## Blast Radius` — what projects, workers, state, runtime or operators could be affected.
- `## Rollback` — the concrete path back to a known-good state.
- `## Validation` — the exact tests, canary, smoke or evidence required before acceptance.

Placeholders such as TBD/TODO are rejected by the guard. A changed high-blast path is accepted only when at least one ADR in the same pull request names that exact path under `Affected Paths`; an unrelated ADR does not satisfy the gate.

## Current high-blast-radius surfaces

The v1 policy treats the central runtime writer, canonical project/resource/autonomy contracts, and deployment/recovery/restart/reconcile/scheduler/allocator/watchdog-style workflows or scripts as high blast radius. The policy is intentionally bounded; expanding it should be a deliberate reviewed policy change rather than an implicit heuristic.

The guard is read-only. It reports only bounded risk categories and counts, never raw diffs or runtime data.

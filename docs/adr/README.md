# zCloud Architecture Decision Records

This directory is the versioned decision log for architectural changes whose production blast radius is classified as high by zCloud's guarded promotion policy.

Use the next unused four-digit number and a short lowercase slug:

`docs/adr/NNNN-short-decision-title.md`

Low-blast changes do not need an ADR. A high-blast pull request must change at least one valid numbered ADR and the ADR must contain these non-empty sections:

- `## Status` — Proposed, Accepted, Superseded, or Rejected.
- `## Context` — the problem, constraints, and relevant prior state.
- `## Decision` — the chosen architecture or policy.
- `## Consequences` — expected benefits, costs, and tradeoffs.
- `## Evidence` — tests, measurements, incidents, or other proof supporting the decision.
- `## Rollback` — the bounded way to reverse or disable the decision if evidence turns negative.

Do not use ADRs to bypass runtime safety. High-blast production promotion still requires its own normal pre-change, feature-flag, transactional promotion, rollback, post-deploy, and evidence gates.

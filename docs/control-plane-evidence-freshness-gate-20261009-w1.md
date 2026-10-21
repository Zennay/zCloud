# Control-plane evidence freshness gate (2026-10-09)

This document is a **non-authorizing review checklist**, not a production release approval. It does not change VPS state, SQLite, worker allocation, CI, or deploy permissions.

## Evidence is head-bound

Before accepting any control-plane PR, record its exact head SHA and the current `main` SHA. A success on an earlier head is not proof for a new commit. Re-run the affected focused suite and regression after a changed head or a restack; distinguish hosted checks from permanent-VPS proof. Record terminal GitHub run URLs, their conclusion, and the SHA actually checked. Queued/in-progress/skipped jobs are not green evidence.

## Negative-case acceptance

A contract suite with expected failures is evidence of *known production gaps*, not remediation. In particular, receipt callback tests must prove that a terminal command cannot be overwritten by a late callback, unknown command IDs are not acknowledged as success, and IDs outside the supported positive safe-integer range are rejected. Do not remove expected-failure markers until the actual handler passes those same cases.

## Ownership and integration

Inspect open PRs and PR-less branches for the exact files to be changed before claiming a slice. Keep #580/PWQ-41 and #1089 as the serialized production integration window; do not bypass their owners through an ancillary workflow or documentation PR. Follow the canonical replacement/supersession PR when another owner consolidates the same files.

## Runtime safety

A GitHub Actions queue reduction alone does not prove browser generation, SQLite consumer progress, or VPS health. Require a fresh trusted-main read probe with explicit listener/SQLite/worker heartbeat evidence before targeted recovery. Do not fire an untrusted PR workflow at the self-hosted runner, restart healthy services, force-push all workers, or infer a running worker from Notion claim state alone. Preserve the FTMO-first resource policy.

## Reviewer record

Capture: PR / owner, exact head SHA, exact base SHA, changed-file collision check, focused run and result, full regression run and result, VPS proof where relevant, outstanding expected failures, live-operation authorization, and explicit final reviewer decision. Missing data means **not yet accepted**, never implicit approval.

Context: zCloud Notion Current State & AI Handoff (2026-10-08 22:07 UTC); open PRs #1220–#1223 reviewed 2026-10-09. This checklist is deliberately file-disjoint from their implementations.

# Control-plane parallel handoff: collision-free evidence protocol

Status: **advisory only**. This document does not grant merge, release, worker, queue, or VPS mutation authority.

## Why this exists
Concurrent control-plane workers can independently prepare valid PRs while a serialized production writer or deploy gate remains occupied. A green CI run for one commit does not prove current-main compatibility or ownership isolation. Use immutable snapshots and stop on ambiguity.

## Minimum handoff record
For each proposed change, record:

- Repository and PR URL, immutable head SHA and exact base/main SHA at inspection time.
- Claimed feature/capability and every modified path, including generated and workflow files.
- Open PR owners **and PR-less branches** overlapping any path or capability.
- Current release gates (#580 / PWQ-41 and #1089 or their verified successors), with live status checked rather than inferred from old handoff notes.
- CI workflow, run URL, run head SHA, terminal conclusion, and whether the check covers the changed paths.
- Outstanding expected failures, independent reviewer and explicit production integration owner.
- Explicit declaration of operations **not performed**: deploy, persistent SQLite changes, queue dispatch, service changes or browser worker actions.

## Before claiming work
1. Read the latest Notion project state, but treat live GitHub ownership and branch/PR states as the primary collision evidence.
2. Search open PR titles, bodies and changed files, then search branches even when no PR exists. Compare capability ownership, not just filenames.
3. If two workers claim the same feature, stop writing and reconcile with the existing owner; a new filename does not make overlapping semantics safe.
4. Prefer add-only, file-disjoint offline checks when runtime and writer gates are occupied. An offline reference model must never be described as actual API conformance.
5. Take an exact-current-main snapshot. Any subsequent main/head or owner change invalidates that snapshot for admission purposes.

## Before review or merge
1. Check the entire diff against current main, not merely the initial commit.
2. Confirm changed-path ownership has remained collision-free since the snapshot.
3. Require terminal successful checks on the **final head SHA**. An earlier green run is historical context only.
4. Do not count expected failures as fixed production behavior. Document every known red contract and its remediation owner.
5. Recheck serialized gates immediately before a release decision. No checklist or green result bypasses an occupied gate.
6. Independently validate deployment-specific effects on the authorized permanent runner only when the integration owner permits it.

## Failure response
- **Overlapping ownership:** keep the new change draft and hand it off; do not replace an active owner's files.
- **Main drift / stale CI:** produce a fresh comparison and run evidence; do not restack another owner's branch without permission.
- **Unknown PR-less branch:** treat it as occupied until provenance is established.
- **Gate occupied:** continue independent offline work; no live queue, SQLite, worker, service, browser, or deployment changes.
- **Mismatch between Notion and GitHub:** record both observations, timestamps and immutable links; do not silently select the older entry.

## Evidence handoff template
```text
Capability:
Owner / branch / PR:
Base main SHA:
Head SHA:
Changed paths:
Related open PRs and PR-less branches:
Serialized gate snapshot:
Exact-head checks (run URL, SHA, terminal result):
Known expected failures / negative tests:
Non-mutated live resources:
Integration reviewer:
Decision: DRAFT / REVIEW-READY (never an automatic merge/deploy authorization)
```

This protocol provides traceability, not an automated lock. Actual worker-claim leases and server enforcement must be validated by their designated owners.

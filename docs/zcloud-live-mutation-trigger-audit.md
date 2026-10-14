# zCloud automatic live-mutation trigger audit

`scripts/zcloud_live_mutation_trigger_audit.py` is a read-only deploy-ops inventory for GitHub Actions workflows that combine an automatic trigger with a conservative mutation primitive.

## Why this exists

The self-hosted trust inventory answers whether a workflow has runner/provenance debt. This audit answers a different question: **can a workflow file plausibly reach live mutation authority without an explicit human dispatch?**

It is intentionally conservative. A candidate is not proof that every trigger reaches the mutating step; job-level `if:` guards may narrow execution. Missing evidence is never approval to mutate.

## Run

```bash
python3 scripts/zcloud_live_mutation_trigger_audit.py --json
```

To make unresolved automatic candidates fail the command:

```bash
python3 scripts/zcloud_live_mutation_trigger_audit.py --json --require-clear
```

Exit codes:

- `0`: inventory completed; either enforcement was not requested or no automatic candidates remain.
- `1`: inventory completeness could not be proven.
- `2`: `--require-clear` was requested and at least one automatic candidate remains.

The report contains only bounded path/trigger/reason-code metadata. It does not emit workflow bodies and never performs a mutation.

## Candidate handling

For each `automatic_live_mutation_candidate`:

1. Re-run the canonical open-PR and PR-less branch ownership checks before claiming remediation.
2. Inspect the exact job-level admission guards and identify whether the mutation can actually run for each automatic trigger.
3. If automatic execution is unnecessary, prefer hosted validation plus an explicit `workflow_dispatch` main-only live job.
4. If automatic execution is required, prove exact revision, permanent runner identity where relevant, bounded non-cancelling concurrency, idempotence, rollback/recovery semantics, and least-privilege permissions.
5. Keep production/recovery writers serialized while an existing writer window is active.
6. Validate the exact candidate head before integration and re-run this inventory after the remediation lands.

## Detection boundary

Current reason codes cover zCloud queue writes, runner-control writes, systemd mutation, Git push, GitHub/API write methods, container mutation, and writes to common system configuration roots.

The audit deliberately does not treat permissions such as `contents: write` as proof of a mutation by themselves. It also does not infer safety from absence of a marker. New mutation primitives should be added with focused regression coverage before relying on the inventory for enforcement.

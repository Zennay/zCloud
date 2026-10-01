# Cloud permanent VPS-first deploylane

## Assignment
- Queue item: `cloud-permanent-vps-first-deploylane`
- Project: `cloud`
- Goal: permanent VPS-first deploylane without temporary HaxLab bridge.

## Deploylane contract
1. Changes are delivered through GitHub.
2. VPS execution must run through the configured self-hosted runner route.
3. Deploy evidence must include workflow run id and result.
4. Temporary project bridges are not considered the permanent path.

## Current implementation checkpoint
This document records the contract while the deploy workflow implementation is being completed and verified.

## Permanent runner identity guard
- Production deploy and execution-probe workflows now fail closed unless the runtime host matches `vps-bb300bba`.
- Any runner whose `RUNNER_NAME` contains the forbidden token `haxlab` is rejected before claims or production writes.
- The guard is policy-backed by `vps-execution-policy.json` and covered by regression tests.
- Sanitized runner-identity evidence is uploaded with the deploylane probe.

## Audited runtime drift recovery
- Runtime-mutated `resource-policy.json` drift is preserved only when SQLite `config_audit` proves the exact current priority was written after the active LKG snapshot.
- Untracked/manual resource-policy drift remains fail-closed.
- Known non-runtime userscript backup files are moved into the existing zCloud runtime-backup directory before the pre-change guard, never deleted.
- This keeps dashboard-owned resource priorities persistent without weakening source-drift protection for executable files.

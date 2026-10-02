# LightUp

LightUp is an AI-assisted defensive security lab for **owned or explicitly authorized systems**.

## Current phase: M0 — infrastructure only

This scaffold deliberately has **no network execution path**. It can:

- define and validate target scope;
- model authorization metadata;
- build assessment plans from a capability registry;
- normalize findings and remediation state;
- render reports;
- prove fail-closed behavior with tests.

It cannot yet send requests, scan ports, authenticate to targets, or execute exploit logic. Network-capable adapters are a later phase and require an explicit project-level activation decision.

## Safety invariants

1. Unknown public targets fail closed.
2. Public targets require exact host or CIDR authorization.
3. Private/loopback lab targets can be permitted by policy.
4. Authorization has an owner/reference and optional validity window.
5. Every future active adapter must pass through the same scope gate.
6. No adapter may silently widen target scope.
7. Findings must include evidence, remediation, and retest state.
8. Parallel workers claim non-overlapping capabilities/write scopes before changes.

## Architecture

```text
AuthorizedTarget -> ScopeGate -> AssessmentPlanner -> CapabilityAdapters (future)
                                      |
                                      v
                              Finding + Evidence
                                      |
                                      v
                              Remediation/Retest
```

## Quick start

```bash
cd lightup
python -m unittest discover -s tests -v
PYTHONPATH=src python -m lightup.cli scope-check 127.0.0.1
PYTHONPATH=src python -m lightup.cli plan 127.0.0.1
```

The `plan` command is intentionally non-invasive: it only emits a structured plan.

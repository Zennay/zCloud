# Dashboard recovery trust contract (2026-10-08)

This is an **operator-facing, non-executable** safety contract for the zCloud dashboard recovery lane. It does not grant permission to restart services, touch SQLite, dispatch workers, or alter the production gate.

## Event and authority boundaries

| Context | Allowed | Forbidden |
| --- | --- | --- |
| `pull_request` | GitHub-hosted, read-only validation and external reachability checks | Privileged `recover`, VPS/self-hosted mutations, service restarts |
| Manual `workflow_dispatch` | Explicitly reviewed diagnostics only | Treating a manual click as sufficient authorization for recovery |
| Trusted production recovery | Only after an independently reviewed, exact-main and serialized authority contract is landed | Reusing an untrusted PR head or bypassing the #580/PWQ-41 + #1089 deployment gate |

PR #1143 deliberately **quarantines** the privileged `recover` job even on manual dispatch. This is a temporary safe-disabled state, not a production recovery implementation. Keep the quarantine until a separate authorized change is reviewed.

## Interpret results independently

- `external_verify=success`: public status endpoint answered with valid expected data **from the hosted vantage point**. It does **not** prove the VPS listener recovery path.
- `external_verify=failure`: reachability or response validation failed from that vantage point. It does **not** prove dashboard outage or authorize a restart. Preserve the HTTP/transport/parse distinction in future diagnostics without leaking credentials.
- `recover=skipped`: expected safety behavior under #1143; not a failed health recovery and not evidence of remediation.
- `recover=failure`: for legacy workflow runs, inspect the exact command and runner context; do not infer from this alone that the public dashboard is unavailable.

## Before any future live enablement

1. Complete a fresh inventory of **all pages** of open PRs and PR-less branches and confirm there is no file/authority overlap.
2. Resolve the serialized #580/PWQ-41 and #1089 ownership window; require current-main exact-head acceptance for the proposed runtime/deploy authority.
3. Prove the PR event path is hosted/read-only, does not schedule a self-hosted or privileged recovery job, and cannot chain into a mutating reusable workflow.
4. Require an immutable trusted revision, explicit environment/runner identity checks, bounded retries/timeouts, and a separate non-cancelling live concurrency group.
5. Record a machine-verifiable receipt for any permitted recovery: triggering authority, source SHA, runner, action, outcome and post-action independently observed health. Never equate a skipped recovery with success.
6. Re-run targeted workflow trust tests and full regression on the **exact candidate head**. Keep any production dispatch separate and behind operator approval.

## Related work

- #1135: unsafe PR-triggered privileged recovery incident.
- #1143: safety quarantine; **do not merge before owner coordination**.
- #580 / PWQ-41 and #1089: active serialized production/control-plane gate.
- #1046: separately owned worker activation trust; do not alter its workflow in this slice.

This document may be reviewed and integrated independently because it changes **no workflow, source runtime, test, or existing runbook path**.

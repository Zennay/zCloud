# Dashboard recovery: deployment acceptance boundary (2026-10-08)

Scope: read-only deploy-ops acceptance guidance. This document does **not** authorize running the privileged dashboard recovery workflow, changing the live VPS, or merging another worker's branch.

## Observed baseline

At the time of writing, `main:.github/workflows/zcloud-dashboard-access-recovery.yml` responds to `push`, `workflow_dispatch`, and `pull_request`. Its `recover` job uses `runs-on: self-hosted` and runs privileged operations including `systemctl stop/restart`, database ownership/mode repair, potential removal of SQLite immutable attributes, and an optional systemd public-bind drop-in. Its separate `external_verify` job checks the public status API from `ubuntu-latest`.

A failed public probe is **not** evidence of broken SQLite permissions or grounds to run a privileged repair. An externally healthy API is not evidence that a PR-triggered recovery is safe. See #1135 and #1166.

## Required acceptance matrix

| Trigger / observation | Hosted external verification | Privileged recover | Deployment interpretation |
| --- | --- | --- | --- |
| Untrusted PR, any probe result | May run as read-only, with bounded timeouts | MUST skip | Never change production on PR event |
| Push to feature branch | May run as read-only | MUST skip | Not an authorized production-recovery trigger |
| Push to main | May run as read-only | MUST skip unless an independently approved gated procedure explicitly authorizes it | A commit alone is not repair consent |
| Manual dispatch, missing evidence/approval | May run as read-only | MUST skip | Fail closed |
| Manual dispatch, approved and scoped repair, stale evidence | May run as read-only | MUST skip | Revalidate before mutation |
| Manual dispatch, approved and scoped repair, current independently established failure | May run as read-only | Allowed only within reviewed bounded procedure | Require post-action local AND external verification |
| External probe timeout / invalid JSON / non-2xx | Report a specific non-mutating failure category | MUST NOT auto-recover | Investigate transport, HTTP, and API separately |
| External status shape valid | GREEN for externally observed API only | No action implied | Does not prove internal repair is needed or safe |

## Evidence checklist before any production mutation

1. Current exact `main` SHA and triggering event/ref are recorded; no PR head is promoted to a trusted source.
2. An explicit authorized human-triggered remediation gate identifies the specific service, host, and action. The presence of `workflow_dispatch` alone is insufficient.
3. Public reachability, HTTP status, response parse, and required JSON fields are separately classified. A hosted probe has no sudo, database writes, restart, or self-hosted runner requirement.
4. Independent, fresh on-host evidence establishes the actual failure before choosing a corrective action; connectivity failures alone do not justify repairing SQLite.
5. The serialized #580/#1089 production/control-plane writer gate is released by its owners, and existing branches/PRs are checked for file ownership immediately before integration.
6. Changes are bounded to authorized resources and leave an audit trail. No new generic PR-triggered self-hosted mutation path is introduced.
7. Post-action checks establish both local service and external API health; a failed external test must not trigger an unbounded restart loop.
8. Exact-head CI and VPS validation are recorded for the final candidate SHA; queued or skipped checks are not described as green.

## Non-goals

This contract neither implements the already owned workflow hardening nor modifies `history.db`, the runner, firewall, systemd, or dashboard endpoints. Deployment remains gated until separate production-safe implementation and evidence satisfy the checklist.

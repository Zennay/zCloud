# Control-plane worker evidence classification (2026-10-09)

## Purpose
This document defines the minimum evidence required before zCloud reports worker continuity, fleet health, or safe recovery. It is a **non-authorizing** control-plane contract: no worker dispatch, runner restart, merge, deploy, or SQLite mutation follows from this document alone.

## Evidence classes
| Signal | What it proves | What it does not prove |
| --- | --- | --- |
| Configured worker slot | A slot is configured | A browser is running or producing generations |
| Notion Running / worker claim | A claim was recorded | A live generation or fresh VPS heartbeat |
| `prompt-sent` | Prompt delivery was attempted | Model generation started |
| `generation-started` with timestamp | Generation began at that timestamp | Completion or task materiality |
| `generation-completed` | Generation ended | Code, PR, test or runtime work occurred |
| GitHub commit/PR | A repository mutation exists | Acceptance, merge or production deploy |
| Hosted CI success | Hosted checks passed on a specific SHA | Self-hosted VPS readiness |
| Self-hosted Actions success | A specified job ran on a runner | Listener and all browser workers are healthy |
| Production status success | Exact commit's production context is green | Newer main SHA or current worker health |
| Fresh API/SQLite heartbeat | Sampled endpoint/database answered at observation time | End-to-end generation without correlated IDs |

## State decision
1. Report **unknown** when telemetry is missing, stale, cross-SHA, uncorrelated, or has conflicting lifecycle events at the identical latest timestamp. Never translate unknown into healthy, stopped, or permission to restart.
2. Report **attempted** after prompt-sent only. Do not count it as productive.
3. Report **generating** only with a fresh generation-started signal tied to the same assignment and worker; report **completed** for a fresh correlated generation-completed event (not material progress).
4. Report **material progress** only with durable evidence (commit, changed file, PR, test/build, queue transition, or confirmed worker task) linked to the assignment.
5. Report **accepted** only after required exact-head checks succeed and any explicit integration gates clear.
6. Report **deployed** only with exact-main production evidence after deployment; an older SHA or PR check is insufficient.

## Recovery admission
Recovery needs both a *proven fault* and *current authorization*. Require a fresh correlated listener/process/API/SQLite observation, inspect current runner occupancy and serialized owners, and refuse PR-triggered privileged recovery. HTTP 503 during a restart is not evidence that restart was justified. A missing runner-registration GET route in an API connector is not evidence that the runner is offline.

## Operational ordering
Preserve FTMO-first resource priority. If a check is queued behind active VPS work, treat it as waiting rather than failed; perform independent hosted validation without dispatching duplicate VPS workloads. Never cancel an active production deploy or take over another branch/PR owner. Prefer targeted recovery to global restart.

## Non-authorizing review checklist
- [ ] Exact repository, branch, SHA, run ID, assignment ID and observation time recorded
- [ ] Signal's claim is no stronger than its measured evidence
- [ ] Staleness and absence distinguished from explicit failures
- [ ] Worker assignment and generation correlated
- [ ] Permanent-VPS proof not conflated with hosted CI
- [ ] Open PR and PR-less branch ownership checked immediately before any integration work
- [ ] #580/PWQ-41 and #1089 serialized boundaries respected until explicitly released
- [ ] No automated mutation implied by this classification

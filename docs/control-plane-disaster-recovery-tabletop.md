# Control-plane disaster recovery: non-mutating tabletop

Status: **exercise only**, not deployment authorization. This document is independent of the live writer/deploy work owned by #580 and #1089. Do not execute recovery actions against production as part of this exercise.

## Evidence before intervention

Capture observations with UTC timestamps and distinguish each source:

| Signal | Authoritative for | Not proof of |
| --- | --- | --- |
| GitHub main SHA and main-bound production status | source revision and last recorded deployment result | live API/listener availability |
| GitHub Actions queued/running jobs | workflow scheduler state | VPS resource exhaustion or runner offline |
| VPS-local `systemctl is-active zennay-cloud.service` | service manager state at observation time | healthy HTTP API or active browser generations |
| VPS-local `python3 scripts/zcloud_healthcheck.py --json` | healthcheck contract at observation time | durable browser generation or future health |
| Read-only SQLite task/claim/receipt samples | queue ownership and persisted progress at observation time | successful user-visible browser generation |
| Browser `generation-started` and follow-up lifecycle evidence | actual generation activity | successful committed work without artifact/CI evidence |

Record unobservable signals as **unknown** (never infer healthy or unhealthy from absent access). Keep all sensitive tokens, prompt text, chat identifiers and raw claims out of shared evidence.

## Drill 1: CI backlog, service healthy

**Inject**: 100 queued GitHub Actions jobs; a trusted, independently observed main-bound VPS healthcheck is green.

**Expected**: classify as CI backlog, not a zCloud outage. No global worker restart, runner deregistration or browser replay. Investigate runner labels/capacity separately and preserve FTMO-first compute allocation.

**Failure**: treating job queue depth as proof that zCloud is offline.

## Drill 2: HTTP 503 during a healthy service restart

**Inject**: a single transient `/api/status` 503 while service manager is active and adjacent trusted samples pass.

**Expected**: bounded read-only retry with timestamps; classify transition vs sustained failure. Restart only after a confirmed fault and existing recovery guard approvals. Never use an untrusted PR-head workflow to restart production.

**Failure**: chaining a second restart merely because the first restart briefly returned 503.

## Drill 3: stale worker claim but no generation

**Inject**: a current SQLite claim and `prompt-sent`, but no observed `generation-started` within the existing 120-second generation deadline.

**Expected**: do not mark work complete or infer live generation from a claim. Confirm the canonical no-generation recovery path and its bounded same-assignment replay. Do not duplicate prompts, bypass the existing anti-spam gate, or create a new worker owner.

**Failure**: the dashboard says “working” solely because the claim exists.

## Drill 4: two contradictory status sources

**Inject**: successful PR-level checks and a stale main production status; VPS listener/SQLite evidence unavailable.

**Expected**: preserve separate labels: PR CI success; current production state unknown. Do not promote PR checks to deployment evidence or replace unknown VPS metrics with old numbers.

**Failure**: global “green / deployed” claim.

## Drill 5: writer ownership collision

**Inject**: a candidate change to `server.py` while serialized runtime owner #580 and deploy owner #1089 remain open.

**Expected**: stop that change. Check paginated open PRs and PR-less branches, choose an independently owned path if available, otherwise preserve the gate. Do not force merge or overwrite another worker.

**Failure**: a second writer or undocumented production mutation.

## Pass criteria and audit record

For every drill, capture the evidence time, signal/source, inferred state, uncertainty, permitted next action, forbidden action and owner. Pass requires **zero production mutations**, no fabricated freshness, no ownership collision, and no restart triggered solely by queue depth or a transient HTTP response.

This document defines exercises only; it does not certify any run as passing. Real recovery still follows `docs/zcloud-control-plane-recovery-runbook.md` and the guarded main-bound workflow. Do not change the operational gate based on a tabletop result.

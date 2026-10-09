# Control-plane worker continuity: evidence and safe recovery contract

Status: documentation-only, non-authorizing companion to issue #1218. This file does not grant runner, browser, deploy, or queue write authority.

## Source of truth and identities

For each observed worker slot record `slot_id`, project/assignment identity (redacted), browser/transport identity, last accepted prompt timestamp, last **generation-started** timestamp, last **progress** timestamp, and the authoritative observation time. Keep SQLite operational queue distinct from Notion documentation state. A `Running` Notion claim or `prompt-sent` event is **not** generation evidence.

## Decision table

| Observation | Classification | Permitted next action |
| --- | --- | --- |
| Generation is active with fresh progress | healthy | Observe; do not resend, restart, or close the tab |
| Prompt sent without generation-started; deadline not passed | pending | Observe; preserve assignment identity |
| No generation-started after bounded watchdog deadline | suspected no-generation | Use the existing owner-controlled exact-assignment recovery path only; avoid widening scope or rapid resubmits |
| Firefox process present but browser heartbeat missing | unknown | Obtain independent service/browser evidence before choosing targeted recovery |
| VPS memory/swap evidence absent or stale | capacity unknown | Do **not** force eight slots or spawn additional Firefox instances |
| OOM/recent memory pressure established | capacity constrained | Reduce concurrency first; protect FTMO and control-plane reservations |
| Production status missing, failed, or unverifiable | release unknown | Fail closed; do not dispatch privileged recovery based on branch-level CI |
| Claim held by another unexpired owner | owned | Do not replace, cancel, release or duplicate the task |

## Invariants

1. No more than one live generation per canonical assignment; a resend preserves the exact assignment ID and remains bounded by existing cooldowns.
2. Capacity admission is based on **fresh VPS** CPU, memory, swap and active browser counts, not eight configured slots.
3. A green PR test is not a successful deploy or live worker heartbeat.
4. An unhealthy API probe alone (including transient HTTP 503 after restart) is not proof that the whole service is down.
5. Do not restart healthy Firefox generations, mutate another worker's branch, or operate a production recovery workflow from untrusted PR code.
6. The FTMO-first CPU reservation and protected control-plane floor remain intact.
7. Never display raw prompts, conversation tokens, management secrets or full claim metadata in public evidence.

## Fixture acceptance scenarios

- A configured pool of eight with only three observed active generations reports **three**, not eight.
- Two successive snapshots with `prompt-sent` but no `generation-started` stay pending until watchdog expiry; at expiry, only the owning existing recovery path may retry once according to its cooldown.
- Active `generation-started` evidence blocks any force-push regardless of stale Notion claim metadata.
- A missing resource snapshot produces `capacity_unknown`, not `capacity_available`.
- Fresh memory pressure blocks new browser admission even with idle CPU.
- An unavailable SQLite/runner heartbeat produces `unknown`, never `healthy` or automatic global restart.
- A PR-level green smoke test with missing exact-main production receipt does not pass release acceptance.

## Handoff

Coordinate with #1218 and the established browser recovery owners (#938, #948, #1087) before any implementation. Serialized #580/PWQ-41 and #1089 remain closed to this documentation lane. For integration, attach the exact final commit SHA, file-ownership census, permanent self-hosted runner run IDs, and independent live generation/queue evidence. No live recovery is requested by this document.

# Control-plane degraded-status copy contract (offline, non-authorizing)

This document specifies **user-visible status language**, not control-plane permissions. It deliberately does not modify `server.py`, API responses, scheduler, SQLite, queues, browser automation, runner or deployment. Owners #580/PWQ-41 and #1089 retain integration authority; #1143 retains dashboard recovery.

## Rules

1. Separate **observed state** from **inferred health**. A green GitHub check is not proof of a running browser generation or a reachable VPS service.
2. Always show evidence timestamp, source and scope. If age or provenance is missing, display **Status unknown**, never **Healthy**.
3. A `prompt-sent` event is **Prompt submitted**, not **Worker generating**. Only correlated generation-start evidence supports **Generation observed**; completion still does not imply material progress.
4. Avoid declaring a worker stopped from an expired or empty GET claim snapshot; expired-claim cleanup is not proof of deliberate release.
5. For transient HTTP 503, say **Service temporarily unavailable; live state unverified**. Never display **Worker stopped** or automatically request restart based on this alone.
6. Suppress success claims for a PR whose passing checks apply to an earlier SHA; say **Checks pending for current revision**.
7. Every operational banner is informational; displaying or dismissing it never grants permission to dispatch, restart, merge, deploy or modify queues.

## User-facing decision matrix

| Available evidence | Primary label | Supporting explanation | Forbidden claim |
| --- | --- | --- | --- |
| Fresh, correlated generation-start with worker identity | Generation observed | Last observed at <time> via <source>; activity may change. | Task completed |
| Prompt-sent only | Prompt submitted | No verified generation-start event yet. | Working now |
| Stale heartbeat | Status unknown | Last verified signal at <time>; current operation not confirmed. | Offline / crashed |
| API 503 or timeout | Service temporarily unavailable | Live worker and queue state cannot be verified. | Workers stopped |
| Queue claim expired or missing from GET | Claim state unverified | Cleanup is not an explicit release event. | Worker released |
| GitHub PR checks on previous commit | Checks pending for current revision | Prior passing results are not valid for this commit. | Ready to deploy |
| Fresh healthy endpoint only | API responding | API reachability does not attest browser generation. | Workers generating |
| Conflicting clocks or producers | Conflicting observations | Sources disagree; show provenance and collect new evidence. | Healthy |


## Multi-source precedence and loss-of-evidence boundary

- **No evidence / unavailable producer:** render `Status unknown`; keep last-known evidence explicitly historical, never silently promote it to current.
- **Contradictory fresh producers:** render `Conflicting observations` with separate producer/source timestamps; do not pick the most optimistic state.
- **One fresh source, one stale source:** identify both timestamps and scopes; fresh API reachability alone cannot overrule stale or unknown browser-generation evidence.
- **Replayed event or missing worker identity:** do not infer `Generation observed`. An older generation-start event cannot attest activity in a newer assignment.
- **Clock skew, missing timezone or future timestamps:** treat freshness as unverified rather than treating impossible timestamps as newest.
- **Recovered telemetry:** change from `Status unknown` only when fresh, correlated, same-assignment evidence is available; do not backfill a success claim.
- **Unverified recovery action:** never claim that a service was restarted, a worker recovered, or a queue resumed from a passive dashboard message.

This precedence is display guidance only. UI visibility, operator intent, and green checks cannot authorize recovery actions. Ownership and release gates stay with the designated integration owners.

## Acceptance checklist (manual UI review; no production authority)

- [ ] Copy remains usable without colors, icons or tooltips; screen-reader text spells out unknown/observed distinction.
- [ ] No state becomes more positive when sources vanish, claim GET cleans up, or provenance is invalid.
- [ ] No CTA implies permission to restart, push, merge or deploy from passive observations.
- [ ] Timestamps include timezone, freshness and source; stale signals never silently become live.
- [ ] Copy references the exact PR SHA for CI statuses.
- [ ] Owners review independently before any integration; serialized writer and deploy gates remain unchanged.

**Authority:** `mutation_authorized=false`; `restart_authorized=false`; `merge_authorized=false`; `deploy_authorized=false`.

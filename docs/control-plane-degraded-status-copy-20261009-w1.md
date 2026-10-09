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


## Recovery-check failure: user-visible containment

If dashboard recovery CI fails while other CI checks pass, display **Dashboard recovery check failed** with the exact failing run URL and exact commit identity. The passing contract, regression and CPU checks remain individually reportable, but **overall operational readiness is unverified**. This is not proof that production is down, and it does not authorize a restart, rollback or dispatch. Never infer a safe recovery action from the failure alone.

If a dashboard recovery check fails on an older SHA, label it **Historical dashboard recovery failure** and distinguish it from the current revision. Do not suppress unresolved historical risk, but do not attribute it to the new commit without current evidence.


## Worked operator examples (display-only)

**Scenario A — API 503 while the last worker event says generation started:** show `Service temporarily unavailable` and separately show the historical generation-start observation with its timestamp. Do not replace the 503 with `Generation observed` or imply that a restart is safe.

**Scenario B — a stale worker claim disappears after a GET read:** show `Claim state unverified` with the last claim timestamp. Do not label the worker `released`, `free` or `ready for reassignment` from cleanup alone.

**Scenario C — all focused tests pass but dashboard recovery fails:** show `Dashboard recovery check failed` with failing run and exact head SHA. Show successes as individual checks, not as overall readiness. Do not provide a privileged recovery CTA based on the banner.

**Scenario D — newer prompt submitted after an older generation-start:** show `Prompt submitted` for the new assignment until same-assignment evidence arrives. The older event must not establish current generation.

**Scenario E — contradictory producers after a clock jump:** show `Conflicting observations`, with both source identities and timezones. Do not choose the highest lifecycle rank, most recent wall-clock timestamp, or healthiest state without corroboration.


## Accessibility and localization acceptance examples

The status label must remain meaningful when colors, icons and charts are unavailable. A screen reader should announce **status, affected scope, evidence age, source, and uncertainty** in that order; it must not announce a definitive operational conclusion based on an empty observation. Treat the following as illustrative copy rather than runtime formatting requirements:

- **English:** "Status unknown for Worker 2. Last observation 2026-10-09 06:00 UTC from worker heartbeat. Current activity unverified."
- **Dutch:** "Status onbekend voor Worker 2. Laatste waarneming 9 oktober 2026, 06:00 UTC, via worker-heartbeat. Huidige activiteit niet bevestigd."
- **Recovery failure:** "Dashboard recovery check failed for commit <exact SHA>; see run <run URL>. Worker state is unverified. No restart was performed."

Never encode the sole difference between `Status unknown`, `Generation observed` and `Service temporarily unavailable` using green/amber/red alone. Localization must preserve the distinction between *last observed* and *currently verified*, and must not translate unknown into stopped or successful.


## Integration handoff — blocked until owner review

This contract is **not** wired into production. The serialized control-plane owner (#580/PWQ-41 and #1089) must independently verify these conditions before proposing any runtime/UI integration:

1. Map each observable field to its authenticated producer, scope, assignment identity, and UTC timestamp. Reject synthetic or unattributed producer identity.
2. Confirm that missing, stale, conflicted or replayed observations always use the corresponding non-authorizing label. Do not infer authority from display state.
3. Preserve old observations as explicitly historical. A newer prompt cannot inherit an older generation-start indication.
4. Test degraded API/503 and failed dashboard recovery without dispatching a restart or attempting a privileged repair.
5. Verify accessible English/Dutch copy with the same uncertainty semantics and without color-only differentiation.
6. Confirm the implementation uses live evidence and exact-head CI, not merely these fixture-only documentation tests.
7. Record independent review, changed-file ownership census and the serialized integration gate before changing any production file.

**Release veto:** A focused contract test success is not a deployment gate. A failing dashboard recovery check, missing independent review, unresolved owner collision, or absent verified runtime evidence means **do not merge/deploy**. This is a reviewer checklist, not a delegated permission to mutate systems.

## Acceptance checklist (manual UI review; no production authority)

- [ ] Copy remains usable without colors, icons or tooltips; screen-reader text spells out unknown/observed distinction.
- [ ] No state becomes more positive when sources vanish, claim GET cleans up, or provenance is invalid.
- [ ] No CTA implies permission to restart, push, merge or deploy from passive observations.
- [ ] Timestamps include timezone, freshness and source; stale signals never silently become live.
- [ ] Copy references the exact PR SHA for CI statuses.
- [ ] Owners review independently before any integration; serialized writer and deploy gates remain unchanged.

**Authority:** `mutation_authorized=false`; `restart_authorized=false`; `merge_authorized=false`; `deploy_authorized=false`.

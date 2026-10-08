# Deploy-ops: conflicting candidate evidence — deny-only operator contract

Date: 2026-10-08. Status: **review-only; not an authorization mechanism**.
Scope: offline operator triage for multiple candidate SHAs or incompatible receipts. No workflow, service, runner, database, queue, deploy or recovery changes.

## Invariant

The candidate for a production deployment MUST be a single immutable lowercase 40-hex commit ID proven equal to the selected, freshly resolved canonical `refs/heads/main` at the point of authorization. A green status from another SHA, another run attempt, a PR merge ref, or an earlier snapshot does **not** transfer to the selected candidate. The serial production owners (#580/PWQ-41 and #1089) are not released by any evidence collected using this document.

## Stop conditions (each individually sufficient)

| Case | Offline evidence observation | Operator decision |
| --- | --- | --- |
| C01 | Candidate A in preflight, candidate B in CI receipt | DENY; capture both SHAs and their source URLs |
| C02 | Same candidate but contradictory main SHAs in independent receipts | DENY; refresh trusted main ref and restart all checks |
| C03 | Main ref advanced after the status fetch, before gate decision | DENY; invalidate earlier status and gate snapshot |
| C04 | Multiple queued/running deploy or recovery candidates, even if one is green | DENY until overlapping owners and live attempts are resolved |
| C05 | Same run ID but different attempt numbers are mixed | DENY; require matching run ID **and** attempt number |
| C06 | Green result only from a PR, fork, synthetic ref or non-main event | DENY; not main deploy evidence |
| C07 | Receipt omits SHA, event, attempt, trusted source, timestamp, or runner provenance | DENY; unknown is not safe |
| C08 | A fresh green receipt and stale red receipt disagree | DENY pending a new coherent set, never select the preferable outcome |
| C09 | Gate ownership state is missing, stale, or reports #580/PWQ-41 or #1089 still active | DENY regardless of otherwise green evidence |
| C10 | Dashboard looks healthy while independent reachability returns 503/timeout | DENY automated recovery; do not equate external error to VPS outage |
| C11 | Exact SHA and run match but the runner identity/labels are unverified | DENY; labels claimed by an artifact are not attestation |
| C12 | A request is manually overridden without recorded independent approval | DENY; operator override does not bypass immutable identity or serialization |

## Evidence capture (redacted; no tokens or raw process environment)

Record the current canonical main SHA, candidate SHA, exact run URL + run attempt, event and ref, workflow name, check conclusion and check timestamp, trusted runner identity evidence, competing PRs and PR-less branches, overlapping deployment/recovery attempts, and the named release-gate owner. Include the query/source timestamps and distinguish **observed**, **asserted by an untrusted artifact**, and **unavailable**. Capture only bounded error category and timestamp for network failures, not response bodies or secrets.

## Recheck and handoff sequence

1. Enumerate open PRs and PR-less branches (including work held by another worker); do not edit their owned paths.
2. Resolve `refs/heads/main` afresh from the trusted repository source; retain the immutable SHA and time of observation.
3. Associate every check with **one** candidate SHA, exact run ID, run attempt, event/ref, workflow and source. Never synthesize acceptance by mixing records.
4. Re-resolve main before decision; if it has moved or evidence conflicts, classify the entire set **DENY** and restart, rather than discarding inconvenient records.
5. Keep every production write, rollback, recovery dispatch and merge disabled while #580/PWQ-41 or #1089 retain serialized ownership. Obtain explicit independent release-gate approval separately; this document provides none.
6. Handoff a redacted contradiction matrix to the current owner for exact-head validation. **No action** on live infrastructure follows automatically.

## Acceptance for this documentation-only change

- Path ownership reviewed against all active PRs and PR-less branches; no existing production-owner files changed.
- Markdown/table review confirms all twelve conflict vectors deny and no vector silently authorizes writes.
- Exact-head review and repository CI required before merging this document.
- This is a deny-only human review aid; even a completed review does not establish production readiness.

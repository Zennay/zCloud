# zSentinel · Notion claim-window visibility (22 oktober 2026)

This is a **read-only operator capability** on the existing zSentinel integration-runtime PR #1563. It does not alter the zCloud queue, Notion, GitHub workflows, runner services, owner leases or release authority.

## Product behavior

The live Notion reader queries at most four source pages of up to 100 work items per page, split by Claimed, Running and Verifying. A work status does **not** prove a current lease. Every read item now carries a separate, bounded `claim_window_observation`:

| Value | Operator meaning |
| --- | --- |
| `missing_worker_or_lease_fields` | Worker, claim start or expiry absent |
| `date_only_not_a_lease` | At least one timestamp is only a calendar date |
| `invalid_lease_timestamp` | Clock cannot be parsed as timezone-aware timestamp |
| `invalid_lease_interval` | Nonpositive or implausibly long (>7 days) interval |
| `future_claim_start` | Claim start is after the read instant |
| `expired_claim_window` | Expiry at or before the read instant |
| `time_window_present_not_owner_verified` | Metadata describes a bounded current clock window, **not a verified owner** |

The existing `claim_metadata_status` field is retained for compatibility but is only a field-presence indicator. It must not be interpreted as evidence of an active lease.

The live reader emits aggregate `claim_window_observation_counts` for **all** observed rows, not just its 30 visible examples. The default offline cockpit, private interactive incident desk, and standalone owner handoff render this source separately from status counts and explicitly state that even a plausible time window cannot authorize work claims, retries, release, dispatch or owner takeover. Source adapters are constrained by the same bounded live-only categories, counts and `claim_window_verified=false`. An unauthenticated, manually saved Notion connector snapshot remains historical status-only and **cannot** assert these live claim-window facts.

## Boundaries and operator response

- Expired and missing Notion metadata require **owner recheck**. They are not permission to free, replace or restart a worker.
- If an active status has no timestamp, the lease remains **UNKNOWN**. A missing lease timestamp cannot be treated as a free slot.
- A `Verifying` status is unfinished work pending owner verification; it is not proof of an active running worker or ownership of files.
- A current-looking Notion clock is **unverified metadata** and should be reconciled against the authoritative queue and the existing active claims before any manual change.
- Partial Notion paging is **not** a complete roster: every count is a lower bound, and absent projects remain UNKNOWN.
- These observations are source-only. Existing exact-head CI, independent reviews, branch protection and deployment authorization remain mandatory.

Implementation: `scripts/zsentinel_notion_claim_observer.py`, `scripts/zsentinel_recovery_view.py`, `scripts/zsentinel_incident_desk.py`, `scripts/zsentinel_runtime_handoff.py`. No QA suites were added or run.

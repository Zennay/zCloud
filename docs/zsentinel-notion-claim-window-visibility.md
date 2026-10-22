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


## Per-project owner handoff

The live adapter now also computes `active_project_claim_window_counts` per canonical project label. Each project's seven-class histogram sums to its observed unfinished-work count; project distributions must reconcile exactly with the global histogram. The owner handoff (private HTML and Markdown) highlights the number of missing/invalid/expired/future claim clocks **per project**. The default offline cockpit prioritizes up to twelve projects by observed clock uncertainty. This grouping comes from **all read records**, not the 30 displayed examples; both project paging and the operator's project shortlist remain bounded. Unknown or unregistered project labels are never mapped to an assumed owner.

Rows without live claim-clock provenance are refused by the offline card renderer. These are read-only owner recheck hints: **no file ownership, queue takeover, worker restart, dispatch, merge or deployment permissions result from any of these categories.**

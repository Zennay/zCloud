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

## Owner-first recovery workboard

A multi-page Notion read now uses **one pinned UTC observation instant** for all its lease-window classifications and its published timestamp. Identical lease windows therefore cannot receive inconsistent classifications solely because they appeared on different sequential pages.

The existing `zsentinel_runtime_handoff.py` now turns the **per-project observed anomaly histogram** into bounded non-executable owner actions, while preserving the priority of exact P0 incidents and existing product PRs:

- Prioritize projects by the number of observed missing/invalid/expired/future clock fields (not merely by a large historical `Verifying` backlog).
- An observed `Claimed/Running` work-status signal makes the owner's clock recheck **P1 advisory**; exclusively `Verifying` work remains **P2 advisory**. Neither classification claims a running worker, active file owner, verified lease, or failed queue retry.
- Offer at most six project-scoped owner-review cards, with bounded per-project counts and the Notion observation instant. Revalidate each card against the parent private source when rendered to offline HTML or Markdown.
- **Count only cards actually visible after the shared 24-card P0/PR-priority limit.** If older incidents, PRs or other blockers take those slots, the source explicitly says cards are truncated instead of incorrectly claiming they were displayed.
- Historical/manual Notion connector snapshots and stale/absent live sources cannot create *current* owner action cards. Missing source means **UNKNOWN**, never zero or safe-to-dispatch.
- Use the existing issue/PR and queue owner paths for authorized changes. Never release Notion or SQLite claims, restart a runner, dispatch work, edit a billing gate or merge/deploy based on these advisory clock indicators.

The new project-owner workboard is accessible through the pre-existing `--read-notion-claims` live refresh and existing optional private `--owner-handoff-output` / incident-desk output, without any new API budget, daemon, scheduler, test suite, DB migration, or write permission.

**Release:** This documentation and source are on the existing PR #1563 until exact-head mandatory CI, independent review, branch protection and authorization permit merging; a branch commit is not a main-branch release.

## Same-row urgency, not project-wide inference

The latest owner workboard revision also preserves whether an **individual
Notion row** with an anomalous claim clock is itself in `Claimed` or
`Running`. It exposes bounded
`active_project_in_progress_claim_clock_rechecks` alongside the original
per-project histogram. Every count is checked against the corresponding
project's observed non-`Verifying` status count and anomalous clock total.

This distinction matters: one valid-looking `Running` item elsewhere in a
project must **not** escalate a historical `Verifying` clock anomaly to a P1
active-worker incident. Owner actions are P1 only when at least one anomalous
clock belongs to a `Claimed`/`Running` status row; otherwise P2. This is
**advisory triage**, never proof of current worker activity, claim ownership,
failed retry or lease expiry in the authoritative SQLite queue.

The offline cockpit and private owner report show the same-row count separately
from all uncertain clock fields. The report's provenance validator refuses
contradictory status/clock totals, source timestamps, owner permissions or
priority labels rather than silently passing them to the operator.

The private incident desk now also renders the same-row `Claimed/Running`
anomaly count, sorted top project groups and a visible **partial-coverage**
disclaimer. It independently reconciles the per-project clock classifications
with the source's portfolio aggregates and refuses contradictory statuses.
It remains a private, non-executable owner workspace.


## Project-relation coverage is part of the source contract (23 October 2026)

A live claim row whose `Project` relation is missing, ambiguous, or points outside the canonical project registry is still shown to the operator, but it is **not** complete ownership evidence. The observer emits `project_relation_status=resolved|unresolved`, `project_relation_coverage=complete|partial`, and `unresolved_project_relation_count`. Each bounded Claimed/Running/Verifying source window carries its own `unresolved_project_relations` count.

The recovery cockpit, portfolio overview, and owner handoff reconcile the per-status counts against the global count. A mismatch, absent per-status receipt on a live-labelled source, or a saved snapshot claiming live project-relation coverage is rejected. An unresolved relation produces an explicit **PROJECTRELATIES ONVOLLEDIG** warning and no free-work conclusion. This is a source-integrity signal only: it never proves a live lease, authorizes a claim transfer, retry, dispatch, service restart, or deployment.


## Canonical-project owner action cards (23 October 2026)

The operator handoff now promotes a **positive, recent** unresolved Project-relation
observation into **one bounded registry-owner action card**, in addition to the
read-only warning. This closes the gap where an unidentified active task was
visible in source statistics but never entered the owner follow-up queue.

- If any unresolved relation belongs to a `Claimed` or `Running` status
  window, the advisory owner action is **P1**; if every unresolved relation
  belongs only to `Verifying`, it is **P2**. These are Notion *status*
  observations, not verified leases or current worker assignments.
- The card is assigned to the `cloud` **registry coordinator for review only**;
  it does **not** assign the original task to zCloud or guess the task's
  canonical owner. The authorized project/queue owner must reconcile the
  original Notion relation and current claim before taking action.
- The card is generated only from a live, ≤10-minute bounded Notion source.
  The total unresolved count and the Claimed/Running subset must reconcile
  with all three exact source status windows. Saved snapshots, stale feeds,
  missing status windows or contradictory counts cannot create a live card.
- The owner action competes in the existing **24-card P0-first workbench**.
  The HTML/Markdown overview distinguishes *card visible* from *card
  omitted because higher-priority work filled the list*. Both states retain
  a prominent source-bound operator instruction, not a false zero.
- The same per-card source validation is reused by HTML and Markdown output;
  neither output can promote a changed priority, project, source timestamp
  or ownership claim. The JSON handoff contains the corresponding bounded
  summary without any claim/dispatch/retry permissions.
- The default recovery cockpit and full-portfolio overview also use the
  source-reconciled **Claimed/Running subset** to display the same P1/P2
  urgency, without guessing the missing task's original project or owner.
  The portfolio source JSON exports that subset separately; saved snapshots
  never supply it.

This is an operator follow-up, **not** automated reassignment, claim release,
CI retry, registry write, worker recovery, or deployment. All existing GitHub
exact-HEAD CI, independent review, protection, and release gates still apply.

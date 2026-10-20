# zSentinel · four-lane recovery admission

This is source guidance for the `portfolio-recovery` profile in
`lane_generator.py`. The zSentinel role remains cross-project recovery within
zCloud, not a separate product or permission boundary.

## What is implemented

| Lane | Work chosen from the existing backlog |
| --- | --- |
| `release-integration` | Finish a previously opened product PR on main, when existing exact-head checks, reviews and authorization permit. |
| `product-remediation` | Resolve unowned product-impact issues and real bugs; do not invent QA work. |
| `worker-recovery` | Investigate genuine queue, claim, browser-worker or scheduler blockage. |
| `ci-deploy-recovery` | Investigate CI, runner and deployment problems without bypassing release authority. |

The classifier treats generic ticket words (`issue`, `fix`, `bug`,
`product`) as weak signals in this profile. Specific terms such as
`runner`, `queue` and `merge` determine the lane when present.
This is routing, not proof of ownership or permission to take a task.

## Reservations and collision visibility

- All in-flight queue entries (`claimed`, `running`, `verifying`) reserve
  their own scope, even if multiple entries were accidentally assigned to
  the same lane. Only one owner is *displayed* per lane; hidden active work
  must never cease to occupy its files or capabilities.
- `active_backlog_count` reports how many in-flight entries were observed
  for that lane. `parallel_active_warning=true` means there are two or more;
  it is a **reconciliation signal**, not permission to cancel, reassign,
  duplicate, merge, resume or steal their work.
- Serialized `conflict_scope.capabilities` and `conflict_scope.files` may
  be a string or an array. A single filename is always one filename, not
  an iterable of characters. Existing path/capability overlap checks still
  determine whether a *queued* candidate may use the lane.
- Missing local claims, unseen Notion claims, absent external leases and an
  idle lane are **not evidence of free global ownership**. Independently
  recheck current claims before writing to any PR, queue or VPS runtime.

## Runtime boundaries

The project contract declares a *maximum* of four AI workers, not a
guarantee of four simultaneously executing browser workers. Existing
resource-pool admission, physical RAM/swap/CPU pressure, claim leases and
runner availability can legitimately reduce actual concurrency. Four
configured lanes do not imply four resumed jobs.

No source-only result authorizes production mutations or GitHub merges:
a PR is delivered on `main` only when GitHub confirms `merged=true`
for the exact verified head and the new default-branch SHA is observed.
An approved deploy is a distinct event; a live, recovered worker must be
observed separately. Do not start QA suites, force CI, restart services,
modify broker/security/research state, or reassign protected owner claims
to make these labels turn green.

## Private four-lane operator desk

The concrete read-only product is `scripts/zsentinel_four_lane_desk.py`.
Use the zCloud SQLite source and the **current branch's matching**
`lane_generator.py` when working from a checkout of this PR:

```sh
python3 scripts/zsentinel_four_lane_desk.py --db history.db
python3 scripts/zsentinel_four_lane_desk.py --db history.db --format json
python3 scripts/zsentinel_four_lane_desk.py --db history.db --format html --output /PRIVATE-OWNER-DIRECTORY/new-report.html
```

JSON/text output intentionally contains no claim keys, owner identities,
queue identifiers, raw titles, file paths or capabilities. The report is an
offline, single-file, Dutch four-card interface with restrictive CSP and no
external assets. HTML export refuses existing files, symlinks and non-private
or other-user parent directories; an output is created 0600 only on explicit
request. All other modes perform **no file writes**.

The SQLite connection is `mode=ro` with `PRAGMA query_only=ON` and a
single transactional snapshot. Both queue entries and local claim rows are
bounded independently to a configurable maximum of 500. Above that bound,
coverage becomes partial: **never** interpret missing rows as idle workers.
Unparseable lease records remain ambiguous. The desk records observed
queue `claimed/running/verifying` expiry as either expired or unknown,
but does **not** reclaim or alter either category. It keeps queue occupancy
distinct from the active `task_claims` table. Absence of local rows says
nothing about Notion and other external owners.

Only up to 64 in-flight queue records receive an explicit pairwise
cross-lane capability/file overlap inspection in the private desk. More than
64 produces an *incomplete* comparison rather than a false zero. The
underlying lane generator continues to reserve **every** active task scope
when assessing new candidates, independently from this optional view.
Existing in-flight collisions trigger an advisory warning, never a forced
reallocation.

### Actual direct VPS observation (20 October)

Against a read-only current `history.db` snapshot, source pinned to
`bed05d016965e1e539afad5159384b0aba6ee517`, the desk observed
one claimed PR-integration item, two claimed worker-recovery items and one
queued CI/deploy item; product remediation was idle in this **local** view.
No zSentinel rows existed in the observed local `task_claims` table.
The view correctly warned about multiple worker-recovery queue entries
and about claimed work without locally observed leases. These findings
are **not** a release/worker recovery claim, global source completeness,
permission to take a Notion claim, or evidence that an external owner
is absent. Source syntax and this bounded, real-data functional invocation
were inspected without starting any QA suite.

### Delivery boundaries

Current code in a GitHub PR is not `main`. Exact-head mandatory CI,
independent reviews, branch protection and release authority remain
required. An operator must separately verify GitHub `merged=true`,
the newly observed default-branch SHA and authorized live activation
before describing this feature as delivered or live.

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

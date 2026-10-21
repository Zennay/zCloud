# zSentinel Activity cockpit — operator guide (18 October 2026)

This is a product-first, explicit-action operator view on the existing **Activity** route. All new features are **read-only** and do not mutate portfolio-queue records, worker leases, priorities, runtime settings or deployments.

## Product delivery queue

Choose **Read admission** to request the existing authenticated `GET /api/portfolio-queue` endpoint. The new `delivery_policy` payload comes from unified backend PR #1549 and reports:

- `queued_product_candidates`: queued, eligible product work that passed the initial type classifier (not an authorized claim).
- `queued_qa_only_held`: queued-only QA/review packages skipped for fresh admission, not cancelled or marked Done.
- Up to eight candidate IDs, project IDs and priorities, with explicit **Not claimed** indicators.

The panel is intentionally not loaded in the background. An unavailable API or 403 produces an error instead of a false empty queue. A selected project narrows the visible candidates, while summary counts still describe the global queue; read the label before interpreting totals.

The type classifier never replaces existing scope/owner arbitration, hard resource caps, human approval, memory admission, or GitHub CI gates. `claimed`, `running`, `verifying` continue to reserve their respective ownership scopes. A queue receipt does not prove that an independent runner can execute the work.

## zSentinel issue-first delivery board

The **zSentinel · product delivery board** is displayed first on Activity, above the existing independent GitHub, issue and worker panels. Choose one canonical project and press **Read product sources** to explicitly read its public GitHub P0/P1 issue sample, public PR/CI/main observations and the authenticated queue admission preview. Each source is bounded and independent; if one fails, the other cards remain useful and the failing source is not interpreted as empty.

The board sorts observed open P0 issues ahead of P1 issues, shows relevant existing **open PR mentions** (as mentions or closing text only, not ownership), and separately lists provisional product queue candidates. No claim is inferred by matching GitHub issue IDs with queued project names. Open each original GitHub issue/PR through canonical links to verify context and current status before making a decision.

Manual readings for up to **eight** canonical projects coexist in the browser tab, with at most 150 issue rows and 200 GitHub events across projects. Reading a project again replaces only its own previous sample; snapshots are not cached on disk, sent to third parties other than the explicitly requested GitHub GET, or silently refreshed. The global counts in the queue panel remain global even with a specific project filter.

A missing source, a known partial window, an older observation, a linked draft or green CI always remains unverified. Source observations never authorize taking over a claimed lane or bypassing CI, review, release, human or zSSH capability gates.

### Export a privacy-minimized handoff

After manually reading relevant sources, choose **Export handoff .md** in the zSentinel board. This creates a local UTF-8 Markdown file with up to 60 high-priority GitHub issue references, up to three existing PR references per issue, at most 30 bounded public GitHub observations, and a high-level product queue summary. The export **does not include** queue item IDs, human claim IDs, event/prompt text, tokens, credentials, cookies or raw API payloads. All source timestamps and the evidence-only status are included.

The export action is local to the browser and requires a deliberate click; it never uploads, opens a new worker, contacts GitHub, or modifies runtime state. It is suitable as a *handoff note*, not an authority receipt. The next worker must still read current Notion ownership, exact PR checks and release gates before editing or merging.

## Product queue: QA-only versus needs-owner-review

The admission read-model differentiates **QA-only queued work held from fresh claims** from **malformed/ambiguous product work requiring an owner to correct the original queue entry**. In Activity, **Read admission** shows separate global counters. The **Product delivery board** puts bounded review-needed project/priority/reason entries above provisional product candidates. These rows are not automatically unlocked, claimed, dropped or classified as a completed fix.

The locally downloaded handoff contains no raw queue identifiers for these rows; it includes only their project, priority, constant reason (`invalid_metadata`, `invalid_title`, `oversized_criteria`) and total count. A real owner must correct source queue metadata through the existing authenticated control-plane path, after independent claim/lease/source review. This UI cannot grant itself permission to perform that correction. QA-only work remains excluded from *new* admission under the 17 October policy.

## All projects: bounded issue-first portfolio discovery

Select **All projects** on Activity and press **Read next 4 projects** to
inspect four canonical Zennay GitHub repositories in one explicit batch.
Each project uses the already available, read-only browser GitHub importer:
a bounded oldest/recent open-issue sample plus current open-PR references,
and a separate bounded PR/CI/main-commit sample. P0/P1 non-QA issues are
shown first in the existing product board, with independent per-project
coverage and error indicators. A completed HTTP read is not proof that a
feature was merged, deployed, or assigned to an unclaimed worker.

The button never schedules automatic background requests, cancels CI,
dispatches workflows or mutates VPS/queue/claim data. Work is sequential,
bounded to four repositories per operator click and preserves the existing
per-project two-minute public GitHub cooldown. Anonymous provider requests
can be rate-limited: partial/error states are labelled **unknown**, not
zero work, and the button does not bypass the existing provider cap.
Snapshots are limited to eight project sources, 200 PR/CI entries and the
existing bounded issue list in browser memory; later batches can evict
older project evidence. The counter measures repositories **visited**,
not globally synchronized. Select one project to use the existing trusted
VPS read-only source, when authorized by the normal access boundary.

The dashboard's `app.js` cache URL retains the existing
`v=20261016-auto` compatibility prefix and uses its current source SHA as
a separate query parameter. The existing mobile CSS contract requires the
literal `/enhancements.css?v=r5` URL; it is deliberately preserved.
The backend static-file `reply()` already sends `Cache-Control: no-store`
for CSS/JS, so this is not a claim that new CSS query tokens are required.
Issue [#1305](https://github.com/Zennay/zCloud/issues/1305) remains open
until the complete versioning contract and current live served bytes can be
reviewed by its owner.

## Operator JSON import: preserving useful evidence

Issue-inbox and GitHub Activity offline `.json` imports now follow the same
non-destructive failure contract as the branch-import panel. Files larger than
1 MiB, malformed JSON and receipts failing the pre-existing strict schema
validation are rejected **without clearing any previously accepted sample**.
The panel displays the import error and explains that the older evidence
remains; only the explicit **Clear** button discards it. A validated new import replaces **only the projects explicitly covered
by that file**, preserving already observed other projects (up to the existing
eight-project source budget). A new file with an older source observation
for an already covered project is rejected to avoid silently rolling back
fresh evidence; use **Clear** to intentionally start a different historical
observation. GitHub events retain the existing fair, 200-event limit and issue
rows the existing 150-row bound. No raw provider payload is persisted.

Source-only safeguards protect operator evidence during provider outages and
when manually switching files; none grants claim ownership, merges, deployment
or a verified runner/service recovery.

## PR lifecycle provenance from bounded GitHub samples

A manually requested GitHub `pulls?state=all` source now retains separate,
immutable provider dates for **created**, **updated**, **merged** and
**closed without merge** milestones (where the current record supplies valid
timestamps). Draft status is displayed as draft. An update at/after merge or
closure is not misrepresented as work completed before that transition.
Each event is a bounded read-only provider observation, and the 200-item
in-memory selection still gives every sampled project a share before
timestamp sorting. Event IDs are derived from canonical project/type/PR link
and the source remains explicitly partial (20 recent updated PRs per selected
repository). Nothing infers that a GitHub merge has been deployed or that
an open PR represents ownership of a Notion claim.

## GitHub outage: retain last successful P0/P1 and PR/CI evidence

Manual public GitHub reads no longer delete a project's previously observed
issues or PR/CI events when GitHub returns 403/429, times out, or only some
bounded endpoints succeed. Each project retains its last source observation
timestamp and a separate last-attempt timestamp. Partial reads may contribute
new data but must not establish that an old issue closed or that a previously
observed referenced PR vanished.

Activity prominently labels **STALE retained evidence** by project, the issue
and PR/CI panels state their historical source timestamps, and the existing
Markdown handoff includes this source status and last attempt. A **complete**
new bounded provider read replaces the project's older bounded sample; a
partial failure never silently turns previous work into an empty project.
No source result becomes a verified claim, merge or deployment. Reports are
still limited to browser-memory retention and source counts as before.

## Fair GitHub history and read-only VPS pressure

When Activity reads source PRs/CI/commits for multiple repositories, the
in-memory 200-event window now chooses entries in round-robin project order
before sorting them by original event timestamp for display. A noisy project
can no longer consume all 200 entries while a sampled quiet repository has
recent events. Each project still has its own partial/unknown coverage;
neither this fairness nor a successful `/api/status` proves globally complete
GitHub synchronization.

Activity also displays **zSentinel · VPS capacity signals** from the existing
read-only `/api/status` host sample. The backend host read-model in
[PR #1549](https://github.com/Zennay/zCloud/pull/1549) now includes
`swap: {total, used, free}` in bytes, in addition to existing host load
and disk. The panel reports (a) load more than twice the declared vCPU count,
(b) at least 85% swap use or less than 512 MiB free swap, and (c) at least
90% disk usage. These are operator attention signals, not an algorithm to
kill tabs or confirm that a runner is healthy. When the host sample is older
than 120 seconds, missing, or does not include a coherent swap observation,
the panel labels **UNKNOWN** rather than falsely reporting healthy.
It never asks for VPS credentials, writes to SQLite, restarts systemd or
clears swap. Use the authorized zSSH route and existing owner approval before
any operational remediation.

The two branches are separately source-owned and neither is production
without exact-head CI, independent review, protected-main and serialized
release. Source-only UI updates cannot prove the current VPS status.

## PR-less branch overlap — manual project work handoff

The Activity screen now has an optional **Unmerged branches / Import branch .json** panel. This pairs with [PR #1554](https://github.com/Zennay/zCloud/pull/1554), the standalone read-only GitHub branch inventory for repository `Zennay/zCloud`. The reporter can scan a 12-branch namespace slice and print a bounded JSON receipt; import the JSON file manually on Activity. The UI does not fetch or execute repository content, call GitHub, persist tokens or modify a project queue.

You may select up to **eight** JSON files at once (or append additional slices later),
for at most **96** distinct branch records. A combined session requires the
same repository, source main SHA, matching branch namespace and matching
inventory counts; independent observation times must be within ten minutes.
Duplicate/overlapping slice indices or branch refs are rejected, and an invalid
file never destroys previously accepted browser evidence. Because multiple
observations are not a single atomic snapshot, their union is **always**
reported as PARTIAL even when slice counts happen to cover every index.
The view prioritizes conflicts and displays at most 40 branches; the Markdown
export likewise bounds included branch references at 40 with an explicit
omission count. You can export from a branch-only import without first loading
unrelated GitHub or queue data.

The browser requires the exact `zsentinel-recent-prless-ownership-v1` schema, `repository=Zennay/zCloud`, a verified-shape main SHA, source timestamps, explicit no-authorization flags, bounded branch/path arrays, matching conflict evidence and limited JSON size. Imported details stay **in tab memory only** and render as a source timestamp (with >6-hour stale warning), partial/complete-at-observation coverage, branch ref, ahead count, changed-path count and up to 12 conflicting paths per branch. Other projects never inherit zCloud's owner evidence. Use **Clear** to discard.

The existing **Export handoff .md** can include only the bounded, sanitized branch ref/time/classification/overlap count from a manual import, not raw provider payloads, branch commit messages, GitHub credentials or privately scoped queue IDs. The handoff explicitly does not establish an active claim or authorize source edits, merges, worker actions or deployment. An absent imported report means **unknown**, not no conflicts. A prefix-selected scan remains globally partial even when all matching branches were scanned.

Both PR #1554 (report producer) and the Activity UI [PR #1550](https://github.com/Zennay/zCloud/pull/1550) must be independently accepted and deployed through existing exact-head CI, review and serialized release. This UI is **source code only** until those gates clear.

## Older local history

Choose **Read history** to request the first 40 items from `GET /api/activity-page`. Use **Load older** to follow the source-provided per-project keyset cursor; the list is capped at 400 distinct events in the browser. Switching Activity's project filter begins a separate scoped history. Results are not silently loaded, written to storage, or used to dispatch work.

The API reads canonical SQLite-local event identifiers/timestamps/kinds through a consistent read transaction and fairly interleaves project streams to prevent a chatty project from hiding quieter work. Only names/metadata necessary for local history are shown. A missing backend, invalid coverage, unexpected project, or failed request produces a visible error, not proof of zero activity.

**Coverage is partial by design.** The local feed does not synchronize GitHub PR, branch, merge or CI activity. The separate public GitHub button is an independent manual source. Never equate a branch update or an open PR with merged production delivery.

## Composer failure: actionable worker progress

When the separate actual v2 browser worker [PR #1552](https://github.com/Zennay/zCloud/pull/1552) reports a persistent missing composer, the backend read-only projection [PR #1549](https://github.com/Zennay/zCloud/pull/1549) retains only fixed, privacy-safe literal reason codes. Open Activity, select the affected project and worker slot, then choose **Read progress**. The panel shows the latest bounded browser-observed composer problem or restoration and explicitly says when **manual intervention in the existing assigned tab** is required. No underlying URL, free-form reason, prompt or token is exposed. A prior `composer-restored` observation is not proof the worker has an active queue claim or is generating.

There is **no reset button** here: only the owned v2 tab itself may request the pre-first-send, blank-chat, one-shot SPA repair. Do not use this UI to force navigation, dismiss a current reply or steal an existing assignment. The three PRs (#1549, #1550 and #1552) remain separate review/release dependencies.

## Directly from the VPS: on-demand GitHub and P0/P1 source buttons

In Activity, select exactly one project. **Read VPS GitHub** requests the new trusted `GET /api/github-activity-live?project=<id>` route. It returns an independently bound, read-only canonical repository observation (PRs, CI runs and main commits), with a maximum five-minute in-process cache. **Read VPS issues** requests `GET /api/product-issues-live?project=<id>`, which uses the original bounded, P0/P1-first issue source and existing open PR references. Both buttons show partial coverage and may report the separate `refreshing`, `unavailable` or `capacity_wait` outcomes. Operators can retry manually; no scheduled browser poll or automatic workflow dispatch is added.

The backend accepts only projects from canonical `projects.json`, and requires the existing trusted management network for these read-only API sources. A remote browser without that permission receives 403; this is **not** a cue to relax authentication. **Read public GitHub** and manual JSON imports remain usable separate evidence routes, while private repositories continue to require separately authorized access and are never silently marked empty.

A matching GitHub event ID is not a local SQLite event, a CI success is not a merge, a reported GitHub merge target is not proof a commit reached main, and a main commit does not prove the production VPS has deployed it. All receipts explicitly carry `feed_complete=false` and deny worker recovery/merge/deploy authority. Two concurrent provider collections maximum; source reads are bounded and only started by a button click. Check actual Notion claims and immutable CI/review gates before implementation or release.

## zSentinel queue retry incidents — operator import and movement

Issue [#677](https://github.com/Zennay/zCloud/issues/677) can now be investigated in the **Activity → zSentinel queue retry incidents** panel, in addition to the existing issue-first workboard. This is an entirely manual, browser-local presentation of the independent source-only [retry console PR #1555](https://github.com/Zennay/zCloud/pull/1555).

1. An authorized operator uses `scripts/zsentinel_retry_incident_console.py --database <existing-local-queue-db> --format json --output retry-after.json` in an allowed environment. This opens an existing DB as SQLite read-only, without claims/reconciliation/writes, and produces a bounded, privacy-safe report.
2. In Activity select a project or All projects. Choose the **Queue snapshot JSON** file. The UI validates the exact `zsentinel-retry-incident-v1` schema, source timestamp, scoped canonical projects, explicit non-authority flags, bounded counts and opaque reference identities before using the content. Invalid sources do not replace the previous accepted evidence.
3. To inspect movement instead of a single sample, compare two same-scope snapshots offline with `scripts/zsentinel_retry_trend.py --previous retry-before.json --current retry-after.json --format json --output retry-trend.json`. Import that into the separate **Optional trend JSON** control. The UI strictly checks `zsentinel-retry-trend-v1`, chronological observation times, project identity, total deltas, and explicit non-authorizing state.
4. Inspect high-attempt rows ranked P0/P1 first, per-project increases/decreases and sample freshness. Use **Export retry handoff .md** to produce a small file with bounded opaque references and counts, **not** raw queue IDs, titles, prompts or secrets. Use **Clear imported evidence** before handing the screen to another operator.

A current project filter never converts global counts into a false project-specific total: when local groups exist, they are used for the selected project's count; a single-project receipt for another selection is identified as a scope mismatch. The UI is in-memory only, never writes to localStorage, triggers an API or schedules a poll, and takes no existing owner lease. A stale snapshot is labelled stale; absence of samples is not a reliable assertion that no incident exists. `attempts` tracks claims **not failed work**; do not automatically requeue high counts. Verify actual Notion claims, current GitHub CI/reviews, runner headroom and serialized release separately before acting.

Source branch: [#1550](https://github.com/Zennay/zCloud/pull/1550) modifies only `public/app.js`, `public/index.html` exact content pin and this guide for this UI. The separate producer/trend CLI in [#1555](https://github.com/Zennay/zCloud/pull/1555) must be independently accepted before the full workflow is considered delivered in production. Both branches remain unmerged/undeployed unless the existing required exact-head CI, independent review, protected-main and release gates pass; source UI is not a runtime adoption claim.

## Trusted one-click VPS queue retry source (backend #1549)

A new **Read VPS queue** control, beside the file import, calls
`GET /api/queue-retry-incidents?project=<selected-project|all>` **only when
clicked**. It uses the existing management peer authorization; 403 means the
source is unavailable to the browser and must not be bypassed. The backend
returns the same restricted `zsentinel-retry-incident-v1` source format as the
standalone #1555 CLI. No credentials, arbitrary database path, URL or retry
threshold can be supplied from the browser.

For a first click the panel displays a bounded prioritised queue view.
On a **second** explicit click for the same canonical scope/threshold it
calculates an in-memory before/after comparison without a background timer or
HTTP stream. Changing the project filter does not join unrelated snapshots.
The browser never uses a successful HTTP response as worker ownership,
retry/deploy authorization or a lease-free guarantee. HTTP failure, 403 and
untrusted/malformed response preserve the previous observation and show a
distinct unavailable message. Manual JSON import remains independent.

Backend: [PR #1549](https://github.com/Zennay/zCloud/pull/1549), source
`scripts/zcloud_queue_retry_live.py`, built as an explicit read-only SQLite
snapshot and guarded GET route. UI: [PR #1550](https://github.com/Zennay/zCloud/pull/1550).
Both require their own existing exact-head CI, independent review and serialized
production release. This feature does not implement #677 writer-side finite
retry or backoff policy.

## Unified Activity timeline — issue #1518 (19 October)

The Activity screen now includes **Project timeline · VPS + GitHub** ahead of the existing independent source panels. It combines the already-observed local `/api/activity` events and the opt-in, validated GitHub PR/CI/main-commit/branch-head snapshots into one timestamped list. Branch-head timestamps represent when the browser observed the branch, not the branch's last commit or creation time; changed HEADs are compared only across complete page-1 snapshots. Private branches, branch ownership, merge and deployment remain unknown. The existing public/VPS GitHub read buttons and the JSON importer remain the only GitHub refresh mechanisms. Changing the source filter (All, Local VPS, GitHub) does **not** schedule or trigger provider reads.

The same timeline header now offers **Read project sources** for the selected project, or **Read next 4 project sources** in All projects. The public GitHub activity reader now observes PRs, Actions, main commits and up to 100 branch heads per page, with a browser-local eight-batch rolling-hour budget and the existing per-project cooldown. Both controls reuse operator-triggered, bounded GitHub and queue readers; no new timer, automatic polling, or privileged dispatch is added. The portfolio reader is intentionally sequential and holds only its existing eight recent project snapshots. The combined list retains explicit project, source and source event type for every row, plus bounded privacy-safe GitHub links. The **Project source coverage** disclosure shows every visible project's most recently observed local and GitHub event, per-project GitHub observation time, and explicit unavailable/stale/not-observed status. Clicking a project name narrows the unified timeline. A project absent from a sampled window is shown as **unknown**, never as completed or idle. P0/P1 inbox and worker/queue panels remain independent: observing an issue or PR never acquires ownership. GitHub source availability, missing per-project coverage and source staleness are shown separately from local host connectivity. A read-only `Export timeline .md` creates an operator handoff without queue IDs, tokens, prompts or credentials.

The list reserves round-robin opportunities for quieter canonical projects before displaying the retained events by source timestamp (max 140 rows); an active project's volume cannot erase all others. Only an **exact project + full 40-character SHA** match between a local `project:git:SHA` event and a validated GitHub `main_commit:SHA` event merges them into one combined observation. This is an evidence join, not a deployment claim; unrelated commits, PR updates, CI runs, and events on different projects remain distinct. Each timeline row is fully escaped, GitHub hrefs are restricted to canonical project repo URLs and known event paths.

This source change and its exact `app.js` cache pin live on [PR #1550](https://github.com/Zennay/zCloud/pull/1550) until mandatory exact-head CI, independent reviewer, protected-main and serialized release authorization complete. It is **not** evidence that the live VPS UI is already updated. No new QA or regression suite was created or run manually.


## Complete 13-project source retention and fair P0/P1 operator inbox (19 October)

The portfolio currently has **13 canonical projects**, but the earlier browser Activity importer and GitHub/issue source refresh retained only the **last eight** observed project coverages. Reading the next batch silently discarded evidence for previously inspected projects, even when there was room in the existing bounded UI. That was a real operator handoff defect, not proof of inactivity.

The same existing Activity integration branch now retains **up to 16 canonical project source records** across manual public GitHub reads, trusted VPS reads and explicit operator JSON imports. The global GitHub event history remains capped at **200 distinct events**, fairly allocated across all retained projects instead of just the first eight. Per-import schema maximums (eight requested projects, 200 events and existing exact-source validations) are **unchanged**; the new limit only applies to the accumulated in-memory observation store. No tokens, browser localStorage, hidden polling or provider background sync were added. Older records are not treated as live current status.

Issue discovery remains bounded to **150 unique project/issue references**. It now fairly interleaves source-covered projects **within each priority class**, processing P0 before P1 before lower priorities, instead of letting one high-volume repository starve the rest of the portfolio. Current source observations replace identical previously observed issue IDs; missing/corrupt/inaccessible sources still show partial or unknown rather than zero. This does not infer claim authority or trigger write/repair/merge/deploy actions.

Use the existing **Read next 4 project sources** button until all 13 are covered in the bounded browser view. The progress counter describes repositories **attempted**, not necessarily successfully read; separate GitHub activity and issue source readability counts expose incomplete, inaccessible and private feeds. The last completion message distinguishes the number of projects with both bounded source observations from source freshness; it must not claim full synchronization. New canonical projects are accommodated up to the fixed 16-source cap; larger future portfolios require an explicit review of memory and provider quotas. The earlier note about retaining only eight recent projects has been corrected in the UI.

Release: [PR #1550](https://github.com/Zennay/zCloud/pull/1550), source-only until current exact-head mandatory CI, independent review, protected-main merge and serialized VPS promotion. No manual QA or regression suite was authored or executed for this change.

### Imported PR-less branches in unified timeline

When the operator imports a validated `zsentinel-recent-prless-ownership-v1` slice for canonical `Zennay/zCloud`, the unified timeline now presents its **PR-less branch observations** beside local VPS and GitHub PR/CI/main-commit records. The visible timestamp is the branch's validated `last_commit_at`; the importer observation time is shown separately as freshness evidence. Each branch is clearly labelled **offline source, unverified owner, not main and not deployed**. Branch references are escaped plain text; no branch URL, token, private file path, raw prompts or authority is exported.

The existing All / Local VPS / GitHub source filters remain simple: branch observations appear in GitHub because they originate from an operator-imported GitHub inventory, not because the UI polled GitHub. The provenance line distinguishes private local inventory from direct PR/CI reads. The extra events are bounded to 96 imported branch rows and the existing 140-event combined timeline cap; an empty/nonmatching zCloud scope is unknown, not a cleared ownership finding. This is a read-only improvement to [issue #1518](https://github.com/Zennay/zCloud/issues/1518) with source inventory [PR #1554](https://github.com/Zennay/zCloud/pull/1554) as a separately held dependency.

## Per-item retry churn in the existing Activity cockpit (19 October)

The existing **zSentinel · queue retry incidents** Activity panel now accepts the
extended JSON exported by [retry trend PR #1555](https://github.com/Zennay/zCloud/pull/1555).
It displays **matched pseudonymous queue-item references**, observed claim-cycle
deltas, status transitions, high-priority items and explicit sample-completeness
indicators. The **Export retry handoff .md** includes a bounded matched-item
movement section without raw IDs or titles. The panel remains accessible through
the existing manual, operator-initiated JSON import and the authorized read-only
VPS endpoint; a second explicit VPS read of the same scope computes the equivalent
bounded movement in browser memory.

For each pair of observations, only queue refs seen in **both** snapshots are
correlated. Earlier-only/later-only counts are **not** presented as finished/new
tasks, released claims or permissions to retry. A partial sample is labelled
partial, an unobserved project remains unknown, and older aggregate-only trend
files remain importable. Extended JSON is validated against the known projects,
scope, allowed priorities/statuses, 16-hex opaque reference, <=100 bounded rows,
exact count deltas and source coverage. Unexpected metadata fails closed; all UI
fields are escaped; no credentials, titles, raw queue IDs, worker prompts, or
claim controls are added. Nothing automatically polls the VPS or changes SQLite.

This new UI source and exact Git blob pin are in [PR #1550](https://github.com/Zennay/zCloud/pull/1550).
The producing offline trend CLI is in **separate PR #1555**. Both remain
source-only until the normal exact-head CI/review/merge and serialized production
release. No standalone QA or regression suite was written or manually invoked.

## Safe offline two-snapshot retry comparison and JSON handoff (19 October)

In the existing zSentinel retry incident panel, select **up to two queue snapshot
JSON files** at once. Their source timestamps determine the before/after order.
A two-file comparison requires the same canonical project (or both all), the
same claim-attempt threshold and strictly increasing observation timestamps.
The importer validates both receipts **before updating the visible evidence**:
malformed/oversized files or mixed scopes preserve the prior view. One snapshot
can also be followed by a newer same-scope snapshot; an older second snapshot
requires an explicit Clear or deliberate two-file comparison. Any single new
scope clears the previous unrelated trend.

An imported **Optional trend JSON** is bound to the visible snapshot's project,
threshold **and exact ending observation time**. If they differ, the import is
refused rather than combining unrelated queue histories. Standalone trend imports
remain usable when no snapshot is loaded. The panel shows which rows appeared in
both bounded samples, their observed claim-cycle and status movement and explicit
partial coverage; disappearing from the top-N sample is never claimed as task
completion or as authority to retry. Historical aggregate-only trend receipts
remain compatible.

The high-attempt item list and the matched-item change list each show the first
12 entries by default and offer an explicit **Show all** action when more are
available. Display expansion is bounded to the already validated 100 sampled
records, keeps existing project/priority filtering and never triggers an extra
VPS read. **Show first 12** collapses the view again; Clear removes the
evidence and resets both expansion controls.

Use **Export snapshot .json** immediately after **Read VPS queue** to save a
portable, redacted `zsentinel-retry-incident-v1` source observation. It
reconstructs only the normalized and validated project/priority/status counts,
up to 100 pseudonymous queue references and explicit sample-completeness
metadata; arbitrary API fields, raw queue identifiers, assignment records,
titles, prompts and credentials are **not** copied. Re-import this JSON later
as one of the two before/after snapshots or pass two saved files to the
standalone offline trend CLI in PR #1555. All file downloads are deliberate
browser-local actions without storage on the VPS.

A new trusted VPS reading may update the view only if it is strictly newer
than the last observation of the **same scope**. Re-importing an older trend
likewise requires clearing its existing comparable history. This prevents
temporary source-clock regressions or stale responses from silently erasing
the most recent operator evidence.

Use **Export offline trend .json** to download a sanitized canonical
`zsentinel-retry-trend-v1` record generated from previously validated operator
observations. This retains only canonical project IDs, UTC source times, aggregate
counts, bounded opaque 16-hex queue refs and observed deltas. It contains no raw
queue IDs, titles, prompts, tokens, claims or credentials. Other operators can
open it through the existing Optional trend JSON importer, subject to source
scope and timestamp gates. The existing **Export retry handoff .md** remains
available separately. Neither export makes an HTTP call, starts a poller, or
modifies any worker, lease or SQLite record.

**Source-only delivery:** [PR #1550](https://github.com/Zennay/zCloud/pull/1550);
offline CLI producer [PR #1555](https://github.com/Zennay/zCloud/pull/1555).
Mandatory exact-head GitHub checks, independent review, protected-main merge
and serialized authorized VPS release remain required. No QA suites authored or
manually run.

## Existing resource-detail product fix integrated from PR #1047 (#957)

The current Activity/dashboard integration branch also reuses the **existing
completed product markup** from [PR #1047](https://github.com/Zennay/zCloud/pull/1047)
for [open issue #957](https://github.com/Zennay/zCloud/issues/957).
Only the established resource technical disclosure section of
`public/enhancements.js::resourcePanel()` was integrated. Its project-priority
select controls, resource running status, existing #877 save/pending/error
feedback and independent scheduling/lease semantics were left unchanged.

The default view retains the project-by-project priority controls and status.
CPU/RAM utilisation, attributed/unattributed host CPU and scheduler weights are
behind a single keyboard-accessible native
`<details class="section-details resource-details"><summary>Technical details</summary>`.
This is *presentation-only* and does not read additional APIs or write any
resource configuration. The existing `public/index.html` source pin was updated
to the exact integrated `enhancements.js` Git blob. PR #1047 is untouched and
remains open for independent ownership/reviewer reconciliation; this source
composition must not be mistaken for its separate protected-main merge.

Release: [PR #1550](https://github.com/Zennay/zCloud/pull/1550) is still source-only
until existing exact-head checks, independent review and authorized release
conditions succeed. No QA or regression suites were written, changed or
manually invoked during the integration.

## Local Activity per-project retention and outage evidence (#1518, 19 October)

**Bug fixed in source:** refreshing Activity while filtering to a single project
previously assigned its isolated local events to the entire shared
`ACTIVITY` array. Switching back to **All projects** then lost previously
observed events for quieter projects even though their API reads succeeded.
A failed scoped refresh could also replace meaningful portfolio history with
an incomplete global list. Neither symptom was proof of no work.

The existing browser now retains up to **24 canonical project** read-only
local event streams in ephemeral tab memory, up to 80 events each, and
fairly selects at most **240 displayed local events** across all
visible projects and then orders the selected events by their actual source
timestamps (newest first). Quiet projects remain represented, but the
resulting **Recent activity** view is no longer misleadingly out of time order. A successful empty project read is remembered as empty;
a different project's refresh never evicts it or other scoped histories.
When a local GET fails, its last successful source is retained and explicitly
labelled as an older observation, not current success. Before a project has
been read individually, the original global 80-row VPS API result can appear
as a partial fallback but never proves that its project source is complete.
Archived or unknown project IDs are pruned from retained scoped reads.
Refresh is explicit or part of the existing Activity navigation; **no
additional background GitHub polling or privileged worker action** was added.

The **Local event coverage → Per-project local coverage** disclosure now
shows each project's last successful scoped observation time, latest retained
local event, bounded event count, 80-row source-cap warning and the difference
between a successful empty read, a failed read with retained older evidence,
a stale observation and a project never individually observed. The native
disclosure remains open across rerenders. **Read project** targets one
canonical project, updates the Activity filter and explicitly refreshes its
existing read-only local endpoint. It never claims a queue item, resets a
worker, merges a PR or makes an event current merely because /api/status
still responds.

A change to the Activity project dropdown is an explicit bounded source
request. The reader fetches the selected canonical project's existing local GET
unless its exact scope was observed within the current two-minute local cache
window. It does not fetch GitHub automatically. The same scoped read is available
with **Read project** in the freshness disclosure. This fixes the former case
where changing the dropdown merely filtered a stale global snapshot.

**Export timeline .md** now includes a per-project local VPS source coverage
section alongside its bounded events: latest successful scoped read time, sampled
event count, 80-row truncation and stale/failed/never-observed classification.
This allows the next operator or zSentinel worker to see missing source evidence
without mistaking a green /api/status response for proof of GitHub completeness.
Canonical project IDs, UTC source timestamps and counts only are exported in
the added section; no queue IDs, claims, prompts or credentials.

The **zSentinel product delivery board** (sampled P0/P1 issues and existing PR
references) now precedes the Activity timeline, keeping issue-first product
work in the first view instead of burying it under historic host events.
Both source completeness and GitHub-source uncertainty stay visible.
The exact updated `public/app.js` blob is pinned in `public/index.html`.

Source integration: [PR #1550](https://github.com/Zennay/zCloud/pull/1550).
Issue: [#1518](https://github.com/Zennay/zCloud/issues/1518). Still subject to
independent exact-head CI, review, protected main and serialized release.
No regression or QA suites were authored or manually run.

## Issue-first triage for missing PR references (19 October 2026)

The existing **zSentinel product delivery board** now places a bounded
**High-impact issues with no linked PR in sampled evidence** group above the
complete P0/P1 issue list. It highlights at most eight fair examples from
canonical project issue receipts, preferring P0 before P1. Each row links to
the *existing* GitHub issue and labels the finding accurately: **no open PR
relationship in bounded sampled metadata, ownership unknown, review only**.
A missing PR link is **never** interpreted as an unclaimed lane or permission
to create a competing branch. A matching PR mention is likewise not a merge
or code-ownership attestation.

The board also explicitly lists canonical project issue sources that have
**not been observed** in this browser session, rather than silently treating
the partial 13-project issue sample as a complete portfolio inventory.
When no sampled unlinked issues exist, the UI warns that this is not proof all
issues are owned. Project-source collection remains operator initiated and
bounded; there is no autonomous claim, PR merge, GitHub write or new polling.
The first screen is prioritized by product blocker rather than Activity
history, while exact source freshness and the existing admission boundaries
remain visible.

This implementation is part of the same current-main Activity product
integration [PR #1550](https://github.com/Zennay/zCloud/pull/1550), not a
separate ownership takeover or a production deployment. Source pin updated,
normal exact-head CI/review and serialized deployment gates unchanged.
No standalone QA or regression suite authored or invoked.

## Public GitHub PR/CI/main/branch-head events reach the unified timeline (#1518)

The existing explicit **Read public GitHub** path already fetched PR lifecycle,
Actions runs and main commits, but its browser-produced records had **no
`id` field**. The unified `combinedActivityRows()` source contract
rightly discards rows with no stable provider ID. Consequently, publicly
fetched GitHub progress could appear in the separate evidence panel while
being silently absent from **Project timeline · VPS + GitHub**. The source
now also reads a bounded branch-head page, so the same identity contract
must preserve branch observations without claiming branch ownership or delivery.

This source fix makes each authorized operator-requested public observation
emit an immutable namespaced GitHub identity, derived strictly from its
validated canonical GitHub URL and event type:

- PR created, updated, merged or closed **without** merge:
  `github:Zennay/<repo>:<pr_kind>:<PR number>`
- CI run: `github:Zennay/<repo>:ci_run:<run number>`
- Main commit: `github:Zennay/<repo>:main_commit:<40-hex SHA>`
- Branch head: `github:Zennay/<repo>:branch_head:<validated ref>:<40-hex SHA>`

A branch-head timestamp is the source observation time, not a creation or
last-commit timestamp. The branch receipt explicitly carries
`main_merged=false` and `branch_owner_verified=false`; it is never release
or ownership proof. The token-backed offline collector reads at most one
page of 100 branch heads per canonical project, with long/unsupported refs
counted as skipped rather than rendered unsafely. The same identity
convention is used by strict operator-imported GitHub receipts. It avoids a fuzzy title/date join, retains distinct PR create,
update, merge and closed-unmerged events, and allows only **exact SHA +
canonical project** to coalesce an independently observed local commit with
a GitHub main commit. A merged PR is not deployment evidence; a closed PR
without a merge is not counted as delivered code.

On a second successful, comparable bounded four-source GitHub read, event
identity also enables conservative **first observed in this bounded sample**
labels. Branch HEAD comparison is only page-1 against page-1; older-page
navigation is not treated as a new change. This is **not proof the underlying
branch/PR was created since the last read**. A failed, partial, private or rate-limited read never supplies
authoritative 'new since last' coverage. Older known observations retain
explicit source lag as before.

Browser reads remain operator-initiated unauthenticated public GitHub
GETs with per-project anti-burst budgets and an eight-batch rolling-hour
browser cap. The UI does not auto-poll, query private branches, access tokens
or update the server/worker/queue. The offline collector's separate optional
read-only token path adds one bounded branch-head request per selected
canonical repository within the existing 30-request/three-project limit.
The changes include `public/app.js`, its exact `public/index.html` source pin,
the offline collector/digest/HTML renderer and source documentation. Integration and release gates are still
the normal exact-head required CI, independent review and owner-approved
serialized deployment, not a source-only green claim.

## Product triage and source-bound delivery pulse (19 October, continued)

The existing zSentinel **product delivery board** offers one-click views of
all currently sampled P0/P1 issues, issues with **no open PR reference in the
bounded evidence**, issues with **draft-only PR references**, issues with
**non-draft PR references**, and issues backed by a **stale, failed or not-yet
observed source**. Counters are scoped to the current canonical project or
All projects selection and refer only to the bounded, operator-read sample.
Each issue links to its existing GitHub issue and at most three referenced
open PRs. A PR body mention is **not** a verified fix, current worker claim,
review approval or permission to merge; an absent PR in the sample is
**not** evidence the issue is free. Operator expansion reveals up to 100
fairly distributed P0/P1 issue cards while preserving project boundaries.

**Export handoff .md** uses the same issue triage selection and fair
priority/project admission order (up to 100 sampled items). It records the
number of included and matching issues, source observation, links and PR
reference type. It never exposes raw worker queue IDs, claims, prompts,
passwords, runner tokens, unredacted SQLite rows or private receipts.

The board now also includes a **GitHub delivery pulse**, with observed
source-time events from the **preceding 24 hours** separated into PR created,
updated, merged, closed without merge, Actions run, and main-branch commit
categories. Counts are **bounded sample observations, not live portfolio
totals** and remain UNKNOWN when GitHub evidence has never been read. A
five-row read-only disclosure links to the most recent GitHub-reported PR
merges; the merge is never treated as proof of a production deployment. The
same source-backed pulse (timestamps, categories, sampled merges, and
coverage limitations) is exported to Markdown. Every component continues
to use the existing manually requested, rate-budgeted public GitHub source,
without enabling background polling or worker claims.

This triage + delivery pulse composes into the established dashboard
[PR #1550](https://github.com/Zennay/zCloud/pull/1550) and fixes the earlier
source-only #1518 data gap. Both the UI and its operator handoff are complete
in the branch; protected-main merge, exact-head CI/review and authorized
VPS release **have not** been bypassed. No QA or regression suites were
authored or manually run.

## Previously observed GitHub P0/P1 issues outside bounded pages

The manually requested public GitHub issue observation deliberately reads
up to 30 **oldest** and 30 **most recently updated** issues, plus a bounded
window of open PR references. This is *not* an exhaustive GitHub inventory:
an issue observed in a previous request may remain open but fall between
the two sampled page windows on the next apparently successful read.

Previously, an all-three-requests-successful refresh silently replaced
per-project issue evidence with only the two current 30-issue windows,
discarding such middle-window P0/P1 work. The importer now preserves those
previously observed issue references with an explicit
`staleUnverified` state and shows **previously open; NOT REOBSERVED**
in the product board and detailed issue inbox. It does **not** assert
that the issue is still open, currently unclaimed or ready to implement.
Current reobserved issue evidence is sorted ahead of such carried-forward
observations *within the same P0/P1 priority tier*, preserving global
fairness and the existing maximum of 150 sampled source items.

The operator triage **Source stale / partial** filter includes these
individual not-reobserved issues, even if the three current bounded
provider requests succeeded. The same uncertainty appears in the
Markdown handoff. Explicit source attribution helps avoid both losing
real work due to pagination and silently treating historical records as
authoritative current state. Only exact source GitHub checks, current
Notion claims, required CI/review and actual production receipts can
establish the respective stronger claims.

## 20 October · GitHub 403 no longer hides unrelated portfolio projects

The independent read-only GitHub Activity collector now treats an
**unconfirmed HTTP 403** as a repository-scoped source denial. If one
registered repository rejects its PR/Actions/main-history reads, the collector
does not silently suppress the other independently selected projects.
Repeated calls to the denied repository are stopped for this invocation.

A **401 (invalid authentication)**, **429**, or **403 with explicitly
`X-RateLimit-Remaining: 0` or a `Retry-After` header** still stops subsequent
provider reads globally. `Retry-After` on 403 can signal a secondary GitHub
throttle even when the primary rate budget is nonzero; the collector respects
that instruction rather than continuing across repositories. A 403 without that header is *not* declared a proven permissions
problem: GitHub may also use it for a secondary provider limit. The new
`coverage[].provider_admission` source field therefore distinguishes
`authentication_required`, `rate_limit_confirmed`,
`repository_or_secondary_limit_unknown`,
`global_admission_denied_prior_project` and `none`.

These categories are also rendered in the offline Markdown digest and the
standalone HTML view. They use fixed safe operator explanations, never raw
provider error bodies, tokens or request headers. Earlier valid private
receipts without this optional field remain readable; a receipt with an
unrecognized value is rejected by the digest and HTML importer.

**Scope:** source-level functionality on existing
[zCloud PR #1550](https://github.com/Zennay/zCloud/pull/1550), addressing
[#1518](https://github.com/Zennay/zCloud/issues/1518). This does not enable
background polling, elevate access, infer project inactivity, change claims,
retry GitHub CI, modify a runner or deploy anything. The normal current
exact-head CI, independent review, branch protection, runtime writer and
production release requirements remain mandatory.

### Afzonderlijke, brongetrouwe PR-sluitingsgebeurtenissen

Dezelfde bestaande GitHub-Activity-import heeft nu een eigen **`pr_closed`**
gebeurtenis voor iedere in het begrensde bronvenster waargenomen PR die
door GitHub als `closed` zonder `merged_at` wordt gerapporteerd.
Het event gebruikt de echte GitHub-`closed_at` bron-UTC, een eigen
stabiele namespaced event-ID en een rechtstreekse canonieke PR-link.
Een gemergede PR veroorzaakt **geen** `pr_closed`-event, maar uitsluitend
het al bestaande `pr_merged`-event op `merged_at`.

De read-only CLI, Markdown-overdracht, offline HTML-operatorpagina en
de bestaande Activity-dashboardimport accepteren deze aparte gebeurtenis.
De import weigert een sluiting als de PR-bron tegelijkertijd
`merged=true`, `main_merged=true`, een afwijkende
`pr_lifecycle` of een ander tijdstip claimt. Alle weergaven
markeren sluiten zonder merge uitdrukkelijk als **NIET OPGELEVERD**.
Het beperkte uitvoervenster verdeelt beschikbare eventplekken nu
afzonderlijk over PR-updates, PR-sluitingen, workflowobservaties en
main-commits, om te voorkomen dat een drukke reeks PR-updates alle
sluitingen uit een korte menselijke review verdringt.

Oudere JSON-bronnen zonder dit event blijven importeerbaar en
toveren oude `pr_updated`-records niet automatisch om tot een sluiting.
Dit blijft een **niet-atomaire, begrensde GitHub-observatie**:
een bronvenster zonder sluitingsgebeurtenis bewijst niet dat elders
geen PR is gesloten. Sluiting ≠ merge; merge ≠ bewezen productie-release.

### PR gesloten zonder merge is geen productoplevering

De onafhankelijke GitHub PR-observer leest `state`, `closed_at` en
`merged_at` als afzonderlijke bronfeiten. Een gesloten PR zonder
`merged_at` krijgt de huidige bronstatus `closed_without_merge`; een
wel-gemergede PR heet `merged`, en een nog open PR `open`.
Tegenstrijdige GitHub-bronvelden worden afgewezen in plaats van
geïnterpreteerd als oplevering.

Er komen **geen nieuwe event-types of onverenigbare event-IDs** bij.
Bestaande `pr_created`, `pr_updated` en `pr_merged` houden hun
kanonieke IDs; `pr_lifecycle` en `pr_closed_at` zijn optionele extra
observatievelden. Deze status is nadrukkelijk **de huidige toestand op
het bronmoment**, niet de historische PR-toestand op de dag van creatie.
Oudere JSON-bronnen zonder die velden blijven geldig, maar tonen
`unknown` in plaats van een veronderstelde open toestand.

De offline Markdown-overdracht en HTML-cockpit tonen een gesloten-niet-
gemergede PR expliciet als **niet geleverd**. Per PR wordt de toestand
uitsluitend uit de ene waargenomen `pr_updated` bronregel geteld zodat
creatie en update niet als twee afzonderlijke sluitingen worden gemeld.
Het bestaande verbod op CI=deploy, PR-merge=productierelese en ontbrekende
bron=geen activiteit blijft intact.

## Integration and release

- Backend: [zCloud #1549](https://github.com/Zennay/zCloud/pull/1549) — read-only activity paging and product-only queue admission.
- UI: [zCloud #1550](https://github.com/Zennay/zCloud/pull/1550) — optional panels, explicit loading/errors and pinned JS source.
- Source owner: [#1536](https://github.com/Zennay/zCloud/pull/1536) — original product-only classifier reused, not independently merged.
- Incident: [#1326](https://github.com/Zennay/zCloud/issues/1326) — self-hosted zCloud listener needs independent authorized repair.

These PRs are **not production** until exact-head required GitHub checks succeed, independent review is complete, protected-main merge is permitted and the serialized VPS release is independently verified. Do not enable GitHub-hosted billing, bypass a reviewer, restart an unauthorized service or displace a worker to demonstrate the preview.


## 21 October · Exact worker send/reload operator stop reasons

The existing on-demand **Activity → Worker progress** panel reads only the authenticated, bounded, local `/api/worker-progress-history` source. It now preserves six additional **fixed, static** browser-provided reason codes in the Python read model, and renders their corresponding human explanations in the Activity UI; no raw prompt, URL, arbitrary error string, claim secret or browser-provided prose is returned. The `public/index.html` app.js URL remains pinned to the exact updated Git blob, avoiding an old cached UI that would silently hide the new explanations.

The operator can distinguish three important *unverified* outcomes: (1) the assignment changed while composing and no Send click was authorized, (2) an existing conversation could not load even though a queue prompt may already have been accepted, and (3) the page was reloaded or five-minute response observation expired after a possible Send. The last two explicitly require reconciliation against the original queue and conversation, never an automatic duplicate send. Unavailable per-tab dispatch evidence and a stalled single New chat transition also have separate owner hints. The card caption is **Worker · operator attention**, not merely `Composer`, because these are lease/dispatch continuity problems rather than only a missing DOM editor.

Sources are complementary **open** product PRs: producer [#1552](https://github.com/Zennay/zCloud/pull/1552) (real browser engine and userscript), current Activity integration [#1550](https://github.com/Zennay/zCloud/pull/1550), and the earlier read-model source [#1549](https://github.com/Zennay/zCloud/pull/1549). Their presence in GitHub branches does not imply that any is installed, deployed or merged. The Activity panel never authorizes a lease reset, workflow retry, claim, merge, browser restart or deployment, and never treats browser-reported events as independent proof of generated output.

This documentation accompanies product source only. No new QA/regression suite was authored or run, no CI workflow was manually dispatched, and no live VPS/runner/queue/security or production state was changed. Existing exact-head CI, independent security/review and protected-main release requirements remain binding.


## 21 October · Offline private worker-owner incident package

**New explicit Activity action:** Choose a single canonical project, select a worker slot (1–8), press **Read progress**, then choose **Save private worker handoff JSON** or **Save readable owner brief**. The latter produces a Markdown owner note from exactly the same bounded selected source, with fixed safe next actions and explicit unknown-claim/release status. The existing, authenticated `GET /api/worker-progress-history` is the **only** source; the download adds no API reads or server/Notion/queue writes. A missing or invalid source disables download instead of inventing an idle worker.

The browser constructs a bounded local `zsentinel-private-worker-handoff-v1` JSON file containing the originally observed project/slot/time, at most 24 source event IDs, known fixed event names, bounded browser time/character counts and only explicitly allowlisted operator reason codes with human-safe next actions. It strips all other properties rather than copying raw event objects: **no prompt text, chat title, URL, token, owner identity, arbitrary exception, or queue/claim receipt** is exported. Noncanonical events are omitted; coverage/truncation is carried forward.

The report includes explicit `authority: {claim:false, claim_epoch:false, worker_resumed:false, independent_generation:false, merge:false, deployment:false}`. Operator next action is to inspect the original queue claim, conversation, exact-head PR and CI with a permitted owner; this download never authorizes re-send, retry, claim, merge or deploy. The export is a local manual backup/handoff, **not** background monitoring, current runner status proof or a released user-facing feature until normal merge/deployment.

Compatible with the independent original worker source [PR #1552](https://github.com/Zennay/zCloud/pull/1552) and this existing Activity integration [PR #1550](https://github.com/Zennay/zCloud/pull/1550). No new QA suites, workflow dispatch, mutation of SQLite, runner/worker, production service, billing or security settings.


## 21 October · Recovery of private command acknowledgement after browser restart

The matching browser source on existing [worker v2 PR #1552](https://github.com/Zennay/zCloud/pull/1552) now persists only unresolved command-result receipt identities in private Violentmonkey storage and reloads them after Firefox/browser tab replacement. Activity's existing **Read progress** view and both private handoff exports can now explain two **static, allowlisted** `send-blocked` reasons:

- `command-ack-pending-no-new-send`: Browser action already occurred, but no authoritative acknowledgement of the separate zCloud command-result POST has been observed yet. The worker retries the **receipt only**, does not re-click Send, and defers any automatic next conversation prompt or New Chat reset.
- `command-ack-ledger-unavailable`: Private browser persistence is unreadable or cannot prove a receipt was stored; never treat it as an empty command ledger. Stop worker command execution and request inspection of the original command, account-bound tab and storage with the operator.

The events are still **browser-reported observations**, not proof that a remote task was completed. The report deliberately never exports command IDs, queue IDs, browser URLs, prompt text, tokens, private storage contents or unvalidated arbitrary status strings. An event missing from the local retention window means `UNKNOWN`, not healthy. Source PRs remain gated by independent review and required exact-head CI; neither the Activity interface nor its private export is a release authorization.

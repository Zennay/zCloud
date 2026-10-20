# zSentinel · Private Product Delivery Workbench

The [source-bound product delivery board](zsentinel-product-delivery-board.md) is a deliberately non-authorizing, offline summary of existing P0/P1 issue discovery, queue retry pressure, recent PR-less branches, optional explicit Notion claims and exact-head PR observations. The new **Product Delivery Workbench** turns one saved JSON board into an interactive, still fully local operator workspace.

## Operator flow

1. Generate a private **JSON** board using existing merged CLI and the issue inbox once its exact-head PR is integrated. You may omit optional retry/branch/claim/PR source files, but missing evidence remains unknown.
2. Create the workbench from **that actual immutable board**, without touching live GitHub, Notion, SQLite or a VPS worker:

```sh
python3 scripts/zsentinel_product_delivery_board.py \
  --inbox ./issue-inbox.json --format json \
  --output ./my-delivery-board.json

python3 scripts/zsentinel_operator_workbench.py \
  --board ./my-delivery-board.json \
  --output ./my-private-delivery-workbench.html
```

3. Open the HTML locally, on mobile or desktop. Search and filter the *already observed* real issues by project or priority. Each card links to the original GitHub issue, its bounded existing PR references, known evidence gaps and the owner's recommended next investigation. Choose a **human-only disposition**: investigate, inspect/integrate existing PR, coordinate owner, park external blocker, or candidate for new product implementation (requiring independent fresh claim verification).
4. Add a private note for an issue. **Download private human handoff** to create a source-bound `zsentinel-human-handoff.json` in your browser. If you close the page, **Reopen own handoff** restores precisely that file only if the SHA-256 of the original board, observation timestamp, entire known issue inventory and read-only authorization flags match. Wrong, malformed, overlong or foreign handoffs leave the current notes alone. There is no automatic persistence, autosave, external API call or output to Notion.

## Source, privacy and safety

The workbench opens **one existing regular-file JSON board** via one bounded no-follow descriptor and hashes the **exact bytes that were validated**; a second path reopen cannot silently point its human handoffs to a different board. Duplicate JSON keys, nonfinite values, source mutation, wrong schema and unexpected authorizing flags fail closed. Every GitHub issue/PR link is reconstructed or validated against canonical GitHub-only paths. All visible strings are DOM text nodes, never dynamic `innerHTML`. The embedded JSON escapes HTML delimiters. A strict local Content Security Policy blocks network connections, forms, scripts outside the generated page, images and external resources.

The output HTML is create-only, mode 0600, using the existing board's atomic no-replace reporter. No old snapshot, source, symlink, other report or database can be overwritten. Public issue metadata is the only displayed source content; original queue task titles, prompts, raw lease IDs and credentials are never embedded. The operator's private notes stay in the browser memory until explicit download.

Most importantly: **no claim, merge, release, queue retry or deployment can be performed or authorized through this page.** A visible candidate PR is not proof of ownership, passed mandatory CI, adequate review or release consent; an empty claims snapshot is not a lease release. zSentinel's Notion status stays Verifying until actual required runtime and worker evidence exists. Do not publicize private handoff exports or invent recovery claims from this source-only UI.

No QA, regression or test suites were authored or manually run as part of this feature. The existing repository/owner/production gates stay independent.

## Portable offline project briefing (19 October product extension)

The Workbench now shows a **portfolio observation panel** calculated solely from the
already loaded, bounded JSON snapshot: observed P0/P1 issue totals, projects with
observed issues, referenced PR candidates, explicit evidence gaps, original
observation time and a warning when the saved observation is older than six
hours or its time is unreliable. A zero count is zero **within that captured
snapshot**, never proof that the live portfolio is clear.

Use the existing search, priority and project filters to select the exact issue
set for a handoff. After entering optional **human-only** disposition notes,
select **Preview current filtered briefing**. The complete Markdown preview
includes the original board SHA256, observation UTC time, canonical GitHub
issue and bounded PR links, issue-specific missing gates, next investigation
and any personal notes. Nothing is submitted to Notion/GitHub. The recipient
and the download directory are under the operator's control.

**Download exactly this private briefing** is offered only after previewing it.
Editing a note, switching the selected issue or changing any filters invalidates
the preview; export recreates the content from the current source/notes and
refuses a drifted document. A previously downloaded handoff can still be
restored through the original exact-board-hash-bound JSON path. The Markdown
is human-readable **not evidence of a live claim, security clearance, merge
authorization, valid runner or deployment**. Sensitive human notes may appear
in the file, so do not put it on a public issue or share it without review.

The extension uses only inline static UI code in the existing HTML output.
No live web browser automation, service route, SQLite, git action, worker
dispatcher, queue, new dependencies, QA suites or test runs were added.

## P0/P1 human triage queue and disposition filters (19 October)

The offline Workbench now exposes a **Next-action queue** below the snapshot
portfolio. It displays currently filtered P0/P1 issue cards plus lower-priority
items that the operator explicitly marked as **park-blocker**. The queue uses
the same local, bounded immutable source cards, sorts P0 before P1 and shows
the first available recorded evidence gap together with the **human-only**
disposition. Clicking one entry opens the existing issue details, its original
GitHub issue, candidate PR links and missing evidence. This is a *navigation
tool*, not a scheduler or admission gate.

The **Human disposition** filter works together with existing project, priority
and text filters and controls both the queue and the exact-source Markdown
briefing preview. The default "Investigate / not yet reviewed" includes cards
without a saved choice; **Candidate only** explicitly means "verify live
claims", never that a worker has already been assigned or may claim it.
Changing a disposition invalidates the current Markdown preview and updates
the displayed work. Imported private handoffs repopulate the same filters and
queue from their original board SHA. A missing P0/P1 card means only that the
saved snapshot/filter has none; not that the real GitHub portfolio is clear.

Neither action calls external APIs or modifies Notion, GitHub, SQLite, an
active lease, a runner, or production. The user must still independently check
real ownership, exact-head required CI, review, permissions and serialized
release approval. The source changes do not create or run QA test suites.

## Offline two-observation change detection (19 October)

After generating a **new** workbench from a real current board, load an
**earlier JSON delivery-board** with *Earlier source snapshot*. The HTML
reads the file locally (maximum 2 MiB), validates the expected read-only
schema, per-card GitHub issue/PR identities, bounded paths and the earlier
timestamp, then pins its exact bytes with SHA-256. It refuses the same SHA,
future/tied/later observation, changed authorizing flags, duplicate issue IDs,
oversized cards, malformed IDs and unsupported evidence. There is no network,
GitHub poll or persistent storage. Use **Clear comparison** to forget it.

The comparison distinguishes **newly observed** issue cards, cards with a
changed priority, issue title, recommended investigation, observed PR set or
exact evidence-gap contents, cards still observed,
and cards **no longer in the new bounded sample**. The last category is
explicitly *not resolved*: a changed collector scope, pagination, GitHub
permissions, rate limit, project selection or stale observation could all
cause disappearance. No historical branch, issue or worker is automatically
considered available, integrated or recovered. Newly observed P0/P1 cards
are surfaced first *within their priority* in the human action queue. With
no earlier board, the original static triage order is preserved.

When a previous board is loaded, the reviewed **private Markdown briefing**
includes both source identities and a separate portfolio-wide comparison.
This historical section is intentionally portfolio-wide even if the visible
issue list has a narrower human disposition/project filter; its scope is
named explicitly, and it contains only bounded canonical GitHub issue IDs.
Inspect the complete preview before downloading, especially before sharing
a private portfolio snapshot outside the operator's environment. Importing
or clearing a comparison invalidates an earlier preview.

This feature is a source-observation comparison, **not** a current-state
monitor. There are still no background updates, recovery decisions,
CI dispatches, privileged queue operations, new test suites or runtime
deploys. Existing source/CI/independent owner/release gates remain in force.

### Source-coverage changes are visible, not silently interpreted

The current generated workbench includes only the canonical **bounded
coverage summary**, never GitHub raw response objects: project
partial/degraded/unavailable labels, inbox pagination truncation, card-count
truncation and source denial. It validates these before rendering. Earlier
source imports must carry valid equivalent labels. The comparison explicitly
shows both original capture scopes and flagged unavailable/degraded projects
alongside the two SHA-256 source identifiers.

All original zSentinel issue-inbox windows are partial, even if the
truncation/denial flags are false. Therefore *no longer observed* must never
be relabeled as *closed*, *resolved*, *delivered* or *free for assignment*.
The historical comparison does not have live issue/PR/claim authority and
cannot replace fresh GitHub+Notion owner checks.

### Original issue titles in the delivery cockpit

The board already contains bounded original GitHub issue titles, but the
Workbench previously displayed only opaque repository issue IDs. The
current cockpit now renders the **original public issue title** beside
the canonical ID in the priority queue, issue list and detail view, and
includes it in local text searches and previewed private Markdown
handoffs. Titles must be nonempty, at most 180 characters and validated
before the page is generated. UI strings are inserted only as DOM text;
the Markdown preview uses the same length/control/HTML-delimiter cleaning
as operator notes. Titles never establish assignee or action authority.

### 20 October · Evidence-aware incident changes

The previous comparison counted missing evidence items rather than their
actual contents. Replacing an owner/gate blocker with a different one of
the same count appeared as `still-observed`, so the operator could miss
an actionable change. The workbench now preserves a bounded, validated copy
of prior issue title, recommended investigation, referenced PR set and exact
missing-evidence strings **in memory only**. The historical comparison marks
changes to any of those fields (plus priority) and names precisely which
fields moved in the issue list and source-pinned Markdown briefing.

Earlier imports reject blank/overlength titles, overlength recommendations,
and linked PRs outside the card's original GitHub repository. This avoids
promoting a foreign link from untrusted offline input. A changed/vanished
evidence requirement is **not** proof the gate passed, nor does a newly linked
PR prove completion; every action still requires fresh actual ownership,
required CI, reviewer and release consent. No QA/regression suite was added
or run, and nothing contacts or writes to GitHub, Notion, workers or the VPS.

### Three-line human blocker handoff (20 October)

Every currently selected issue now exposes a **read-only three-line source card**:
original priority/issue/title; bounded next investigation (not an authorized
command); and the first observed missing evidence requirement plus the original
UTC observation. Operators can select these lines for a manually reviewed copy.
The view does not auto-copy to the clipboard, submit a request, include private
human notes, assign a claim, restart a runner or signal a recovery. A first
missing-evidence item is **not** an exhaustive gate inventory; review the full
issue and its original source links before acting. Missing evidence, even when
absent from the snapshot, never means that security/CI/review/release passed.

The three-line summary is regenerated when the issue selection changes; it is
not a continuously updating live monitor. This feature remains offline and
works entirely in the existing file-based operator HTML with no new service,
permission, QA/test suite, dependency, privileged write or release gate change.

### Change-focused triage and briefing filter (20 October)

With a valid earlier board loaded, the operator can now filter the **current**
observed issue list, human next-action queue and private Markdown briefing to:
newly observed issues, materially changed evidence (priority, issue title,
recommended investigation, attached PRs or exact missing-gate strings), or
still-observed issues. **New or changed evidence** combines the first two
categories for a focused delivery handoff. The filter is disabled without a
valid earlier board; clearing the previous board resets it to all currently
observed issues, rather than displaying a misleading empty shortlist.

The historical comparison still lists issues no longer visible in the current
bounded sample separately, and never declares them resolved or reassigned.
Changing the filter invalidates an older briefing preview; only a fresh,
reviewed preview can be downloaded. This remains an offline, read-only human
workflow without queue ownership or release permission. No testsuites were
written or run for this source feature.

## 20 October · Private human follow-up board (product delivery)

The Workbench now has a **Human follow-up · private, manual only** panel for
issues explicitly marked **Coordinate owner** or **Parked blocker**. Operators
may add a human contact **label** (80 characters maximum) and a UTC calendar
**review date** on the issue detail. These values are strictly local human
annotations, **not** verified Notion/GitHub assignees, task leases, delegated
authority, workflow permissions or actual delivery acknowledgements.

The follow-up panel groups human-marked cases by their operator-selected
dates and shows an explicit count of missing contact/date and overdue review
dates. "Overdue" only means that the UTC date has passed, **not** that GitHub
CI, a runner, a human owner or production failed after the observation. The
priority queue keeps P0 before P1 while surfacing overdue manual follow-ups
within a given priority. Its source card still requires fresh owner/CI/review
checks. There is no push/email/calendar/watch notification or automatic claim.

Use the **Follow-up status** filter to focus on: past review dates, missing
contact/date, due today UTC or scheduled later. The same selection applies
to issue navigation, blocker queue and private Markdown briefing. The
unfiltered private follow-up panel remains a comprehensive view of *manual*
human annotations for all bounded cards on the current source board.

Private handoff JSON has **schema_version 2** for contact labels and UTC
review dates. Older schema_version 1 handoffs still restore with empty
contact/date fields; neither version is accepted unless its original source
SHA-256 and observation timestamp match this exact offline board. Imported
v2 dates must be real calendar dates in 2020–2099 and contact labels must
remain bounded without control characters or unsafe HTML delimiters.
The UI sanitizes locally typed contact labels before export. Markdown
previews show the manually selected contact/date and distinguish escalation
from ordinary issues; all text is locally sanitized and the export must be
reviewed again after any edit/filter change.

Contact labels and notes can include private information. Keep exported
JSON/Markdown private and share only through an intentional human action.
No external webhook, browser API call, service, new database, automated
watch, worker dispatch, runner retry, QA suite or security gate change was
introduced. Source-level browser JavaScript was syntax-inspected without
running a QA/regression suite. Integration still requires original exact-head
automatic CI, independent review when required and protected-main/release
authorization. No source merge or production deployment is implied.

## 20 October · Explicitly reviewed offline calendar transfer for human blockers

The private workbench includes an **optional iCalendar (.ics) handoff** for
human-only follow-ups. Only saved issue decisions `coordinate-owner` and
`park-blocker` with **both** a locally entered contact label and real UTC
review date qualify; other product issues are excluded. The user must first
click **Preview reviewed calendar events** and inspect the entire generated
calendar text before **Download exactly these all-day events** becomes
available. Changing any filter, issue detail, human note, follow-up label or
date invalidates the preview; export regenerates from the same source state
and refuses changed content.

Events use stable SHA256-board-bound, canonical issue identifiers, a UTC
creation timestamp, a date-only `DTSTART;VALUE=DATE`, original canonical
GitHub URL and a short explicit "human review only" description. Values
are escaped and long lines are folded at the standard 75-octet boundary.
Export includes **no private free-form operator notes**, no calendar account
identifier and **no embedded VALARM**. The user chooses whether/where to
import this file. External calendar apps may apply their own default alerts,
which should be reviewed before saving. No automatic reminder, external
delivery, watch integration, GitHub write, owner assignment, release, CI
rerun, or background schedule is performed by zSentinel.

The ICS is private because contact labels could identify a person. Keep it
off public issue comments. A scheduled date is never proof of notified or
accepting ownership, and an expired date is not proof of failed CI.
Required live claim checks, exact-head CI, security authorization, review
and original deployment gates remain independent. No QA/regression suite
was authored or manually run.

## 20 October · Reviewed carry-forward between observed source boards

**Use case:** the operator has a newer saved zSentinel board and wants to
preserve a human contact or follow-up date from an older observation. Exact
SHA-pinned `Reopen own handoff` deliberately still rejects files from another
board. To migrate explicitly and safely:

1. Open the new/current workbench, load the actual earlier delivery-board JSON
   using **Earlier source snapshot**. The page hashes its exact bytes,
   validates its bounded source schema and requires its observation time to
   predate the current board.
2. Under **Reviewed human follow-up carry-forward**, choose the private
   handoff exported from **that same earlier board**. Its saved SHA-256 and
   observation timestamp must both equal the validated earlier source, and
   every referenced issue must be canonical and present in that earlier board.
3. Read the proposed transfer list and its counts. Only human decisions
   `coordinate-owner` / `park-blocker` with the **same canonical issue and
   project** on both boards are eligible. Old cards that vanished from the
   bounded new snapshot are omitted, not marked resolved.
4. Click **Confirm carry-forward of reviewed human follow-ups**. It transfers
   only annotations on current cards that have no meaningful manual
   disposition, note, contact, date or previously imported provenance. Existing
   current edits remain intact. Any intervening edit of a candidate prevents
   import until a fresh preview.
5. Reverify each imported human contact, GitHub issue, live Notion claim,
   required CI and release gate. Every imported entry displays the prior
   source SHA prefix; private JSON and Markdown exports preserve its original
   SHA-256 source provenance. This is **not** proof that the old assignee is
   still responsible or that a previously overdue issue is still open.

Earlier v1 JSON handoffs are accepted with empty contact/due dates. V2 JSON
handoffs may contain bounded UTF-8 human notes, contact/date and optional
prior-source metadata. Invalid, duplicate, cross-repository or unrecognized
cards fail closed rather than silently creating new current issues. The
whole flow runs locally in the browser without server writes, browser
network calls, worker assignment, notifications, retries, or queue mutations.
No QA/regression suites were created or run. Integration of this feature
remains gated by the existing protected-main and exact-head release checks.

### Historical source-drift visibility before manual import

Carry-forward preview compares **the old board's exact issue evidence** against
the new board, including priority, original title, linked PRs, recommended
investigation and individual missing-gate contents. It explicitly counts
cards with changed source evidence and identifies the changed fields beside
each proposed human annotation. This warns operators before they reuse a
contact or a date for an issue whose blockers may have materially changed.

An optional locally generated calendar event also includes the previous
board's SHA256 when its manual annotation was carried forward, plus a
renewed-owner-review warning. This is provenance only, not a verified
owner, released claim or calendar notification. The transfer still requires
a deliberate preview and confirmation; current human notes remain untouched,
and all original required source/CI/security/review/release gates stay active.

## 20 October · Local closure and reopening of manual follow-ups

The operator can now explicitly **Mark human follow-up locally complete**
or **Reopen local human follow-up** from the original issue detail. This
state is private annotation history only: it does **not** close the GitHub
issue, verify that a real human received the escalation, release an active
claim, approve CI, or resolve an infrastructure outage.

- A locally closed human follow-up remains visible in the workbench issue
  list and manual follow-up history, labeled **CLOSED LOCALLY — issue still
  UNVERIFIED**. It no longer contributes to overdue/missing-date reminders
  and is excluded from a newly generated manual calendar (.ics) export.
  Operators may filter explicitly for **Locally closed follow-up**.
- Reopening preserves the original manually entered contact and UTC date
  so that the human can review the case again; it does not notify or assign
  an external person. The underlying GitHub issue stays in normal priority
  triage whether the local human follow-up was completed or not.
- A private export now uses **schema_version 3** and preserves the open
  or local-complete lifecycle value. The exact-board restore and older-board
  carry-forward accept v1, v2 and v3. Historical v1/v2 entries default to
  open. Invalid lifecycle states and mismatched SHA/timestamps are rejected.
- The reviewed carry-forward panel shows when an older handoff is locally
  closed instead of silently reopening it, while preserving the previous
  source hash and reporting any new/changed issue evidence. A local closure
  imported from an older snapshot must still be independently rechecked
  before a human assumes the real issue was handled.

All changes remain private in-memory state until the human chooses a
local export. No background polling, calendar service, source/queue writes,
notification, production mutation, test/QA suite or privilege escalation
was added; the original GitHub, Notion, CI, security and release gates remain.

## 20 October · Unsaved private decisions guard

Private notes, human contact/date assignments, manually closed follow-ups
and reviewed prior-board carry-forward are deliberately held in memory,
never browser localStorage or a remote database. Because closing a tab
could silently discard user work, the operator page now shows whether its
private human annotations have unexported changes. Browsers supporting
the native before-unload confirmation warn before the user leaves a tab
with local changes. The browser controls the actual prompt wording and
may suppress the prompt in some situations; the banner remains the
authoritative visible indication to export.

Use **Download private human handoff** to request a locally saved
source-SHA-bound v3 JSON backup, then verify the file actually arrived.
Edits after that action mark the page dirty again. Selecting an exact-board
handoff to restore prompts for explicit confirmation before replacing
unexported annotations; cancelling preserves all current edits. Importing
the original exact-board snapshot resets the local edited marker only when
validated replacement succeeded. Carrying prior annotations forward marks
the work as unsaved again.

The Markdown briefing or iCalendar export is NOT a full private JSON
backup and does not clear the pending-unsaved warning. No automatic
persistence, cloud save, network requests, worker notifications or
privileged recovery actions are introduced. No QA/regression suites
were added or manually run; all CI, review and security gates remain.

### Private follow-up status is never issue closure

Only an explicit human-only blocker decision (coordinate-owner or park-blocker) can carry a locally completed follow-up. Changing the human disposition to a non-escalation decision resets the local follow-up state to open. Imported v3 handoffs, including older-board carry-forward, reject forged locally closed states attached to unrelated issues. This prevents the local completion marker from impersonating real GitHub issue resolution or a released claim. These are changes to the operator's private data model only: original issue visibility, current live ownership verification and release gates remain unaffected.

### Device-clock review trace, not an external acknowledgement

When a human explicitly closes or reopens a private follow-up, the workbench
records the current device's UTC ISO timestamp as the **last local toggle**.
The timestamp is visible in the issue detail and private Markdown briefing,
and is preserved in v3 JSON exports and reviewed cross-snapshot carry-forward.
Older v3 exports without this optional field and v1/v2 backups remain
importable. Malformed clock values from private files are rejected.

A device-generated time may be inaccurate or forged and is NEVER treated as
evidence that a worker resumed, a GitHub issue was fixed, an owner acknowledged
the request, CI passed or a release occurred. No event is transmitted to
Notion, GitHub, a calendar or any external service; the original lease,
authorization, independent review and deployment gates still apply.

## 20 October · Reviewed bulk human follow-up actions

The offline Product Delivery Workbench now supports a bounded **bulk human
follow-up panel**, built on the existing SHA-pinned source cards and *private
operator annotations*. It operates only on issues explicitly marked by an
operator as `coordinate-owner` or `park-blocker`. All currently open manual
follow-ups may be selected with one click; the user may also select individual
entries. Maximum scope is the current captured board (60 issues), never a
live GitHub/Notion owner inventory.

The operator chooses exactly one local transformation for the selected
entries: mark the private human follow-up **locally completed**, **reopen** a
previously completed human follow-up, or change a specified **UTC review
date** for still-open human follow-ups. The date is validated as a real
calendar day in 2020–2099; completed entries are not silently reopened by
date edits. The selected source issue IDs, current private annotation values,
requested operation and source SHA/observation timestamp are bound to
a reviewed **Preview exact private changes**. A second deliberate confirmation
applies only the rows shown. If selection, local notes, dates or source state
change before confirmation, the preview is refused and must be recreated.

Applying a locally completed/reopened state records one device-derived UTC
toggle timestamp; batch date edits do not forge an event timestamp.
Private contact labels, notes and original source evidence are preserved.
All edits mark the private workbench as unsaved until the user explicitly
downloads a JSON handoff. The follow-up and issue lists refresh immediately,
and local completions cease appearing in future optional .ics calendar
exports. Original GitHub issues remain visible and their live state, owner,
lease, authorization, CI, review, runner and release admission are all
**UNVERIFIED**. No automatic claim, reviewer assignment, CI retry, notifications,
calendar writes, workflow dispatch, VPS command or production deployment
occurs. The existing protected-main and independent review gates are
unchanged. No QA or regression suites were authored or manually executed.

### Source checks directly in the bulk review

Every selectable human follow-up includes a canonical original GitHub-issue link. The private pre-apply review prints that issue URL and the first source-observed missing gate alongside the exact proposed local change, so a human can reopen the original owner/CI/release evidence before confirming. The first displayed gap is not an exhaustive authorization checklist and the original source snapshot may be stale. The operator must still verify all current gates independently; neither preview nor confirmation writes to GitHub, Notion, workers, CI or production.

## 20 October · Calendar contact privacy by default

Calendar follow-up export now **excludes human contact labels by default**.
The original issue link, scheduled all-day review, source-observation timestamp
and an explicit "not an authorization" warning remain so the event is still
usable without shipping a private contact name to any calendar provider.
The optional **Deliberately include private human contact labels** checkbox
is initially unchecked and must be chosen by a person before generating
the private .ics file. Toggling it invalidates the previous calendar preview
and disables its download until the operator reviews exactly the new bytes.

This matters because an intentionally imported .ics file can be synced by
the target calendar client even though zSentinel itself never uses any network
or calendar connector. A contact label is user-entered and **not** a validated
GitHub/Notion assignee or escalation acknowledgement. The selected personal
note and any raw GitHub/Notion data are never included automatically.
No background notification, browser storage, worker dispatch, queue mutation,
privilege expansion or production release was introduced. Source syntax was
directly inspected; no QA/regression suite was authored or manually run.

## 20 October · Conflict-safe local bulk undo

After a reviewed, explicitly confirmed bulk follow-up update, operators can
click **Undo last confirmed local bulk action**. The workbench holds only
the immediately preceding in-memory snapshots for the affected issue IDs,
plus their post-apply values and the unchanged original board SHA.

Undo checks *every* affected issue is still on the same source board and
that its current private annotation still exactly matches the recorded
post-apply value. If even one affected value changed, the **entire undo is
refused** without overwriting anyone's intervening edit. A successfully
validated undo restores all affected private records as one local operation,
disables the one-shot undo button and flags the workbench as unsaved so the
operator can explicitly download a new private JSON handoff. Loading a
different exact-board handoff or importing prior-board annotations clears
the pending undo record. It does not persist in browser storage or a service.

This is not rollback of any GitHub issue/PR, real worker claim, queue,
calendar import, CI, review, deployment or external notification: no such
action was performed. It only reverses changes to the **private human
annotation state** within the current browser session. Original verified
owner/security/CI/release gates remain independent and unchanged.
No QA or regression suites were authored or manually run.

## 20 October · Reviewed offline spreadsheet triage export

The private workbench now offers **Preview filtered offline CSV** followed by
a separate **Download exactly this reviewed CSV** action. The export covers
only currently filtered issue cards (maximum 60) from the already saved,
validated board; it does not query or modify GitHub, Notion, workers, claims,
SQLite, CI, runners or production. The export contains the board SHA-256,
UTC source observation timestamp, canonical issue link, captured issue title
and priority/project, observed candidate PR links, evidence gaps, proposed next
step, manual disposition, private follow-up state and date, plus explicit
`claim_authorized=false`, `merge_authorized=false`,
`deploy_authorized=false` for **each row**. The CSV also preserves the
snapshot's **per-project coverage** (`partial`, `degraded` or `unavailable`),
`inbox_truncated`, `cards_truncated` and `github_denial` flags in each
row. Ordinary issues with no explicitly marked human-only follow-up export
`private_followup_status=not-configured`, not a misleading `open`.
Canonical candidate PR links must match the original issue repository
and cannot be duplicates; malformed or cross-repository candidates cause
the entire untrusted source board to be refused. It is a portable working view
for spreadsheet sorting, **not** proof of current issue state or authority.

By default no private contact labels or free-text operator notes are exported.
The operator can explicitly enable those two extra columns; toggling the
checkbox invalidates the prior CSV preview and requires reviewing it again.
Editing a note, selecting a different issue, changing any filter or changing
the source-derived view also invalidates an existing preview. At download
the complete CSV contents must equal the reviewed bytes, or the action is
refused and preview must be recreated. Exports happen through a local browser
Blob with UTF-8 BOM for spreadsheet import; no automatic network/upload or
localStorage persistence.

Every cell is quoted; control bytes are stripped and leading spreadsheet
formula markers (`=`, `+`, `-`, `@`) are neutralized with an apostrophe
before CSV quoting, including untrusted GitHub issue titles and notes.
The private CSV may still be copied to a cloud provider by the chosen
spreadsheet application: the workbench explains this and the private-note
checkbox defaults off. Original ownership, exact-head checks, independent
review, security/consent and release gates remain unchanged. No QA or
regression suite was authored or manually run.

## 20 October · Provenance-bound two-snapshot CSV change ledger

When an operator loads an earlier, validated private board, the offline
comparison now also enables **Preview full change CSV** and a separate
**Download reviewed two-snapshot CSV** action. This is a product export,
not a new live GitHub/Notion observation. It serializes **all bounded issues
from the two saved boards** (up to 60 in each), distinguishing newly
observed, changed evidence, still observed and no-longer-observed items.

Each row records both exact source SHA-256 hashes and UTC observation
times, issue identity/link, before/after priorities and projects,
the categories of changed source fields, both project-coverage labels,
and both snapshot truncation/denial flags. The stable field
`not_observed_proves_resolved=false` explicitly prevents interpreting
an absent row as fixed, merged, released or remotely closed. All three
authorization columns remain `false`. There are no private free-text
annotations, owner labels, current claim assertions, network calls,
issue mutations or external syncs in this change ledger.

The full CSV must be reviewed before saving. If the earlier board is
cleared or replaced or the underlying comparison differs, the previous
preview is invalidated; download recomputes and byte-compares the
provenance-bound CSV. All fields use the same quoted, control-normalized,
formula-safe CSV handling as the single-board export. A UTF-8 BOM aids
manual spreadsheet import. No QA suite was authored or manually executed;
normal exact-head review, CI, protected-main and runtime gates remain intact.


## 20 October · Merge-first PR desk and unlinked product issue queue

The already saved bounded delivery-board now powers a separate **Merge first**
section in the same offline HTML workbench. For the operator's currently
selected project, severity, disposition, follow-up and historical-change filters,
it groups captured candidate PR links **by canonical URL**, shows each PR
only once, orders groups by highest linked issue severity (P0 before P1),
and displays original issue titles, issue links, local human disposition and
observed missing-evidence text. Multiple issue references to one PR are
shown explicitly as coordination candidates, **not** proof of duplicated
implementation, conflict, closure or current PR ownership.

The companion **product issues with no recorded PR reference** section
makes free-looking backlog visible without falsely implying availability:
the source is intentionally bounded, and a missing saved candidate does not
establish that live GitHub has no open PR or that Notion has no active claim.
No new worker task or branch is automatically created.

**Preview filtered merge-first handoff** produces a source-SHA256-bound
Markdown snapshot of both groups, including captured UTC time, canonical
public issue/PR URLs, original priority, missing evidence and the fact that
current exact-head CI, reviews, branch rules, worker claim and release
permissions are unknown. **Download exactly this reviewed PR handoff** accepts
only the unchanged complete preview; adjusting filters or private human
dispositions invalidates the preview. The output is a local browser download.
Private personal notes and contact labels are *not* included. No GitHub,
Notion, browser persistence, queue, runner or production mutation occurs.

The feature reuses existing canonical board validation, URL admissions,
bounded card/reference lists, DOM text nodes and strict offline CSP. It
does not infer `merged=true` from a historical PR reference or enable any
automatic claim, merge, review, dispatch or deploy. Existing mandatory CI
and independent review gates remain separate. No new QA, regression or
test suites were written or run.

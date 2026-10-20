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

## Snelle offline triage: navigeren zonder claims

De bestaande private Workbench heeft nu direct onder de filters een vaste
**Vorige issue / Volgende issue / Volgende open P0/P1-opvolging** bediening.
Met **J** (volgende) en **K** (vorige) kan de operator dezelfde gefilterde,
brongebonden issues doorlopen. In tekstvelden, selecties en notities zijn die
toetsen volledig uitgeschakeld zodat ze het typen nooit verstoren.

- Volgende/vorige circuleren alleen over de **zichtbare gefilterde**
  momentopname en tonen positie plus bronissue. Geen filterkoppeling of echte
  claim wordt gewijzigd.
- De P0/P1-sneltoets in de UI selecteert uitsluitend een urgente,
  **niet lokaal gesloten** menselijke opvolging. Een lokaal gesloten
  follow-up is géén gesloten GitHub-issue en sluit het issue niet uit van de
  gewone lijst.
- Bij elke overgang worden eventuele handmatig ingevoerde notities eerst
  in het bestaande **lokale handoff-geheugen** bewaard en wordt een eerder
  gemaakte privé-exportpreview ongeldig. Voor definitieve bewaring blijft
  expliciete JSON/Markdown-export nodig; geen browserstorage of autosave.
- Als een filter geen kandidaten meer toont, blijven huidige GitHub-status,
  actief eigenaarschap, verplichte CI, review en releaseautoriteit onbekend.
  Geen automatische bronopvraag, taakclaim, workeractie of merge.

De nieuwe navigatie is expliciet **lees- en bedieningsfunctionaliteit voor
het bestaande lokale product**, geen scheduler en geen QA- of testsuite.

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


### Structured review-only JSON manifest

The same Merge-first Desk also supports **Preview exact review manifest (.json)**,
then a separate deliberate **Download exactly this review-only JSON** action.
The manifest includes a versioned schema, source board SHA256 and UTC
observation, current filter count, bounded coverage flags and de-duplicated
candidate PR-to-issue relationships, plus unlinked issue references. It
includes the original public GitHub URLs, captured product priorities and
missing evidence, without the local operator's private notes or contact
labels. Each PR and each unlinked issue has `active_claim_verified=false`;
the root explicitly records `claim_authorized=false`,
`merge_authorized=false`, `deploy_authorized=false` and
`dispatch_authorized=false`.

The preview is rendered as plain text. Download re-derives the entire manifest
from the current filtered source and refuses byte drift. All original link
identities were already validated when opening the source board, and
the manifest is not a current GitHub state query or machine-executable
dispatch/release instruction. Only an independently authorized operator or
worker may later inspect a candidate using live ownership, CI and release
evidence. No automatic workflow, background scheduler, claim or PR mutation
was installed.


## 20 October · Saved live GitHub gate source overlay for merge-first triage

The workbench now optionally imports a **single saved, bounded JSON GitHub
PR gate observation** and attaches it to *only* canonical PR URLs already
linked from the validated, offline delivery board. This bridges the local
Merge-first operator view with the source created by the separate zSentinel
live issue/PR scope cockpit in [PR #1567](https://github.com/Zennay/zCloud/pull/1567).
It does not merge either pending PR and does not make the offline tool
into a GitHub client.

### Workflow (once both source PRs have passed required integration gates)

Produce a saved read-only report using the authorized operator's own local
GitHub credentials and the **existing** command in the PR #1567 cockpit:

```sh
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --inspect-pr-gates \
  --max-prs 10 --format json > saved-github-pr-observation.json
```

The output can be imported into the already-open local Product Delivery
Workbench through **Import GitHub PR-gate snapshot**; it requires no browser
network access, token, service, Notion claim or background scheduler. A direct
saved `zsentinel-current-pr-gate-observation-v1` JSON receipt is also
accepted. The file must be under 1 MiB, use a supported versioned schema,
contain the source's explicit non-authorizing flags, have a valid nonfuture
UTC observation timestamp and match at least one original board PR link.
Malformed, foreign, conflicting, changed-head or unavailable evidence never
promotes a PR to merge-ready. One failed import keeps the previous overlay
unchanged. The overlay is memory-only and can be cleared explicitly.

The optional **PR gate focus** narrows *only the grouped PR references* to
observed failing CI, pending CI, review changes/missing approvals, missing or
incomplete receipt, or apparently completed observed checks. An observed
green group still says **NOT MERGE AUTHORITY**: the real current PR HEAD,
required branch rules, independent review and owner/release consent are
not established by this saved JSON. Within priority order, failed and
pending observations are shown first. The independent list of product
issues without captured PR links remains visible and never becomes a
claimable queue.

Imported observations show the saved HEAD prefix, modern and legacy
pending/failed check counts, independent review count, source completeness,
explicit human next gate and age. Receipts older than 15 minutes are labeled
**STALE**; newer receipts remain *observational*, not live authority. The
exact imported source bytes are SHA-256 pinned. The reviewed Markdown and
JSON handoffs include that hash, their observed source time and explicitly
false authorization flags, never raw credentials, private contacts or notes.
All downloads require a preview of the *current* filters/imported source,
and modifying the overlay invalidates existing previews.

The feature writes no issue, PR, Notion page, queue, worker lease, runner,
workflow, VPS service or deployment. It performs no merge/restart/dispatch
and adds or manually invokes no QA/regression suite. Current protected
main, mandatory CI, review and release rules apply independently.


## 20 October · Complete offline → current GitHub → offline release triage

The offline operator can now export a source-bound
`zsentinel-offline-merge-first-review-v1` JSON handoff and re-inspect its
**highest-priority existing PR references without manual retyping** via the
read-only collector in the already-open
[PR #1567](https://github.com/Zennay/zCloud/pull/1567).

1. In the workbench, set desired project/P0/P1 filters and deliberately
   download **Download exactly this review-only JSON** after preview.
2. In an *authorized local* environment with configured GitHub access,
   run the read-only existing-PR gate command:

```sh
python3 scripts/zsentinel_pr_gate_now.py \
  --from-review ./zsentinel-offline-merge-first-review.json \
  --output ./private-current-head-gates.json
```

3. Back in the original offline workbench, import the new JSON using
   **Import GitHub PR-gate snapshot**, then inspect the red/pending/review
   groups and optionally download a reviewed Markdown or JSON handoff.
   If the source handoff contains more than four referenced PRs, run a
   separate bounded `--batch 2` command with another new output filename,
   then import **that** observation. The interface displays the selected
   batch number and total; one imported file does not cover other batches.
   A user must separately confirm current PR HEAD, independent human review,
   protected branch/check policy, active claims and deployment authority.

The importer compares the new source receipt's **original board SHA256**
against the actual currently opened offline board, validates optional
batch index/total/start/count against the full source candidate count,
and rejects unrelated boards and malformed/partial provenance without
discarding an existing accepted overlay. The UI explicitly shows `source_review_pr_count` and
warns when a >4-candidate source was **only observed for the first four
highest-priority PR references**. The exact imported gate receipt bytes are
also SHA256 pinned. Older than 15-minute sources are marked stale.

No previously saved observations can confirm live mergeable status or
current required rules. Neither this local workbench nor the bounded
GitHub collector claims issues, reruns CI, merges PRs, dispatches workers,
writes to VPS or deploys to production. Both source PRs must independently
clear mandatory CI, protected-branch and review conditions before this
workflow exists on `main`. No QA suites were created or run.


## 20 October · Complete saved gate-batch bridge to the existing offline browser

The new **`scripts/zsentinel_saved_gate_batch_bridge.py`** joins saved
current-head PR-gate receipts from the existing read-only collector in
[zCloud PR #1567](https://github.com/Zennay/zCloud/pull/1567), into **one**
`zsentinel-integrated-issue-pr-scope-v1` JSON document that the existing
**Import GitHub PR-gate snapshot** browser input in this workbench already
accepts. The offline browser importer now also validates the bridge's pinned
original board SHA256, complete-batch marker, bounded oldest/newest UTC
timestamps and exact set membership of captured PR URLs. It visibly labels
the receipt set as complete *saved batch coverage*, never complete
mandatory CI/review or merge authorization. Network restrictions, runtime
and owner claims remain unchanged.

Use a **previously reviewed and downloaded** exact JSON manifest and
the separate non-mutating collector's output for *every* numbered batch.
The bridge deliberately uses the **oldest** batch observation as the browser's
overall timestamp so it does not present stale members as fresh.
Example for a nine-PR manifest, after separately collecting the three
read-only batches using `zsentinel_pr_gate_now.py --from-review ... --batch 1/2/3
--output ...`:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./reviewed-manifest.json \
  --receipt ./private-gates-batch-1.json \
  --receipt ./private-gates-batch-2.json \
  --receipt ./private-gates-batch-3.json \
  --output ./new-private-combined-gates.json
```

Then import **new-private-combined-gates.json** in the offline workbench.
The result contains only original canonical issue-to-PR associations,
the bounded allowlisted exact-head observation fields, original manifest
SHA256, original board SHA256, observed batch coverage and explicitly
false claim/merge/deploy/writes flags. It is created exclusively as a new
private `0600` file with an `fsync` before the command succeeds.
No existing file is overwritten. Neither original receipts nor any
privileged GitHub/Notion/VPS state is modified.

**Fail-closed rather than silently showing a partial backlog:** all
`ceil(PR count / 4)` exact numbered receipts must be supplied. The bridge
verifies their raw manifest SHA, original saved board identity, exact
priority/repository/PR sort order and batch boundaries against the
existing collector's contract; it refuses duplicate/missing batches,
unexpected PRs, changed/mixed source identity, source data outside a
ten-minute observation window, foreign issue relationships, and
unbounded files. Limits: 300 PRs, 75 batches, 60 issue rows, up to five
PR references per issue, at most 1 MiB per input/output and 8 MiB of
combined input. When any bound is exceeded, no combined import is created;
the operator can use smaller separately reviewed manifests.

This is **saved source evidence**, not fresh or necessarily complete
mandatory checks, branch rules, human reviews, current worker ownership,
or deploy authority. Even a complete file set may contain unreadable PR
observations or checks still queued. Operators must open each real PR
and independently verify its current exact head, required branch
protection, independent review and authorization. No QA/regression
suite, CI rerun, paid runner, live worker or production action is
initiated by this bridge.


### 20 October · Trustworthy saved PR triage and strict input admission

The original offline Merge-first Desk now displays one **saved PR gate triage
overview** for its current human-selected filter: failed observations, pending
checks, review required, unavailable/incomplete source and the explicitly
non-authorizing category of apparently clear sampled checks. It always labels
these as saved observations. The private Markdown handoff includes the same
overview. A structured `saved_gate_triage` section in the reviewed JSON
manifest makes the observed categories available to a subsequent *human*
operator without turning them into a worker assignment or merge command.
All live exact-head required checks, current claim, branch rules, independent
reviews and owner/release permissions remain `false`/unverified.

The companion `zsentinel_saved_gate_batch_bridge.py` additionally refuses
cross-repository or duplicate issue-to-PR links in saved manifests, even when
their individual URLs are syntactically valid. Untrusted JSON is now admitted
through a single bounded no-follow/nonblocking **regular-file descriptor**;
the file identity and size are compared before/after the read. Duplicate
JSON keys (including a second `merge_authorized` field), nonfinite numbers,
replaced/symlinked paths, nonregular files and visibly changing bytes are
rejected before any import is written. The source limit, UTC bounds,
all-batches-required rule and private output permissions remain unchanged.

These are product changes on the **existing open PR #1560**, not a
standalone QA initiative. Source-acceptance and optional normal mandatory
CI remain separate from authorization to merge or deploy.


### 20 October · Original board membership survives combined import

After importing a complete saved-gate bridge, the browser now checks
**every reported issue ID and issue URL** against the exact already-open
validated delivery board. Each reported PR must appear in that original
issue card's captured PR links. Repeated issue entries, repeated PR links
for one issue, foreign board issues, and missing gate observations are
refused before the existing accepted overlay is replaced.

This preserves the intended meaning of the complete historical batch
indicator. It says *all saved receipt batches for one source-bound
review manifest were examined*, never that all portfolio PRs were
examined or that exact-head required CI, branch protection, independent
review, production safety, current claims or release permissions
passed. Saved gate categories are also included in the reviewed
Markdown/JSON operator handoff, with false authorization flags.

Latest implementation on the same existing PR; no owner or active-claim
transfer, source-only change, no QA suites written or manually executed.


### 20 October · Continue with safely available batches

The offline saved-gate bridge now offers an **explicit partial import** when
one source batch is absent or unavailable. This avoids freezing all unrelated
PR observations while preserving source integrity and mandatory merge gates.

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./reviewed-manifest.json \
  --receipt ./private-gates-batch-1.json \
  --receipt ./private-gates-batch-3.json \
  --allow-partial \
  --output ./new-private-PARTIAL-gates.json
```

Without `--allow-partial` the original all-batches-required behavior remains
the default. Each provided receipt still passes exact original manifest SHA,
original board SHA, ordered four-PR batch membership, strict UTC source time
window, allowlisted observational fields, and identity-bound, regular-file,
size/duplicate-JSON checks. Missing source cannot become a success.

The combined JSON carries `observed_batch_count`,
`expected_batch_count`, `review_candidate_pr_count`,
`missing_batch_count`, sorted `missing_batch_indexes`, and
`all_batches_observed_not_all_checks_required_verified`. The browser
verifies these fields together: no forged complete flag, missing index outside
the manifest, duplicate index, or contradictory totals is accepted.
It reports **PARTIAL saved batch coverage** and exact missing batch indexes.
For the versioned bridge, it additionally recomputes the number of distinct
PR receipt identities expected from the manifest's candidate count and
received batch indexes, and refuses reports that silently omit one PR from
a supposedly supplied batch. The human review-only JSON export separately
records the original candidate count, distinct observed PRs, and missing
batch indexes. A fully supplied source still does **not** establish green
required checks, independent review or merge authorization.
Only actually supplied PR observations appear; other captured PRs remain
**UNKNOWN**. The existing original board membership/URL checks continue
to apply to every imported issue/PR association.

The workbench's read-only human Markdown and JSON export carries source
coverage and missing indexes. A *complete saved batch set* is still not
evidence that required current GitHub workflows succeeded, the current PR
HEAD remained unchanged, independent owner reviews occurred, a worker slot
is free, or deployment was authorized. It never triggers a dispatch,
retry, merge, claim, external network call or file overwrite. Refuse reports
with zero receipts; unverified input or unsupported schema cannot be
partially ingested.

No QA/regression suite, runner restart or privileged production task is
needed for this offline source feature.


### 20 October · Direct PR navigation for missing batches

The partial bridge now records `missing_batch_prs`: for every absent
original four-PR review batch, it includes its **exact saved canonical PR
links**. These are extracted from the original previously reviewed manifest;
they are NOT live-open, current-head, check, review, ownership or deployment
evidence.

The browser validates the missing batch index sequence, exact expected PRs
per numbered batch, uniqueness and that every URL belonged to the already
validated original saved board. A missing-batch PR cannot simultaneously
appear as a received gate receipt. Invalid source fails atomically without
replacing a previously imported observation.

In the merge-first desk, a new **PR links with no saved gate observation**
area provides the original clickable links, grouped by missing batch,
explicitly marked **UNKNOWN**. The operator can use the existing
`zsentinel_pr_gate_now.py --from-review <original-reviewed-manifest>
--batch <number>` read-only collector for just the missing batch when
appropriate. The Markdown and JSON review-only handoffs include the
same unobserved PR links; neither output constitutes permission to inspect
private data, claim work, start CI, merge or deploy.

All original source limits, exact-head release gates, private local file
permissions and no-network browser restrictions remain unchanged.


### 20 October · Native offline multi-batch PR gate import in browser

The existing merge-first workbench can now combine original reviewed
manifests and one or more saved gate batch receipts *without any
command-line step*. In the **Import GitHub PR-gate snapshot** panel,
expand **Combine original reviewed manifest and saved PR batches here
(offline)**, select the exact previously downloaded
`zsentinel-offline-merge-first-review.json` and select the corresponding
JSON output files from the read-only
`zsentinel_pr_gate_now.py --from-review ... --batch N` commands. Press
**Combine saved batches in this browser**. This processes only
explicitly selected local files using browser File/crypto APIs; the
offline page does not contact GitHub, Notion or the VPS and cannot
start, retry, merge, claim or deploy work.

The browser rejects a mismatched board SHA, manifest SHA, foreign or
duplicate original PR/issue associations, improperly ordered batches,
repeated batch indexes, non-canonical PR URLs, unbounded inputs and
contradictory non-authorizing gate flags. It uses the same numeric PR
and case-sensitive ASCII repository sort as the Python source
converter. Input is restricted to **one <=1 MiB manifest, 1–75
<=1 MiB receipts and 8 MiB total**. All received batches must fall
within a common 10-minute saved observation window; the timestamp
used for source age is the **oldest** batch. Invalid input leaves the
previously imported overlay untouched.

Selecting a subset of originally available batches is supported.
The UI explicitly shows **PARTIAL** coverage, exact missing indexes,
the original corresponding PR links as **UNKNOWN**, and observed
gate categories in the existing merge-first desk and private
Markdown/JSON human handoffs. Supplying all original batches yields
**COMPLETE saved-source coverage**, never complete required checks,
independent approval, live ownership or release authority. Users may
still import one gate JSON or a precombined CLI bridge using the
existing single-file control; neither path loses its behavior.

The local source inspection for this feature checks Python
syntactic validity and embedded browser JavaScript syntax only.
No QA/regression suite, CI retrigger or privileged VPS task was added.


### 20 October · Focused missing-batch collection handoff

When saved historical batches are incomplete, the workbench now also
prints the *manual read-only* command for each missing batch beside the
unobserved PR links. The same command is included in the private Markdown
operator handoff. For example, with batch 2 missing:

```sh
python3 scripts/zsentinel_pr_gate_now.py --from-review ./zsentinel-offline-merge-first-review.json --batch 2 --output ./zsentinel-saved-gate-batch-2.json
```

The command is rendered as text, **never executed**, and its batch index
is bounded to 1–75. The operator decides whether/when to collect new
GitHub observations; it requires access to the exact original reviewed
manifest and must respect current GitHub/API and project ownership gates.
New data can be imported via the same in-browser multi-batch selection.
No CI retrigger, write to GitHub, claim, merge or deploy is performed.


### 20 October · Save and later restore an in-browser combined source

The browser's **Combine original reviewed manifest and saved PR batches**
path now also produces a *portable offline saved-source snapshot*.
After successful source-bound import, select **Preview portable, private
combined JSON**, inspect the exact source (and its non-authorizing
provenance flags), then **Download exactly this reviewed combined JSON**.
Nothing is downloaded automatically or transmitted to GitHub, Notion,
the VPS or another service. The output filename is
`zsentinel-offline-source-bound-pr-gates.json`.

On another offline session, open the ordinary **Saved PR gate JSON**
single-file importer and select that saved combined file. It contains
the same `zsentinel-integrated-issue-pr-scope-v1` schema as the Python
batch bridge, including source board and manifest hashes, oldest/newest
saved source timestamps, all/partial coverage and exact missing
batch indexes/PR links. All check/review rows are restricted to the
canonical allowlist and explicitly disallow claims, merges, deployments
and writes. File size is capped at 1 MiB, and the generated combined
JSON is checked against the workbench's existing single-file importer
*before* it can replace the current overlay.

Only actual received batches are included. PRs in missing batches remain
UNKNOWN; saving and restoring a JSON does not turn historical observations
into live required-check or independent-review evidence. If the reviewed
download preview or imported source changes, preview is required again.
The browser never persists personal notes, private gate observations or
files silently. Python AST and embedded JS syntax were checked on the
exact pinned branch source (not a QA or regression suite).


### 20 October · Compare saved PR gate changes, without inventing live recovery

The existing PR gate panel now includes **Compare an older saved PR-gate
snapshot with this imported source (offline)**. First import the newer
single-file or browser-combined gate observation. Then select an
**older saved** gate JSON from the same original captured board. The
older UTC source timestamp must precede the newer observation. The
normal source-validation path checks the older JSON before comparison.

The view lists PR references whose **saved observational state**,
same-HEAD pending/failing/approval counts, or sampled HEAD changed.
Each row links to its original PR. A changed HEAD is always marked
**NOT COMPARABLE** for old versus new check counts. Unavailable, absent,
incomplete and no-check-context sources are **UNKNOWN**, not a passed
workflow or PR. If the samples do not differ, the view explicitly
says this is *not* evidence that mandatory checks are green today.
Both exact saved UTC observation times appear in the report.

The operator can preview a Markdown **historical PR gate difference**
report and deliberately download those exact reviewed bytes. The
report contains the original source-board identity and both selected
JSON SHA-256 digests, with prominent labels that active claims, live
GitHub HEAD, required branch rules, independent approval and release
authority were never verified. Replacing or clearing the current gate
source clears any previously selected comparison and its export.

No GitHub request, runner operation, claim, CI rerun, merge, deploy
or local automatic persistence is made by the browser. This provides
human incident recovery context only; the historical comparison
cannot establish live delivery or authorization.

# zCloud GitHub activity observer (read-only source)

Tracks https://github.com/Zennay/zCloud/issues/1518. The local activity stream cannot represent a remote PR, merge or GitHub Actions run absent from the VPS checkout. This independent source provides bounded real GitHub observations **without modifying** the serialized server owner, production SQLite, eventstream, browser workers or active queues.

## Use

From this repository, on an approved operator host:

    python3 scripts/zcloud_github_activity_source.py --project cloud --hours 24 --max-events 80

For two or three scoped projects, request repeated --project options; use separate invocations for larger portfolios to stay within the 24-request budget. A private repository requires an existing GitHub token in the process environment with the smallest read-only permissions necessary for pulls and Actions. **Do not put a token in an argument, file, dashboard, issue or committed config.** There is no token persisted by either script. Without access the individual project reports repository_inaccessible / unavailable; an outage never becomes an empty, complete feed.

Readable operational output, with links and explicit no-deployment claims:

    python3 scripts/zcloud_github_activity_source.py --project cloud --project supa | python3 scripts/zcloud_github_activity_digest.py

**Compare subsequent snapshots without SQLite, worker restarts or GitHub write authority:** save a first bounded receipt, then supply it on the next invocation. Both files stay local; the CLI reads the previous snapshot but never modifies it.

    python3 scripts/zcloud_github_activity_source.py --project cloud --max-events 80 > /tmp/zcloud-first.json
    python3 scripts/zcloud_github_activity_source.py --project cloud --max-events 80 --previous /tmp/zcloud-first.json > /tmp/zcloud-next.json
    python3 scripts/zcloud_github_activity_html.py < /tmp/zcloud-next.json > /tmp/zcloud-activity-changes.html

The new receipt records stable per-event `first_observed_at` and `new_since_previous`; the digest and HTML highlight events missing in the **prior bounded sample**, not necessarily newly created GitHub work. Repeated observations preserve the earliest valid first-seen timestamp carried by the supplied prior receipt. The input must be a local, bounded, non-symlink regular JSON file at most 30 days old with consistent canonical project/source identities and no authority flags. If these conditions fail the CLI refuses the comparison. This feature makes no persistent database writes or completeness claims.

## One-click public GitHub evidence in Activity (explicit opt-in)

The Activity screen now has two independent **Read public GitHub** actions, one for PR/CI/main activity and one for existing P0/P1 issue/PR candidates. Pick one project from the existing Activity project dropdown, then click the relevant action. Requests run **in the user's browser** to `https://api.github.com/repos/Zennay/<canonical-repo>/...` using `GET`, `credentials: omit`, no redirects and no browser/server API key. They are not scheduled background polling. There is a two-minute per-project backoff for each action, with bounded 8-second requests and response byte caps. The GitHub origin and canonical `projects.json` repository name are validated before requests. Unavailable/private/rate-limited sources show partial or unavailable coverage, never zero-as-proof.

- PR/CI/main: a bounded recent PR window, Actions workflow runs, and canonical `main` commits, with reconstructed canonical GitHub links; no false attribution from green CI to merge or deployment. GitHub activity is shown in its own panel separate from unchanged local VPS events.
- Product inbox: bounded oldest and most recently updated open GitHub issue windows plus recent open PR references; explicit P0/P1 title/label priority; QA-only issues suppressed per 17 October user policy. PR mentions and closing-keyword references are not active owners or evidence of merged fixes.
- Selection is **required**. 'All projects' does not issue dozens of anonymous GitHub calls and pretend that the entire portfolio was covered. These browser samples are never uploaded to zCloud, persisted as a queue, used to claim work or used to authorize a deployment. Refreshing the browser discards them.
- The original manually generated JSON receipt imports remain available for private repositories, larger samples, cross-project operator workflows and stricter source evidence. The one-click public snapshots are always labelled **partial**, even if the bounded GETs succeed. No user authorization/CI/release gate is changed.

## Existing resource-priority feedback integrated with current dashboard (issue #877)

This main-based integration also carries the actual `public/enhancements.js` product code from existing open [PR #1532](https://github.com/Zennay/zCloud/pull/1532), without rewriting that upstream branch. While a resource priority is being saved, the dropdown is disabled and `aria-busy` is set with visible progress. A backend-confirmed response must name an accepted saved priority; the UI distinguishes **saved**, **saved but live VPS weight not applied**, **saved but refresh failed**, and **save not confirmed**. On a failure, the previous value is restored and no raw transport/server error is shown. The JavaScript blob is pinned separately in `public/index.html` to prevent a stale client asset from hiding the UX fix.

The upstream PR #1532 remains open and must not be independently merged along with this source-composed PR until owner review reconciles their paths. This source copy did **not** change the resource-priority API/permissions, queue, workers, live VPS, SQLite or deployment. Preserve the existing independent required CI and UI review gates.

## Read-only worker progress alongside GitHub Activity (dependent feature)

The existing Activity page now includes a **Worker progress** panel for one selected project and worker slot 1–8. The user explicitly clicks **Read progress**; it performs an authenticated same-origin `GET /api/worker-progress-history?project=<id>&slot=<n>&limit=24` and never automatically polls or triggers a worker. Four cards separate last browser event, last prompt, last reported generated-text progress, and last completion; an expandable bounded event list shows sanitized UTC timestamps, event IDs and reported character counts only. Responses failing the strict schema, or unavailable due to authorization/deployment, show a clear error rather than falsely claiming no worker activity.

**Dependency:** this API endpoint is implemented in [zCloud PR #1549](https://github.com/Zennay/zCloud/pull/1549), not in this Activity-UI PR. The panel is inert and clearly indicates unavailable backend state until a separately authorized release includes the endpoint. Browser telemetry is never proof of a current claim, real visible text generation or live deployment; it cannot acquire/modify leases or use diagnostic output for automated merges. This UI dependency requires coordinated reviewer/release sequencing. No QA, tests or backend behavior changed by this Activity branch.

## Activity tab: manual GitHub evidence without changing the server

This PR also integrates the observer's JSON receipt **directly into the existing zCloud Activity tab**, via `public/app.js`. Create a receipt locally, go to **Activity → GitHub evidence → Import a JSON receipt**, and select the saved JSON file. The browser reads it **in memory only**. There is no file upload, server-side API change, GitHub token in the browser or modification to the local VPS activity stream.

The Activity page presents GitHub PR/CI/main history in a separate project-filterable panel. It displays exact PR head SHA prefixes, first-observed snapshot flags and canonical links, while keeping partial-source and unverified merge/deploy warnings. **Clear snapshot** removes the in-memory selection. Page navigation preserves it within that browser tab; a browser reload discards it.

The import admits at most a 1 MiB JSON file and 200 events; enforces the zCloud read-only schema with all authorizing flags false; binds each event to a known displayed project and canonical Zennay GitHub repository; derives links from validated positive PR/Actions IDs or exact 40-hex main commit SHA; rejects duplicated/cross-project event IDs, invalid timestamps, future observation dates and unknown source identities. Uploaded arbitrary HTML, file paths, raw issue bodies or upstream links are never injected into the page. No secret keys or worker control actions are exposed.

This **operator-assisted source bridge** is intentionally distinct from an automated GitHub collector. The owners of the central `/api/activity`, eventstream and serialized production release retain live synchronization, credential, CI and deployment authority. An imported receipt is not proof of a live deployed system.
Mobile-readable self-contained **offline HTML view** (no JS, remote assets, tracking, server or credential persistence):

    python3 scripts/zcloud_github_activity_source.py --project cloud --hours 24 --max-events 60 | python3 scripts/zcloud_github_activity_html.py > /tmp/zcloud-activity-snapshot.html

Open that local file in a browser. It displays expandable accessible per-project timelines for PRs, workflows and SHA-bound main commits, with explicit incomplete-source and no-deployment warnings. Generated HTML escapes every data field and derives links solely from validated canonical repository names/IDs.

The GitHub HTTP reader refuses redirects altogether, including when a token is present; it never forwards Authorization across redirect destinations. Merge-target reporting and canonical main-history commits are separate evidence and must not be added together as two delivered features.

For every sampled PR event the observer also captures its **exact GitHub head commit SHA** (not a user-controlled branch name). This distinguishes actual code heads from mere issue/comment updates, without inferring delivery or complete non-main branch inventory. HTML and Markdown outputs show that SHA prefix; inspect the GitHub PR for the full commit. If the PR head SHA is malformed/missing, that source window is marked partial rather than inventing a commit.

The observer's output is zcloud-github-activity-readonly-v1 JSON containing event stable ID, canonical project/repository, event kind, source timestamp, separately recorded observation timestamp and coverage. It exposes no PR title, body, review content, workflow log, API response body, runner arguments or credentials. All repository identities are drawn from the canonical projects.json GitHub HTTPS allowlist; CLI parameters cannot name arbitrary repositories. The reader makes at most 30 HTTP GETs across up to three selected projects, and stops all further requests after GitHub authentication/rate-limit denial (401/403/429): up to five pages of ten verbose PRs, two pages of forty Actions runs, and two pages of twenty SHA-bound canonical-main commits per project. Source responses and the local registry have byte caps. The output explicitly marks when a source page cap truncates the older history; even a short --max-events result alternates PRs and CI so Actions volume cannot hide every PR.

Kinds: pr_created means created, not merged; pr_updated means changed, not accepted; pr_merged means merged_at exists and includes an explicitly **reported** GitHub target branch (reported_main_merge for target main), but by itself is **not** verified reachable on canonical main; an explicit `main_merged=true` is emitted only when GitHub's exact PR merge SHA also appears in the bounded canonical main history **and** the matching `main_commit` event survives the final exported receipt; ci_run provides GitHub Actions status, conclusion and SHA, not deployment or worker resumption; main_commit is the SHA of a commit returned from GitHub's canonical main-history endpoint (main_merged=true), not live deployment. No private commit messages, authors or titles are retained.

Coverage remains partial_sources even on successful GitHub reads because branch-only commits, all pagination, local live runtime and cross-provider deduplication are not fully synchronized. Failed and partial pages become explicit partial_error or unavailable states; never infer there was zero activity. The digest validates a strict receipt and renders a bounded, metadata-only human handoff. No HTTP server is started.

### Product issues: second existing receipt in the same Activity screen

Use the existing zSentinel product-issue inbox source, not a new GitHub action or queue writer:

    python3 scripts/zsentinel_product_issue_inbox.py --project cloud --project supa --format json > /tmp/zcloud-product-issues.json

On the Activity tab select **Product issue inbox → Import a JSON product issue-inbox receipt**. The browser shows P0/P1 issues, existing referenced open PR candidates and links beside the separate GitHub PR/CI timeline and local VPS event list. Both JSON files are read in memory only and may be cleared independently. The same project selector filters imported issue candidates.

The dashboard focuses on P0/P1 by default, with an explicit option to include lower-priority sampled issues; it displays each source's oldest/recent/open-PR coverage including truncated, degraded and unavailable windows so missing GitHub access never looks like an empty backlog. The issue reader validates canonical known project + Zennay repo identity, numbered issue and PR identity, QA-only exclusions and all non-authorizing flags. It derives all links from canonical project/repo/IDs and never treats an issue/PR mention as a proven fix, ownership lease or permission to act. It neither writes the receipt to the VPS nor takes any claims. The source is bounded and may be rate-limited or incomplete. Details of the issue receipt itself are in [ZSENTINEL_PRODUCT_ISSUE_INBOX.md](./ZSENTINEL_PRODUCT_ISSUE_INBOX.md) (PR #1539, independently gated).
## Integration boundary

The central server and /api/activity are **not** modified here. Central eventstream, runtime/writer ownership, canonical main integration, release gating and worker scheduling stay with existing owners (#580/#1089, #776/#777 and #1519). A source-only PR or successful local syntax/build check is **not** a live feed repair. Before automated live GitHub synchronization, the integration owner must reconcile GitHub/local event identity, per-project fair paging/cursors, branch coverage, private-repo access, sync budgets and exact-main provenance. Do not replace the existing local events or mark the UI fully live from these partial source observations.

No QA/regression suite is introduced or manually executed under the 17 October product-delivery instruction. Existing mandatory GitHub-hosted CI, branch protection and production review remain intact.


## 20 October — Corroborate PR merges in a bounded main history (source integration)

PR `merged_at` and the reported target branch are still only GitHub PR metadata. The observer now reads the canonical `commits?sha=main` window and marks `main_merged=true` on a PR only when `merge_commit_sha` exactly matches a commit returned from that canonical main history **and a matching main-commit event is present in the same final exported bounded receipt**. GitHub commit reachability is **not** proof of a production deploy, worker restart, runtime source update, acceptance or project completion.

If the canonical history is unavailable, outside the bounded page range or truncated out of the exported event cap, the PR stays `main_merged=false` with `main_merge_evidence=main_sha_not_in_exported_window` or `not_proven_in_bounded_history`. This means **unverified**, not “GitHub definitively says it never merged.” The coverage count is computed from positive matched PR-merge events in the **actual final receipt**, not a larger internal candidate list.

The integrated Activity UI, text digest and offline HTML now recognize only positive claims supported by an exact same-project SHA from a separately included `main_commit` event in the receipt. All imported receipts remain **untrusted user-supplied observations**; a correlated SHA in imported text is called *source-matched* and does not provide independent release, review, security or deploy authorization. Receipt validators continue to deny arbitrary `main_merged=true` assertions lacking this exact source combination.

This work modifies the existing [Activity integration PR #1550](https://github.com/Zennay/zCloud/pull/1550) rather than an unrelated original PR. A normal source-branch merge preserved the already accepted canonical issue-inbox files on main and brought the Activity branch up to current main. The browser JS cache pin in `public/index.html` was updated to the actual modified app.js Git blob; no live site, SQLite worker, queue, release or CI job was changed by this feature.


## 20 October follow-up — direct public GitHub option (same API budget)

The existing **Read public GitHub for selected project** browser action now uses its already-fetched three public endpoints (latest 20 PRs, latest 20 Actions runs, latest 15 canonical `main` commits) to identify a **positive recent main match**. A PR merge earns the message “exact merge SHA in sampled GitHub main” only if its verified-looking `merge_commit_sha` value is a lowercase 40-hex SHA, the PR reports target `main`, and the exact same SHA is in the **separate canonical-main API response from the same user-triggered fetch**. If that short main window does not include the commit or one source is inaccessible/rate-limited, the result is strictly “main reachability unverified” — never “not merged” or “deployed.”

The browser continues to use no credentials, `redirect:error`, a canonical Zennay repo allowlist, bounded responses, a single on-demand fetch, rate budgeting and `no-store` caching. The sole cache pin in `public/index.html` now tracks the resulting updated `public/app.js` Git blob, so the new screen is not hidden behind the stale version query string.

These browser observations and any separately imported activity receipts have different trust boundaries: an imported receipt is an operator-supplied claim and must include its **own exact main-commit event** to display even a qualified match; a browser live refresh reads the canonical GitHub main response directly. Neither is production acceptance, required-HEAD CI approval, a review or runtime proof. Work is on the existing **open PR #1550**, not merged to main or deployed.


## Source freshness and Markdown handoff · 20 Oct 2026

The combined Activity timeline now distinguishes **source-observation time** from
**last event timestamp** in the per-project coverage disclosure. A successful
scoped `/api/activity?project=...` read that returned zero events is displayed
as a scoped read with 0 sampled events; an item from the older global 80-row
fallback is never mislabelled as a successful project read. A failed scoped
refresh retains prior events but marks them stale. Project sources not yet read
remain explicitly unobserved rather than silently treated as idle.

Each project shows the bounded local source read time and event cap separately
from the GitHub read time, GitHub source status, and latest GitHub sample event.
When the project registry contains a canonical, validated
`https://github.com/Zennay/<repo>` URL, the coverage table also offers direct
**Current PRs** and **Current CI** links. They open GitHub's current pages
outside the stale local snapshot; no GitHub API call, token, automatic refresh,
new task or worker claim is performed by rendering those links. Arbitrary
registry URLs are not accepted as links.
The combined timeline's **Export timeline .md** now includes bounded
per-project GitHub provider coverage, observed timestamps and stale/unavailable
states, in addition to the existing scoped local VPS section. The export
contains **no tokens, owner claims or release authority**.

The overview's former generic "Recent activity / Full history" copy now reads
"Recent local activity / Inspect source coverage". The same overview panel
now also shows **GitHub projects sampled / total visible projects** and the
number of source observations older than six hours. This is drawn exclusively
from the explicitly imported or manually fetched in-tab GitHub receipt. It
does not claim an automatic GitHub sync, that PRs merged on main or that VPS
deployment was verified. A green `/api/status` or
the presence of a local checkout commit does not imply remote PR/CI sync.
Manual opt-in GitHub source collection, its request budgets, source validation,
protected-main merge proof and all existing release gates are unchanged.
This PR is source-only until normal exact-head CI, independent review,
protected-main merge and authorized VPS deployment; no runtime activation is
claimed.

## GitHub provider holds stay visible across Activity screens (20 October)

The read-only GitHub source may return a bounded `coverage[].provider_admission`
enum in its JSON receipt. The Activity browser importer accepts only the
following safe known values: `none`, `authentication_required`,
`rate_limit_confirmed`, `repository_or_secondary_limit_unknown` and
`global_admission_denied_prior_project`. Older valid receipts without the
field remain importable and default to `none`. An unknown value is rejected
instead of rendered as arbitrary operator text.

A repository-specific 403 with no confirmed provider cooldown cannot erase
independent project evidence. A global admission stop after 401, 429, or an
explicit primary quota exhaustion / `Retry-After` is **not** interpreted as a
healthy read of later projects. These are GitHub source facts only: the
dashboard does not retry, bypass provider limits or grant itself credentials.

The per-project **GitHub evidence** panel, **Project timeline → Project source
coverage**, zSentinel Markdown product handoff and **Export timeline .md** all
show a safe fixed description of the exact observed provider hold. The
dashboard's recent-source coverage summary never treats a provider-blocked
read as complete. Earlier events retained from a blocked project carry the
explicit **prior evidence, not re-confirmed** label, even if their original
event timestamp is recent. A provider hold is not proof that the project had
no work or that a worker stopped; source absence remains **UNKNOWN**.

This is a product UI improvement in existing
[PR #1550](https://github.com/Zennay/zCloud/pull/1550), associated with
[#1518](https://github.com/Zennay/zCloud/issues/1518). There is no
additional background sync, live deployment, workflow dispatch, VPS mutation,
worker-claim acquisition, independent review or protected-main merge implied.
The exact-head mandatory CI/review, approval and release gates remain intact.


## Canonical project source identity and lossless partial imports (20 October)

The existing Activity UI now binds **every imported GitHub Activity and
P0/P1 issue receipt** to the current canonical project registry. Matching the
shape of `Zennay/<repo>` is insufficient: its repository must equal the
GitHub URL registered for the named project. GitHub Activity also checks
that the requested project set matches the declared project coverage exactly.
A mismatched, duplicate or unknown scope is rejected as an invalid import
**before** replacing any current browser observation. These receipts confer
no owner claim, release permission, CI approval or runtime authority.

A separate source-loss bug affected partial GitHub updates. If a manual
public read returned one or two of the three PR/Actions/main endpoints, the
UI formerly placed earlier observations before newly fetched observations
when merging identical event identities. The older values could therefore
win. The repaired reducer places **newly observed provider facts first**,
and keeps only older event IDs not reobserved, tagging each as
`staleUnverified`. Earlier events never count as newly confirmed facts in
the 24-hour delivery pulse. They remain accessible as historical references
in the GitHub evidence panel, combined Activity timeline and Markdown
handoffs, with the source limitation explicitly visible.

Manually imported offline `zcloud-github-activity-readonly-v1` receipts have
the same principle: a partial/error or unavailable new source does **not**
erase previously observed PR/CI events for the affected project. Newly
observed events supersede matching old identities, while remaining old
events are marked historical and the project's source coverage is explicitly
incomplete. A complete newly fetched bounded sample can still replace
prior project samples; absence from a bounded sample is never proof that
no GitHub work occurred outside its window.

This is local read-model behavior on existing
[PR #1550](https://github.com/Zennay/zCloud/pull/1550) for
[issue #1518](https://github.com/Zennay/zCloud/issues/1518).
The browser does not fetch additional URLs, mint a claim, bypass GitHub
provider admission, trigger CI, merge a PR or deploy a worker. Existing
mandatory exact-head CI/review, branch protection and owner-controlled
production release remain unchanged.


### Trusted VPS GitHub source uses the same conservation rules

An explicit authenticated **Read VPS GitHub** in the Activity page now runs
its validated `zcloud-github-activity-readonly-v1` source through the same
per-project history merger as the offline JSON import. An incomplete server
receipt cannot silently clear previously visible PR/CI events, nor can an
older server response replace a newer scoped source. New GitHub evidence for
an existing identity supersedes the old entry, while un-reobserved old
entries are labelled historical instead of being promoted to fresh
evidence. The server endpoint and token boundary are unchanged; this is
only client-side display and retention behavior.


## 21 October — manually page older public Activity observations

The Activity tab's **Read public GitHub for selected project** still fetches only
three bounded, read-only public GitHub endpoints by explicit operator click.
For busy projects the first page omits older PRs and Actions. After a complete
first-page read, **Read older GitHub page (2/5)** becomes available. Each click
reads exactly one further page of 20 PRs, 20 Actions runs and 15 canonical
main commits, capped at page five. The existing two-minute per-project cooldown,
8-second request timeout, response size bounds and credential-free browser GETs
apply to every page. A failed or partially inaccessible page is marked partial
and does not advance the page cursor or erase earlier observations.

**Newer loaded page** and **Older loaded page** navigate already-read local
windows without a network call. Previous windows stay in tab memory within
the existing conservative 200-event/portfolio presentation cap; an older event
that falls outside that bound is not proof of inactivity. Page-specific
views intentionally do not call a later observation `new GitHub work` merely
because it was absent from the earlier page. Re-reading the newest page
replaces the older-page sample for that project; **Clear snapshot** clears the
in-memory page cursor as well. These controls work only with one selected
canonical project; the unfiltered all-project Activity still never launches
an unbounded API crawl. Snapshot timestamps and source coverage remain partial.

This feature adds no scheduled fetch, GitHub write, background sync, secrets,
workflow retry, claim action, merge authority or production deploy.

### 21 October continuation — per-source visibility and independent source windows

The manual public GitHub page control now shows separate metadata-only status
for **PR, Actions and main** on the requested page: unreadable, bounded full
page, or bounded window ended, with observed row counts. A successful response
with fewer than 20 PRs, fewer than 20 runs and fewer than 15 commits stops the
**Read older page** control rather than offering empty pages; a full page
allows the operator to request another bounded page, but does not imply any
additional content must exist. Failed/partial requests do not move the cursor.
No hidden reads, provider body or token is shown. Source status is not a
statement about the completeness of GitHub history, private data or branches.

A separate **Read VPS GitHub** or manually imported JSON receipt now resets
any public per-project page/view cursor for the affected scope. Earlier
independent sources may still be preserved by the safe receipt merger, but
cannot be hidden accidentally merely because the browser's last public page
selector pointed at page 2 or 3. This is display isolation, not a provider
merge, claim handoff or live-recovery proof. All source windows remain
bounded and never authorize repair, merge, deploy or operator impersonation.

### 21 October operator handoff — actionable GitHub admission

Activity's scoped **combined timeline Markdown export** now carries the same
per-endpoint bounded window observations as the on-screen GitHub panel: PR,
Actions and canonical main page, fixed enum status, numeric sampled count,
manual page number and the next safe human step. The operator can see whether
an explicit next-page request is meaningful or GitHub admission must be
reviewed first. This handoff does **not** represent a separate live provider
check, continuous observation, guaranteed completeness, independent PR review
or proof of deployment.

Public browser requests also label their fixed, non-sensitive failure classes
per endpoint: HTTP 429 confirmed throttling; 403 permission/secondary limit
unknown (never automatically assumed to be global); 401 authentication required;
404 source inaccessible; other network/provider errors unknown. The browser
never includes GitHub response bodies, raw transport exception messages,
credentials or untrusted source text in these status labels. All incomplete
receipts preserve earlier sampled facts explicitly as stale/historical.

Source-level syntax was parsed without running any QA/regression suite; a
source commit or parse check is not exact-head CI, owner approval or production
verification. Merging and releasing remain independent protected operations.

### 21 October — preserve busy GitHub pages and respect provider-wide throttling

A bounded multi-project Activity sample now shares its **200-event display budget**
across observed projects, explicitly loaded browser pages and PR / Actions /
canonical-main categories. A chatty newest page cannot independently consume
every entry while leaving the requested older page blank. Retained events still
carry source timestamps and immutable identities; dropped events remain
unknown, not proof they never happened. The page control shows the retained
count and continues to disclose that the sample is bounded.

The unauthenticated manual browser reader honors GitHub 429 and GitHub 403
**only if** the latter contains a confirmed rate-limit signal
(`X-RateLimit-Remaining: 0` or `Retry-After`). Its strictly capped global,
in-memory pause applies to subsequent reads across projects (between two
minutes and one hour, never persisted). A plain ambiguous 403 remains scoped
to that source and does not assert all projects are inaccessible. The panel
makes the global pause visible, disables manual public reads during the
cooldown, and preserves existing observations; it neither sends a retry nor
starts a background scheduler. Response body and raw rate headers are never
shown or committed. The existing per-project two-minute backoff and bounded
request policy remain in place.

These behaviors are on the existing Activity product PR #1550 only. They
confer no authorization for recovery actions, protected merges, checkout
writes, credential access or production deployment.

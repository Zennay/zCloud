# Autonomous blocker recovery contract

This document specifies the intended scheduler behavior; it does not claim the runtime already implements it.

## Recovery lifecycle

1. Detect stalled work by CI terminal failure, unchanged failure fingerprint on repeated attempts, or expired worker heartbeat/lease. Distinguish ordinary queued CI from a failure.
2. Deduplicate incidents by project, root task, failing workflow and normalized failure fingerprint. Assign one owner and a recovery deadline. Do not steal an active lease.
3. Classify safe automatic action: retry transient failures with exponential backoff and a bounded budget; for deterministic code/configuration failures route a single root-fix task to an authorized owner. Never weaken CI or security checks.
4. Park only the dependent task while routing unrelated ready tasks to workers with healthy capacity. Avoid creating child PRs against blocked parent branches.
5. Reconcile the original task on terminal CI state: green means review/merge eligibility under existing gates, red means root-fix, not blind retries. Record resulting-main verification after merge.
6. Escalate to a human only for required permission, an irreversible/high-risk decision, or exhaustion of safe recovery routes. Never auto-grant sudo or bypass required review.

## Minimal incident payload

- **Problem:** project and one-sentence cause, with evidence link.
- **Action:** current automatic recovery step, owner and deadline.
- **Status:** recovering / waiting / needs your input / resolved.

The dashboard and audit show the same current incident state, highest impact first; Wear OS alerts only for actionable human escalations. Historic resolved incidents must not appear as active blockers. Suppress duplicate alerts and report recovery outcomes.

## Capacity guardrails

Throttle admission of heavy jobs on sustained memory pressure, swap saturation, or runner unavailability; do not terminate running jobs solely on a single load sample. Resume conservatively when resources recover. Preserve self-hosted runner boundaries and least privilege.


## Executable policy explainer (advisory only)

scripts/zsentinel_recovery_policy.py turns one bounded, operator-supplied incident receipt into a deterministic HOLD or READY_FOR_HUMAN_REVIEW explanation. It is deliberately separate from the recovery router: the router diagnoses incidents and suggests independent work; this command explains whether a specific proposed action has the minimum policy evidence. It performs no network I/O and never executes the proposed action.

    python3 scripts/zsentinel_recovery_policy.py --input incident.json --format markdown

Use --input - to read JSON from standard input, --format json for a machine-readable receipt, and --max-age-minutes N to set an explicit freshness window (1–1440; default 60). The input must be UTF-8 JSON, at most 256 KiB, with no duplicate keys, unknown properties, symlinks, or non-standard numeric constants. Source URLs must use HTTPS on github.com or the approved Notion hosts. Raw whitespace and control characters (including newlines and tabs) are rejected so an operator-supplied URL cannot break a Markdown handoff line or inject additional formatting. This check is applied by both direct evaluation and template generation before the source URL is echoed into JSON or Markdown. Query strings and fragments are stripped from the emitted source link because they are not needed to identify the issue/PR page and may contain access tokens or tracking data.

Supported proposed actions:

- retry_ci: terminal red exact-head CI, classified transient failure, remaining numeric retry budget, elapsed backoff, clear claims, healthy capacity and owner authorization.
- route_root_fix: deterministic failure, confirmed root cause, named owner, no active/unknown claim and owner authorization.
- assign_independent_work: explicit ready task, positively established disjoint scope, no active/unknown claim, healthy capacity and owner authorization.
- park_blocker: park only one dependent task when its blocker is confirmed external and unchanged; require a clear claim state, an authorized owner, healthy capacity and separately identified disjoint ready work.
- resume_blocked: consider returning one exact preserved blocked queue item to normal queue selection only when a fresh source confirms the dependency is cleared, the previous blocker/evidence remain preserved, an explicit operator attests to resolution, no active/unknown claim exists and a named authorized owner approves. This action does not force a worker; normal queue capacity and lease checks remain in force.
- escalate_human: route to a named human only for a specifically evidenced permission requirement, irreversible/high-risk decision or exhausted safe recovery routes; unknown claim state remains HOLD.
- merge_pr: verified exact head, green required CI, independent approval, satisfied branch protection, zero unresolved review threads, verified not-yet-merged state and owner authorization.
- deploy: exact-head CI/review/protection, verified merge on main, healthy post-merge state, release approval, runtime-owner authorization and healthy capacity.
- restart_runner: verified unavailability, no running jobs, confirmed cause, runtime-owner authorization and explicit human approval.

For `resume_blocked`, the request must also carry a bounded `blocked_task_id`, `dependency_cleared=true`, `dependency_unchanged=false`, `blocked_task_preserved=true`, `previous_blocker_preserved=true` and `operator_attestation=true`. The policy receipt adds `authority.queue_resumed=false` like all other actions: even READY_FOR_HUMAN_REVIEW is advisory and does not change queue state.

The evidence is supplied by the operator and is not independently re-fetched or verified. Missing, stale, contradictory or adverse evidence yields HOLD. A clear result means only READY_FOR_HUMAN_REVIEW; it is not a grant of authority, merge approval or deployment permission. GitHub's actual exact-head checks, branch protection, review state and the responsible runtime/release owner remain authoritative.

Every result explicitly records advisory_only=true, evidence_independently_verified=false, actions_executed=false, and false claim/retry/merge/deploy/restart side-effect flags. The tool cannot acquire a claim, retry a workflow, merge a PR, deploy or restart a runner.


### Batch triage for independent recovery proposals

Use `--batch-input` to review **1–25 separate, complete policy requests** in one operator pass:

```bash
python3 scripts/zsentinel_recovery_policy.py --batch-input incidents.json --format markdown
```

The input must be one UTF-8 JSON array, within the same 256 KiB input bound. Every item is validated against the exact single-request schema; duplicate JSON keys, duplicate `(project_id, incident_id, action)` entries, unknown fields or any invalid request fail the entire batch closed rather than emitting a partial report. Source-age decisions use one shared evaluation-clock snapshot. JSON output includes each individual policy receipt plus bounded HOLD/ready counts; Markdown output groups each request with its own blockers and required evidence. In both single-request and batch Markdown, the source URL is HTML-escaped inside a literal code element so URL path characters cannot inject Markdown links or formatting.

### Incident-level route conflict protection and operator desk

When an operator supplies different proposed actions for the same `(project_id, incident_id)` in one batch, the policy evaluator treats these as **competing alternatives**, not separate approvals. Every proposal for that incident receives a `competing_incident_routes` HOLD and the required `incident_route_selection` field. The owner must deliberately select one route and submit an updated, freshly observed request. Other independent incidents continue to receive their own decisions; a gate HOLD affects only the relevant incident. Exact duplicate `(project_id, incident_id, action)` entries still invalidate the entire batch, as before.

Batch JSON now includes `incident_count` and bounded `incident_triage`, sorted HOLD first. Each incident group contains the original proposal indices, all alternative action labels, deduplicated blocker codes and missing-evidence paths, sanitized source links, an **unverified** operator-supplied owner hint, an owner-hint-conflict flag and one explicit **human** next step. These source-local hints are not real-time worker claims, a definitive source-of-truth owner, or proof that any action is authorized. Every incident card explicitly disallows claim/merge/deploy.

Batch Markdown starts with the same incident worklist, followed by the existing detailed per-proposal receipts. This lets an operator choose a single route and immediately see which unrelated incident can continue while another waits for CI, approval or capacity. A `READY_FOR_HUMAN_REVIEW` outcome remains advisory; only GitHub and the actual owner can establish execution permissions.

Batch mode remains advisory: it does not fetch or independently verify sources, acquire claims, retry CI, merge, deploy or restart runners. A valid evaluation returns exit code `0` by default even when its decision is `HOLD`, matching single-request mode. Add `--fail-on-hold` when a calling workflow explicitly wants exit code `2` if the single result or any batch item is `HOLD`; malformed input and output-bound failures always return `2`. The flag is unavailable in template mode. A ready result is never an approval to act.

### Callable API admission

The Python entry points enforce their own policy bounds as well as the CLI: `evaluate(..., max_age_minutes=...)` accepts only an exact native integer in **1–1440**, and `template_request(action, ...)` accepts only a registered action. Passing booleans, floats, out-of-range ages or an unknown action fails closed with `PolicyInputError`; callers cannot bypass CLI validation by importing these helpers directly.

### Start from a fresh request template

Generate a schema-complete request with every evidence field set to unknown, rather than hand-writing the JSON shape. The timestamp is generated at invocation time.

    umask 077
    python3 scripts/zsentinel_recovery_policy.py --template merge_pr --project-id zCloud --incident-id pr-1562 --source-url https://github.com/Zennay/zCloud/pull/1562 > incident.json

Edit incident.json using only evidence actually observed. Leave unavailable fields null; the evaluator will hold the action. Then render the decision:

    python3 scripts/zsentinel_recovery_policy.py --input incident.json --format markdown

The template command validates the project/incident identifiers and source URL. It does not assert that the source is current, that any check is green, or that an owner has approved an action. Regenerate or refresh the observation timestamp immediately before evaluation; the default freshness window is 60 minutes.

### Incident-owner workorders and conflicting proposals

An operator can now get a **three-line problem/action/status handoff** for the
same 1–25 bounded, source-validated policy requests without opening every
individual JSON receipt. This is an **advisory queue, not worker assignment**:

```bash
python3 scripts/zsentinel_recovery_policy.py --batch-input incidents.json --format workorders
```

- Each `(project_id, incident_id)` produces one incident-level owner workorder.
  JSON batches expose `incident_count` and `owner_workorders`; Markdown
  batches put the compact handoff before the full per-proposal evidence.
- If one batch proposes different actions for the same incident, **every**
  proposal becomes `HOLD` with `competing_incident_actions`, and the single
  workorder says `select_one_authorized_action`. This prevents two separately
  policy-eligible routes being presented as concurrent permission. A repeated
  identical `(project_id, incident_id, action)` still rejects the whole batch.
- Workorder `required_evidence` aggregates the **exact missing evidence fields**
  from all incident proposals; compact text surfaces the first four fields and
  the offline cockpit shows the full list in its details section.
- Workorder `next_step` is `resolve_evidence_holds` for ordinary HOLDs or
  `verify_live_gates_and_obtain_owner_approval` for locally ready proposals.
  The owner is UNKNOWN unless the input identifies one consistent owner.
- The order groups proposals by **intervention class** (merge review, runner
  recovery, root fix, bounded CI retry, release, escalation, blocker parking,
  independent work). It is *not* a severity score, P0 prioritization,
  authorization or evidence of runtime capacity. Existing actual issue
  priorities, active claims and required CI/review gates remain authoritative.
- Workorder source URLs are only the validated, sanitized supplied links;
  independently verified remains false and no claim, CI retry, merge, deploy
  or runner action occurs. `--format workorders` is batch-only.
- `--fail-on-hold` also detects collision-induced HOLDs and returns exit 2
  after emitting the complete local handoff.

This local policy gate complements the runtime incident router in PR #1563,
but does not claim it was merged or connected to a live scheduler.

### Offline incident cockpit (no JavaScript)

The same owner handoff also renders as a responsive, **offline static HTML page**,
without loading scripts or third-party assets:

```bash
umask 077
python3 scripts/zsentinel_recovery_policy.py --batch-input incidents.json --format html > private-zsentinel-workorders.html
```

The cards display per-incident **problem / next action / HOLD-or-local-ready
status**, with a collapsed list of sanitized source receipts. Each card always
states that observations were supplied locally and have not been independently
verified. CSP forbids scripts, network assets and forms; the page has no buttons
to execute recovery actions. Keep operator exports private (`umask 077`);
do not publish source receipts or owner names to public dashboards.

`--format html` is batch-only. The normal output cap of 128 KiB remains in
force, even when many incident cards are selected. Actual P0/P1 urgency,
self-hosted headroom, lease authority and GitHub merge gates must still be
checked in the live authoritative systems.

### Consume the unified zSentinel Activity recovery case (private)

The existing Activity product PR #1550 can export a bounded
`zsentinel-portfolio-recovery-case-v1` JSON file with P0/P1 issue references,
queue retry workorders and incomplete source coverage. This advisory policy CLI
now accepts that file directly:

```bash
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json --format html > private-case-handoff.html
```

`--format workorders` renders three-line problem/action/status cards,
`--format markdown` generates a short text handoff, and `--format json`
emits bounded structured `zsentinel-recovery-case-handoff-v1` output.
`--fail-on-hold` returns exit code 2 when observed issues or queue incidents
require owner attention; it **does not trigger** automatic recovery. The tool
remains entirely offline and applies its existing private, non-symlink,
256 KiB input and 128 KiB output boundaries.

A case is accepted only with its expected schema, explicit false authority
flags, bounded P0/P1 issue IDs/URLs, up to three canonical PR reference URLs
per issue, internally consistent project scope, bounded queue group totals,
and explicit source coverage. Malformed, contradictory, duplicate or
authorizing inputs are rejected rather than partially emitted. A case with
**no issue/queue workorders is rejected**, even if an incomplete source happened
to report no sampled problems: such a case cannot prove a clear production
backlog. Sources older than six hours are called out; a stale queue source
requires refresh *before* owner verification. Every actual issue/queue
workorder is an unconditional `HOLD`; no locally imported evidence can make
it `READY_FOR_HUMAN_REVIEW` or acquire an owner.

### Source identity and queue-clock admission

The private recovery-case importer binds every referenced repair PR to the
**same canonical GitHub repository as its issue**. Cross-repository or duplicate
PR references are rejected as ambiguous evidence rather than presented as
existing fixes. Neither a matching repository nor an imported PR link proves
current PR ownership, green checks or merge eligibility.

A covered queue sample must have a valid UTC observation timestamp no later
than two minutes after the export was generated. The importer independently
marks queue evidence **STALE** once the queue observation or overall recovery
case exceeds six hours, even if the export supplied `source_stale=false`.
Its own source-age calculation only adds HOLD reasons; a caller-provided stale
flag can never be used to turn old evidence into fresh permission to retry.
No live queue is read, no active claim is modified and no recovery action is run.

The structured case report now carries the original case export UTC, observed
queue UTC (or UNKNOWN), source-case-stale flag, and effective queue-stale flag.
HTML and compact owner workorders show **the case, issue and queue source clocks**
and the later local evaluation time. A newly rendered handoff can no longer describe an old
offline import as a freshly observed production state. Raw queue task/worker
identifiers remain unavailable and there are no executable controls.

A second integration boundary consumes the Activity exporter's source-coverage
fields. The issue sample count must exactly match exported issues and both the
issue sample truncation and queue sample completeness flags must be actual
booleans. Cases with missing or contradictory coverage are refused; valid
but **partial** issue/queue samples remain available as HOLD-only cards with
explicit `issue_sample_truncated`, `queue_source_incomplete` or
`queue_sample_truncated` problem codes. An old or absent issue observation
is labeled `issue_source_stale_or_unknown`. Affected cards now direct the owner
to refresh that source before evaluating claims and existing PRs; complete,
fresh independent project groups remain visible. This imports existing source
metadata and does not add provider reads, runner restarts, workflow dispatches,
queue mutations or permission changes.

### Actionable private owner handoffs

The operator's existing Activity recovery-case import has a direct,
bounded `--format csv` output in addition to JSON, HTML, Markdown and
three-line text. It produces **one row per P0-first incident**, never one
row per approval or runnable worker task:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json --format csv \
  > private-owner-workorders.csv
```

The same `--format csv` works with `--batch-input incidents.json`
(where priorities are UNKNOWN because operator-supplied policy requests do
not have independently established issue severity). Every CSV row carries
the review action, HOLD reasons, missing owner evidence, canonical original
issue URL and already-referenced candidate repair PRs, **unverified** source
receipts, the distinct original source/evaluation UTC clocks and explicit
false verification/claim/merge/deploy authorization columns. CSV fields are
properly quoted and all output modes retain the existing 128 KiB limit.

The human-facing plain-text owner list now leads with the observed P0/P1
priority and includes canonical original issue and candidate repair-PR links.
The separate `--case-input --format markdown` now produces a proper,
source-linked P0-first private owner handoff with **three concise lines per
incident (problem, human action, HOLD status)**, explicit original case /
issue / queue UTC clocks, and the still-UNKNOWN live owner. An old export
can never imply a live merge-ready candidate. For example:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json --format markdown \
  > private-recovery-handoff.md
``` The offline HTML labels these candidate PR
links explicitly and displays the original priority on each incident
card.

### Existing PRs first: linked-repair routing

The imported Activity recovery case now carries the actual bounded *historical*
issue-to-PR candidate data into the private owner worklist. Only the canonical
same-repository PR references from the validated source are considered (up to
three per issue). In addition to the issue URL, each candidate includes:

- PR number and original canonical URL;
- `draft_at_source` and a strictly admitted `relationship_at_source`
  (`closing_keyword_only` or `text_reference_only`);
- explicit `current_pr_state_verified=false`,
  `current_claim_verified=false`, and `merge_authorized=false`.

The importer refuses contradictory `link_status` and draft-reference
metadata rather than choosing a fictional repair owner. For the same P0/P1
priority, issues with a sampled **non-draft** PR reference are examined first,
then draft-only issues, then issues without an observed candidate. This is a
*source-linked review ordering*, not proof that a PR remains open, ready,
tested, approved, product-only or unclaimed.

The next owner action now differentiates **inspect existing non-draft PR and
exact-head CI/review**, **finish existing draft with authorized owner**, or
**look for another existing product PR before creating new work**. When the
issue source is old, uncertain or truncated, refresh-first still wins.
No linked PR is automatically completed, merged or assigned. Existing exact
head, CI, review, branch protection and owner verification remain mandatory.

The resulting JSON contains `candidate_prs`, `pr_source_triage`, and a
bounded `sampled_existing_pr_triage` summary. The focused historical view
recomputes its candidate counts instead of presenting the whole original
case's counts as though they came from the filtered slice. HTML, Markdown,
text and CSV expose the sampled draft and relationship hints with explicit
"at source" and "verify live" wording.

### Issue to existing PR: private HOLD-first merge review worksheet

To continue an already referenced PR without inventing a new work item, a
human operator can take the **existing historical case and one sampled PR**
directly into the policy evaluator's canonical `merge_pr` evidence schema:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1518 \
  --prepare-merge-review --candidate-pr-number 1550 \
  > private-pr-1550-merge-review.json

python3 scripts/zsentinel_recovery_policy.py \
  --input private-pr-1550-merge-review.json --format markdown
```

The numbers above are **an illustration of the command shape**: the
specified issue and PR must actually be present as a valid same-repository
reference in the selected source. The command never discovers or assumes
a current PR owner. Missing candidates, ambiguous cross-project issue
selection and unknown issue source timestamps fail closed. Only original
`--case-input` data may be used with `--prepare-merge-review`; a
`--view-project`, `--view-issue` and `--candidate-pr-number` are all
mandatory, and this export only accepts JSON format.

The generated JSON is a **normal bounded single-policy request**, not a
GitHub-ready merge instruction. Its `source_url` is the sampled exact
canonical PR URL and `observed_at_utc` preserves the **original issue
source observation** rather than stamping a stale source with the current
clock. Every evidence field remains `null`, the owner remains `null`,
and `source_verified` is never manufactured. The subsequent policy
evaluation will therefore **HOLD** until a human checks fresh authoritative
GitHub PR state, exact-head required CI, independent review, unresolved
threads, branch protection, ownership and active claims. The operator may
complete that separately verified worksheet, but this tooling itself
never invokes merge, retry, claim or deploy.

### Current PR observation for one historical issue (read-only)

The existing case-import CLI can now follow a selected issue's **already
sampled** PR candidates with `--live-candidates`, rather than leaving an
operator with a stale URL and no current PR HEAD:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1518 --live-candidates \
  --format markdown > private-live-pr-1518.md
```

Set an existing `GITHUB_TOKEN` **environment variable**, if needed for
private repositories; otherwise the adapter uses the unauthenticated public
GitHub API. Never place a token in command arguments, source export,
Markdown or CSV. The adapter performs only canonical HTTPS
`api.github.com` GET requests, refuses redirects, imposes response and
request bounds, and never starts Actions or calls merge/claim/deploy endpoints.

The observation scope is **one exact project/issue and at most three
canonical, same-repository PR links** already validated against the
original Activity case. A first bounded, canonical GitHub **issue GET**
now refreshes only the original issue's current open/closed state. A
closed issue is *not* automatically deemed fixed, merged, or free of an
owner lease: the desk instead directs a human to verify the closure and
existing repair before reopening any product work. If that issue GET is
denied, the state remains UNKNOWN; admission/rate-limit failure also
stops further GitHub reads in the same bounded collection. The same command also supports a portable,
script-free offline HTML operator desk:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1518 --live-candidates \
  --format html > private-live-pr-1518.html
```

The HTML has a restrictive Content-Security-Policy, escaped labels,
a prominent **suggested existing-PR owner action list**, direct canonical PR
links, **direct per-HEAD checks and changed-files links**
for a stably observed PR, expandable sampled check/review evidence,
individual links to at most six observed failing and six queued/pending
check runs, and clear UNKNOWN/HOLD signals. It distinguishes **sampled
failing HEAD checks** (investigate the actual source failure before
merging), **sampled waiting HEAD jobs** (investigate runner/queue admission),
an existing draft, a non-main target, closed unmerged PR, and a reported
merged PR (verify actual main merge commit). These are bounded first-page
signals and never replace the complete protected-branch required checks
or independent review requirements. These links facilitate legitimate review,
not an automated CI-pass or merge decision. No remote assets, client-side API requests,
tracking scripts or external style resources are included.

Each candidate has a bounded current PR GET,
a sample of at most 30 exact-HEAD check runs, at most 30 reviews, and a
second PR GET to detect an intervening HEAD/target/state change:
**at most 13 GETs in total** (one original-issue read plus four per
referenced PR). If an initial PR read or the final HEAD/state re-read fails, the
candidate remains `UNKNOWN_PROVIDER`, not absent or merged. If the core
PR source remains readable but a **secondary** check-run/review endpoint
fails without an account-admission denial, the second PR read still
retains the verified current PR HEAD and marks the specific check/review
source explicitly UNKNOWN; the remaining release gates do not become
green. GitHub 401/403/429 admission denial stops attempts for the
remainder of that one bounded source collection rather than repeatedly
hammering a blocked provider. JSON, Markdown and HTML disclose
`get_attempts`, UNKNOWN candidate counts and secondary-source gaps. A PR that
moves HEAD or changes draft/merge state during collection is not treated
as a stable, current observation.

JSON, Markdown and the portable HTML desk reveal the actual observed PR HEAD, open/closed,
draft, reported merge, and the **sampled** check-run states/review
categories. Required-branch checks, branch protection, independent review
approval, current worker/Notion owner and merge eligibility stay explicitly
`UNKNOWN` even when a sample looks green. The report marks
`action_executed=false`, `merge_authorized=false` and
`claim_authorized=false`; it cannot fill in or override the normal
exact-head policy worksheet. GitHub 401/403/429/404, API changes and private
access denial cannot silently clear a P0/P1 problem.

For example, an operator can inspect the live PR observations first,
then separately generate a **HOLD-first** review worksheet using
`--prepare-merge-review` and obtain the current required CI, review,
protection and ownership evidence from authorized sources. These are
two deliberately separate steps; the live read is not a new gate and
does not auto-merge any PR.


The imported historical issue export timestamp, issue observation time,
sample-truncation state and whether the original source was stale are
retained separately from the new live GitHub GET clock. A live PR read
**does not renew historical issue→PR linkage evidence**. The JSON
`historical_case_provenance` and `current_issue` keep both types of
evidence distinct, with active claims and release permission still false
or UNKNOWN. The optional ordered `suggested_owner_review_order` is a
small, non-executable review shortlist, **not** a task queue or dispatcher.

### Source-bound P0 repair prioritization from actual read-only GitHub observations

The live owner desk now creates a `suggested_owner_review_order` over the
**same maximum three already-linked PRs**, rather than simply repeating
the original historical link order. It distinguishes:

- An open main-target non-draft PR with a **sampled terminal failing** or
  timed-out check on its observed exact HEAD: investigate the linked
  **sampled check run** and fix the actual product failure before any merge.
- An open main-target non-draft PR with sampled queued/in-progress checks:
  inspect the linked waiting job and the existing runner/provider-admission
  incident before retrying or assuming CI is green.
- Missing check-read permission, an existing draft, an unexpected target
  branch, a closed unmerged PR, or a PR reported merged: distinct
  owner-facing next actions, with actual `main` merge evidence still
  independent and mandatory.

Every check URL is built from a bounded numeric ID in the already admitted
repository. The first-page check-run observations (up to 30) retain no
more than six failing and six pending links in the UI. Check-run
`status=completed` with a neutral/skipped conclusion is **not**
misrepresented as terminal failure. All per-check requiredness,
`required_ci_verdict`, `branch_protection_verdict`, review
independence and merge authority remain UNKNOWN/false. The original
historical candidate order is preserved in `observations`; the separate
owner list is only an advisory priority projection, never a new worker
queue. PR `target_branch` now participates in the double-read drift
guard, so a branch retargeted during inspection is UNKNOWN.

A single **live issue GET** is made first for the original canonical issue,
then at most four PR GETs per sampled candidate: 13 GET requests total.
A GitHub 401/403/429 response stops this bounded collection. A currently
closed issue prompts a human to review the closure and repair before
restarting work, but neither closure nor a PR's `merged=true` implies
that any specific protected-main commit is deployed or that a worker
lease may be released. The observed issue status and import-era source
coverage are separate JSON fields.

As another source-only hint, the already-fetched live PR description is
inspected without publishing its body: the operator sees whether the
description still *mentions* the original issue number, contains a closing
keyword, has no sampled textual issue mention, or has an unknown/oversized
body. A changing PR description during the two PR GETs results in UNKNOWN.
A missing textual reference prompts rechecking the issue→PR relationship
before using that historical PR candidate as a repair. Text mentions,
closing keywords and their absence **never prove or disprove** GitHub
cross-reference links or authorized ownership; no title/body, issue text,
token or private prompt is exported.

### Discover existing PRs before creating new work (explicit opt-in)

When the *validated historical* recovery case has **zero referenced PRs**
for an issue, the normal `--live-candidates` path fails closed. An operator
can explicitly request **one bounded open-PR source window** instead:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1518 \
  --live-candidates --discover-unlinked \
  --format html > private-existing-pr-discovery.html
```

This is only for an issue whose imported `candidate_prs` list is empty;
a case that already knows PR references must use the normal path first.
No new PR is opened. The route reads one canonical GitHub
`/repos/Zennay/<repo>/pulls?state=open&per_page=30` page, examines
the returned PR descriptions **in memory** for the selected same-repo
issue number, and selects at most three candidate references whose text
contains that issue number or an ordinary closing keyword. It never
exports raw PR descriptions or other issues. Other rows are skipped with
a visible incomplete-source count; all returned candidate PRs still use
the normal double-read HEAD/description-drift guard, check/review
source bounds, and unauthorized-action flags.

This opt-in path costs at most **14 canonical GitHub GETs**: one open-PR
list page, one original issue GET, then at most four GETs per candidate.
In a direct read-only source inspection of the current zCloud repository
on 21 October 2026, the first open-PR list page was **1,008,465 bytes**:
larger than the ordinary 160 KiB API-response bound. The actual
discovery-only PR-list GET therefore has a separate, fixed **2 MiB**
ceiling; every individual PR, exact-HEAD check and review GET retains
the original 160 KiB ceiling. This is one page and not a pagination
crawler, and an over-limit response still fails closed as UNKNOWN.
The same current zCloud page contained 30 sampled PRs and three textual
mentions of existing issue #1518 during direct read-only inspection;
these are **candidates, not verified issue links or product fixes**.
The result preserves `source_candidate_count=0` separately from the
up to three newly observed `candidate_count`, and carries
`optional_unlinked_discovery` with the scanned page length, matched
textual references, malformed/skipped rows, truncated candidates,
source completeness `false`, and `existing_repair_absence_verified=false`.
HTML and Markdown explain the same limits. An empty first page or zero
text matches **never prove no existing PR**: GitHub may have older pages,
private/inaccessible PRs, text-free issue cross-references, different
branch targets, or missing activity sources. An authorized human must
still check the real repository PR inbox and active ownership before
starting or assigning new work.

Current open-PR text mention is **not** proof of an approved issue→PR
relationship, PR ownership, exact-head required CI, independent reviewer
approval or protected-main merge. It only provides bounded navigation
toward potentially reusable existing work. GitHub admission/quota errors,
stale issue sources and partial coverage stay UNKNOWN, never READY.

### Project- and priority-focused historical owner views

Use `--view-project <project-id>` and/or `--through-priority P0|P1|P2|P3`
with `--case-input` for a focused *projection of the already imported
source*, without rereading or modifying Notion, GitHub, the worker queue or
any lease. P0-only operator review for one project:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --through-priority P0 \
  --format html > private-cloud-p0-workorders.html
```

The original case may cover `all` or a single project; a requested
project outside its source scope is rejected. An explicit slice with no
matching source observations also fails closed instead of producing a
misleading empty or green backlog. For a single existing issue, add `--view-issue <number>` **together**
with `--view-project`. This selects one source-linked issue card and
its already referenced candidate PRs for an owner handoff:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1326 \
  --format markdown > private-issue-1326-handoff.md
```

Issue numbers are repository-local. Requiring a project alongside the issue
number prevents ambiguous cross-project selection. If the imported historical
case lacks that issue (including because of a truncated source), the selector
fails closed rather than manufacturing a current clean bill of health.
The source-only view explicitly records `selected_issue_number` in JSON
and a dedicated CSV column; HTML, Markdown and text display it as
`project/issue-number` along with hidden historical record counts.

A successful sliced report preserves the
original case/issue/queue source clocks and source coverage, includes a
`source_case_view` JSON field recording the selected project, priority cap,
total original incidents, number hidden, `projection_only=true` and
`backlog_clear_verified=false`. HTML, text, Markdown and CSV disclose
the selected slice and the number hidden. Every retained incident remains
HOLD-only, P0-first, owner-unverified and non-executable. Slice options are
unavailable for normal single-request and batch-policy inputs. All issue/PR links in this case path have already passed the importer
same-repository and bounded-source requirements. They are navigation hints
only: **recheck current GitHub status, exact-head CI/review and active
Notion/worker/queue ownership before acting**.

The conversion ranks P0 before P1–P3, puts issue/PR examination first within
a priority, and requires checking real GitHub source/required CI plus current
Notion/worker/queue ownership. Grouped high queue claim attempts are not proof
of deterministic/transient failures or permission to retry. Output contains no
raw task IDs, prompt content, tokens or active-claim keys. This is an operator
handoff from #1550's *source branch*: it does not assert that either PR
has reached main, that the exporter or importer has been deployed, or that
the current protected-main gate permits any merge.



## 21 oktober · privé-uitvoer voor recovery policy

De recovery-policy CLI kan iedere bestaande JSON-, Markdown-, workorders-,
HTML- of CSV-weergave nu optioneel als **nieuw privébestand** opslaan met
`--output`. Zonder dit argument blijft de bestaande stdout-route ongewijzigd.
De extensie moet overeenkomen met `--format`: `.json`, `.md`, `.txt`,
`.html` of `.csv`.

```sh
install -d -m 700 "$HOME/.local/state/zsentinel"
python3 scripts/zsentinel_recovery_policy.py \
  --case-input /private/zsentinel/recovery-case.json --format html \
  --output "$HOME/.local/state/zsentinel/recovery-workorders.html"
```

De uitvoer wordt begrensd tot 128 KiB, exclusief aangemaakt met mode `0600`
en geflusht naar bestand en directory. De doelmap moet al bestaan, eigendom
zijn van de huidige gebruiker en niet door groep/anderen schrijfbaar zijn.
Bestaande bestanden, symlinks en gewijzigde directory-inodes worden geweigerd;
bij een schrijffout wordt uitsluitend de eigen nieuwe inode opgeruimd. Fout-
meldingen tonen geen privépaden. De uitvoer blijft uitsluitend advies/HOLD:
het bestand geeft geen live-status, claim, retry, merge- of deploybevoegdheid.


## Merge-review packets: JSON, Markdown and HTML

The existing --prepare-merge-review flow can now produce a machine-readable JSON request or a human-readable Markdown/HTML handoff. Select an issue and a candidate PR that are already present in the imported recovery case; this does not discover a new PR or refresh live status.

```sh
install -d -m 700 "$HOME/.local/state/zsentinel"
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project "$PROJECT_SLUG" --view-issue "$ISSUE_NUMBER" \
  --prepare-merge-review --candidate-pr-number "$SAMPLED_PR_NUMBER" \
  --format html --output "$HOME/.local/state/zsentinel/merge-review.html"
```

Use --format markdown with a .md output path, or keep --format json for the policy request alone. The human handoff links the original issue and sampled PR, preserves the issue-source observation timestamp, and provides an explicit checklist for current exact-head CI, independent review, branch protection, unresolved review threads, merge state, owner approval and claim clearance. It is script-free offline HTML with no external resources.

**Merge safety:** the policy evaluator now requires evidence.active_claim = "none" for merge_pr. An active or unknown claim is a HOLD, even when the other fields appear ready. Every request field is left null in the generated template; the saved draft/non-draft label is historical only. The packet does not run checks, acquire claims, merge, deploy or restart a runner.

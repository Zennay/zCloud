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

### Cross-project GitHub issue cockpit (read-only, no saved case)

zSentinel's portfolio role can now triage original issue source across multiple
explicit Zennay repositories in a single operator file, instead of requiring
separate manual project inboxes:

```sh
umask 077
mkdir -p -m 700 "$HOME/.local/share/zsentinel"
python3 scripts/zsentinel_portfolio_issue_desk.py \\
  --repo Zennay/zCloud --repo Zennay/Supa \\
  --view explicit-critical --format html \\
  --output "$HOME/.local/share/zsentinel/portfolio-$(date -u +%Y%m%dT%H%M%S)-$.html"
```

The **canonical** `zsentinel_recovery_policy.py` entrypoint now
also exposes this exact multi-project product workflow without requiring
the standalone script name:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \\
  --portfolio-repo Zennay/zCloud --portfolio-repo Zennay/Supa \\
  --portfolio-view explicit-critical --format html \\
  --output "$HOME/.local/share/zsentinel/portfolio-shared-$(date -u +%Y%m%dT%H%M%S)-$.html"
```

The entrypoint delegates to the same bounded adapter and private writer;
portfolio mode refuses mixed case-, issue-, single-repository, merge-,
batch- or retry-oriented modes, and rejects unsupported output formats
before a remote source read. It does **not** reinterpret the source report
as a policy approval or automatically follow the offered live-PR commands.

The same command supports `--format markdown|json|csv` and `--view all`
(the default), `explicit-critical` (only **label-backed** P0/P1),
or `critical-and-title-hints` (clearly separates unverified title hints
from explicit labels). Filters change only local displayed cards; the
original full bounded source counts, omitted-by-filter and hidden-by-output
numbers remain present. Per-repository source scorecards show actual
observed explicit P0/P1 counts and freshness/partial status, independent
of whether the cards fit the global P0-first display. The portable HTML
groups P0, P1, unverified hints, P2/P3 and unknown source records under
accessible in-page anchors; the grouping never upgrades a title hint to
a verified GitHub label. Each issue card and each exported Markdown,
CSV or JSON workorder also carries the **canonical next existing-PR
inspection command** from the original issue reader:

```sh
python3 scripts/zsentinel_recovery_policy.py \\
  --live-issue-url https://github.com/Zennay/zCloud/issues/1326 \\
  --format html --output /your/owned/private/issue-1326.html
```

This is a human-selected follow-up example and **is not executed** by
the portfolio tool. Any subsequent PR merge must still be independently
authorized from current branch/review/CI/owner evidence, not an inbox
card. The display reports omitted cards separately for the view filter
and the global 120-card ceiling; hidden cards remain part of the bounded
source sample and are not implicitly resolved.

**Source and safety bounds:** 2–6 distinct canonical `Zennay/<repo>`
arguments; one sequential bounded GitHub observation per repo,
maximum 7 GET per repo and 42 overall, no arbitrary remote hosts,
and at most 120 P0-first issue cards in a source-bound snapshot.
The input repo list is explicit rather than inferred from a historical
Notion claim. Observations across repositories are **not atomic**.
Authentication, rate-limit or unknown provider failures are isolated as
`SOURCE_UNAVAILABLE` and stop further API calls for the run; earlier
repository issue cards remain available in the private output. Projects
not read after a global provider error become
`NOT_READ_AFTER_SOURCE_ERROR`, never implicitly empty. An explicit
repository-local not-found/inaccessible GitHub response affects **only that
repository**, so other independently requested projects can still be read
without repeating the blocked repo request. Project source observations
include a human follow-up action (`human_source_followup_code`) and
distinct `source_error_scope` for repository-only versus provider/unknown
errors; follow-ups are never executed by the CLI. The report separates
unread/incomplete provider sources (`source_partial_or_unknown`) from
issues deliberately omitted by the local view/display cap
(`display_partial`); the combined legacy `partial_or_unknown` flag
continues to communicate either kind of incomplete handoff. The boolean
`all_repositories_covered` is true **only when every requested repository
actually produced a source observation**, not merely when a global
provider failure did not occur. Actual GitHub GET count is reported UNKNOWN
when a raised provider error makes attempted-request accounting uncertain,
while the absolute 7-per-repo ceiling remains unchanged.
Source failures do not mean no issues. No raw titles, bodies, contact data or tokens are included;
original GitHub issue URLs and human-only follow-up codes remain present.
All formats require a user-owned private destination, created exclusively
under the existing secure output guard (non-following, 0600, no overwrite,
128 KiB cap). JSON/CSV/HTML/Markdown never assert current lease,
independent review, exact-head required CI or merge/deploy approval.
No queue/worker/runner/Notion writes, GitHub mutations, Action reruns,
claims, merge or deploy are performed.

The portfolio inbox is complementary to the single-repository
`--live-inbox-repo` CLI below; it is not a substitute for the exact
issue-to-existing-PR live observation (`--live-issue-url`) or the
repository's real protected-main release gates.

### GitHub P0/P1-first live issue inbox (no saved case)

zSentinel can now open a **bounded current incident inbox** from the
repository's live GitHub issues, without a historical Notion/JSON file:

```sh
umask 077
mkdir -p -m 700 "$HOME/.local/share/zsentinel"
python3 scripts/zsentinel_recovery_policy.py \
  --live-inbox-repo Zennay/zCloud \
  --format html \
  --output "$HOME/.local/share/zsentinel/inbox-$(date -u +%Y%m%dT%H%M%S)-$.html"
```

Also available as `--format markdown` or `--format json` for
portable owner handoffs. A private **source-ranked human workorders CSV**
can now be created from the same live bounded issue observation:

```sh
umask 077
mkdir -p -m 700 "$HOME/.local/share/zsentinel"
python3 scripts/zsentinel_recovery_policy.py \\
  --live-inbox-repo Zennay/zCloud \\
  --format csv \\
  --output "$HOME/.local/share/zsentinel/issues-$(date -u +%Y%m%dT%H%M%S)-$.csv"
```

CSV requires `--output`, uses the existing create-only private 0600
writer and includes at most 45 issue links **per selected page**.
Use `--inbox-page N` (1–5) with the same `--live-inbox-repo` command
to select another page of the bounded P0-first issue sample, for example
`--inbox-page 2 --format csv --output /your/private/issue-page-2.csv`.
All four formats expose the selected page, number of sampled pages,
omitted sampled issue count and explicit out-of-range source-page flag.
Each page selection performs the same bounded source read with at most
7 GETs, not an additional GitHub provider search in the same run. When
run separately, page 2 is a **new observation time**, not an atomic
continuation of page 1; compare original timestamps before interpreting
cross-page changes. This pagination navigates only the sampled source
(<=210 source rows), never claims an exhaustive GitHub backlog.

**One-snapshot alternative for the complete bounded issue sample:** to
avoid moving GitHub Search results between separately requested pages, use
`--inbox-all-sampled-csv` with private CSV output. It selects *all* up to
210 deduplicated issues admitted by **one** existing maximum-7-GET read,
not a new inventory or a GitHub-side bulk action:

```sh
umask 077
mkdir -p -m 700 "$HOME/.local/share/zsentinel"
python3 scripts/zsentinel_recovery_policy.py \\
  --live-inbox-repo Zennay/zCloud \\
  --inbox-all-sampled-csv \\
  --format csv \\
  --output "$HOME/.local/share/zsentinel/all-issues-$(date -u +%Y%m%dT%H%M%S)-$.csv"
```

This restricted mode requires the default page 1 selector, an existing
private output directory, and `--format csv --output`. The compact CSV
retains original GitHub URL, priority as observed, owner action code,
human follow-up, source-drift warning, timestamp and coverage flags,
while omitting untrusted issue titles and private worker details. Its
false owner/merge-authority columns do not imply eligible work. If the
sample is empty, one provenance row says that *the sampled source*
contained no issue records, never that the live backlog is empty.
Both paged and complete-sample CSV use the same create-only 0600 output
guard and 128-KiB bounded output. The complete sample is still
bounded by provider pagination/partial results; it is not a claim of
all open GitHub issues.

The legacy JSON field `source_window_full` now matches
`recent_issue_window_complete`: true requires a stable, complete,
error-free recent source, rather than incorrectly returning true when
the three-page read was exhausted while more GitHub issues remained.
Both fields use exactly one calculated completeness value, so downstream
operator integrations cannot receive contradictory coverage claims.
Two copies of a GitHub issue that differ only in label-source completeness,
conflicting labels or label/title disagreement (even while normalized
priority remains UNKNOWN) are now explicitly counted as source-window
drift. The human route becomes `recheck_changed_issue_source_before_owner_action`
rather than silently carrying a stale suggested fix. Columns carry the original
GitHub issue, unverified title hints, observed severity, source-window
coverage, explicit human-only owner action code, and three short fields
**problem / action / status**. CSV prefixes unsafe spreadsheet formulas,
never includes original titles/bodies, worker contact details or secrets,
and every permission/CI/merge/deploy field is false. Empty source windows
produce a provenance-only record, not a misleading empty file.
This handoff does not dispatch a worker or claim an issue.
The script-free HTML inbox now groups visible issue rows into navigable
source-priority sections (explicit P0, explicit P1, title-only hints,
explicit P2/P3 and unknown/conflicting). Each issue has the same
three-line **problem / owner action / status** as the JSON, Markdown and
private CSV; group links remain inside the original static file. The
grouped 45-row display is not proof that no older incidents exist.
The inbox is **read-only** and only accepts an
explicit canonical `Zennay/<repo>` identity. It reads at most
three pages of 30 **recently updated open issues** using the
official GitHub **issue-only search** (`is:issue is:open`), not the
repository issues listing that also contains PRs. It independently
rejects any PR-shaped result, validates every retained issue URL and
repository, and displays at most 45 links. Maximum network cost:
**7 GitHub Search GETs**, each capped at 2 MiB. The normal recent
inventory has at most 3 pages. If a Search page ends early while
GitHub still reports unseen issues, the inbox continues bounded pagination
instead of treating that short page as proof the inventory is complete.
Only if recent coverage is truncated, GitHub flags incomplete results,
the reported total changes, duplicate recent issue records imply moving
page boundaries, or invalid issue rows prevent complete admission does
zSentinel make up to four additional issue-only searches,
**one 30-row page per source**: `label:P0,...`
and `label:P1,...` (explicit label variants including `priority:P0`
and `priority/P0`), followed by `P0 in:title` and `P1 in:title`.
Each labeled search hit is checked against the actual returned issue's
recognized GitHub label before gaining advisory P0/P1 rank. A title
match without an explicit matching label is an **unverified navigation
hint**, never a confirmed issue priority, active claim or independently
established blocker. The title queries match words outside the title
prefix too; irrelevant hits are excluded after the source is read.
Provider counts above the 30-row recall cap are displayed as partial.
Repeated issue URLs are deduplicated and every displayed issue records
all source windows in which it was observed. When recent results already
cover the full repository's open-issue listing, all four additional
queries are skipped entirely.

The JSON, Markdown and HTML report the provider's `total_count`,
`incomplete_results`, actual source queries/cutoffs, recent coverage,
older P0/P1 recall coverage, and how each issue entered the inbox.
Repeated issue URLs across source windows are deduplicated. When a
repeated issue has inconsistent timestamps or priority signals, the
issue's derived urgency is set to UNKNOWN and a source-drift warning
is shown; when the repository's reported issue count changes across
recent pages, overall coverage is also marked partial/UNKNOWN.
The outputs also expose `recent_rows_read`,
`recent_duplicate_issue_records`, and `recent_issue_window_complete`.
This completeness signal requires a finished, stable, fully admitted
recent window; it remains false when repeated GitHub results or malformed
issue rows may have displaced other open incidents. All recovery searches
remain within the original absolute 7-GET limit.
Incomplete search, truncated display, inaccessible source or changing
issue records never prove the absence of P0 incidents or permit
duplicate repair PR creation.

Only explicit `P0`–`P3` or `priority:p0`–`priority:p3`
(and slash variants) GitHub labels establish the **advisory** source
priority; conflicts become UNKNOWN. An issue title beginning with
`P0:` or `P1:` may appear as a clearly unverified,
secondary **navigation hint**, not a verified priority or authority.
An explicitly labelled P2/P3 issue remains in its labelled priority group
even when its title contradictorily begins with P0/P1. Incomplete or
malformed label sets never promote a title hint into prioritized triage.
A mismatched title/explicit label now creates an operator-visible
`explicit_label_title_disagreement` and a human-only
`inspect_conflicting_priority_with_issue_owner` follow-up across JSON,
Markdown, HTML and CSV. Each source-admitted issue also carries a
three-line `operator_workflow` (problem, action, status) and an
`owner_next_step` code. Inconsistent source snapshots are routed to
reinspection rather than retaining stale human instructions. The source-window summary reports the number
of observed contradictions. If two sampled versions of the same issue
disagree, the prior contradiction becomes UNKNOWN rather than falsely
preserving a stable owner action; only the original issue owner can
resolve the metadata inconsistency.
The original issue title/body, user names, private label names and
tokens are never exported; the operator receives canonical issue
links, observed priority labels (not the original label text), limited
update timestamps, and explicit UNKNOWN ownership/repair/CI/merge
statuses. A derived "no GitHub issue update for 72 hours" flag helps
triage neglected P0/P1 records but **does not establish stalled workers
or permission to restart/reassign anything**. Within a priority group,
records with older last-issue activity are shown first. An issue
outside the bounded recent-source window may be missing.

The inbox cannot take over an active claim or report any PR merged or
live from issue-list data. Select an issue URL from its output and use
`--live-issue-url` to inspect the current associated PR
candidates with bounded timeline and open-PR reads. Both paths preserve
the current owner/CI/protected-main/research-integrity gates. Neither
will retry workflows, restart services, access worker secrets, alter
Notion or SQLite, merge or deploy.

### Inspect one live GitHub issue without a saved private dossier

The read-only owner desk can start immediately from an existing canonical
`Zennay/<repo>` GitHub issue. A saved Notion/queue case is **not** required:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --live-issue-url https://github.com/Zennay/zCloud/issues/1326 \
  --format html --output /private/zsentinel-issue-1326.html
```

Use `--format markdown` for a human-readable issue/PR owner handoff,
or `--format json` for a versioned machine-readable **read-only** observation.
The optional `--live-focus-pr N` restricts the expensive double-read to
one PR whose number was actually observed in the current bounded timeline or
open-PR source. Arbitrary PR numbers, other GitHub owners, PR URLs supplied
as issue URLs, noncanonical identities, unsafe output destinations and
incompatible source modes fail closed.

The operation reads only GitHub's canonical issue, issue timeline,
open-PR list and at most three PR/check/review observations, under the
existing **20-GET upper bound**. A current issue's explicit `P0` through
`P3` or `priority:p0` through `priority:p3` labels are surfaced as
advisory priority only. Missing or conflicting priority labels result
in `UNKNOWN`, never an invented emergency, owner claim or automatic
escalation. No free-form issue title or body is exported. It never performs a claim, retry,
workflow dispatch, merge, deployment, runner restart, SQLite mutation or
Notion write. Partial/failed GitHub sources remain UNKNOWN, never `no
repair exists`. A GitHub 401/403/429 in timeline or list discovery
stops further reads (including issue/PR detail GETs) in the same run;
already observed hints remain non-authorizing and provider denial is
explicit. This also conserves GitHub quota when permissions are unavailable. The JSON field `observation_mode=direct_live_issue_readonly`
and `historical_case_provenance.historical_case_provided=false` make the
absence of a saved dossier explicit; no fake case timestamp or past
owner confirmation is constructed. A closed issue or GitHub cross-reference
is not proof that an exact-head fix reached protected main or production.

The `--output` route uses the existing private create-only 0600 output
contract. Without `--output`, the report is printed to standard output;
redirect only into an operator-controlled private directory.

### Reconcile historical repair PRs with current open PRs (explicit opt-in)

A saved zSentinel recovery case is a point-in-time observation. It may
reference no PRs, one old PR, or up to three historical repair PRs, while
new fixes are opened or closed later. The normal `--live-candidates`
path reviews only the saved candidates. The **additive**
`--discover-existing` path also checks the official issue timeline's
cross-referenced PRs (including closed/merged PRs) and scans recent
GitHub open PRs for this same repository and issue:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1518 \
  --live-candidates --discover-existing \
  --format html > private-existing-pr-discovery.html
```

`--discover-unlinked` is retained as a backwards-compatible alias for
`--discover-existing`. The option is now useful **with or without
historical candidates**. It never changes the saved case, any claim or the
GitHub repository. If historical PRs exist, those references retain their
original order and priority in the live-read slots; newly discovered
references are deduplicated against them and fill unused slots up to
**three** live double-reads. When all three slots are occupied, additional
discovered PRs are still offered as **navigation-only links with live status
UNKNOWN**; they are not silently discarded or treated as reviewed.

The combined source window is strictly limited to **three issue-timeline
pages of 30 events** and **four pages of 30 open PRs**, with
at most **12 timeline cross-reference hints and 12 text hints** retained
for navigation. The first timeline page is read, then up to two most
recent pages when GitHub's canonical `Link: rel="last"` pagination
header identifies them; otherwise, at most two sequential fallback
pages are read. The returned URL itself is never followed. Middle
historical pages may be omitted and source coverage remains explicitly
partial. Official GitHub `cross-referenced` events are admitted
only when the event's originating PR URL exactly belongs to the same
canonical repository. They precede weaker title/body text matches, and
duplicate PR identities are removed. The text source examines the issue
number in bounded PR titles/descriptions, without emitting those
contents. Closing-keyword references precede ordinary text mentions;
equal ranking preserves GitHub source order. Short pages stop early;
full final pages explicitly mark incomplete pagination. Timeline events
prove only GitHub's reference relationship, **not** that the PR resolves
the issue, belongs to a free worker, passed required gates or reached
the default branch. Text hints are weaker still.

The two bounded sources are independent: if GitHub rejects a later
timeline or PR-list page, already observed cross-references or textual
hints remain usable as navigation clues while the failed source,
actual GET attempts and coverage are explicitly marked UNKNOWN.
No single partial source can justify a new PR, retry, claim or merge.

Each live-read PR is fetched twice and compared for immutable observed
HEAD, state, draft flag, target and both description/title digests;
a change yields UNKNOWN. Other source snippets (check runs and submitted
reviews) remain non-authorizing. JSON plus printable, script-free HTML
and Markdown show actual pages/rows scanned, historical count, overlaps
deduplicated, live newly discovered count, navigation-only hint links and
incomplete-source warnings. A missing text match never implies no open
repair PR, available owner, closed incident or merge clearance.

### Focused inspection when the historical PR slots are full

For an incident that already has three historical repair PRs, use
`--discover-existing` to obtain additional **navigation-only** links.
To inspect the precise current state of one such source-linked PR with
the existing double-read guard, run a **focused** read:

```sh
umask 077
python3 scripts/zsentinel_recovery_policy.py \
  --case-input private-recovery-case.json \
  --view-project cloud --view-issue 1518 \
  --live-candidates --discover-existing --live-focus-pr 1524 \
  --format html > private-focused-existing-pr.html
```

The example PR number must appear in the saved case or the bounded
discovery results for that issue. It is not a free-form way to inspect,
adopt or control arbitrary PRs. `--live-focus-pr` may also select an
already recorded historical PR without `--discover-existing`; in that
case it does not perform extra GitHub PR-list searches. Only the
selected PR receives the live issue/PR/HEAD/check/review observation.
Every other saved or discovered PR remains a visible **unreviewed link**
with live status UNKNOWN. A focused read never asserts that a PR is
merged to main, owner-approved or deployed. With discovery enabled,
the upper GET budget reduces from 20 to **12** (3 timeline pages,
4 open-PR pages, one issue and up to four focused PR reads).

The upper read bound is **20 GitHub GETs**: at most three issue-timeline
pages, four open-PR-list pages, one original issue GET and four GETs
for each of three live-read candidates. Each timeline/list response
has a fixed 2 MiB cap; issue/PR/check/review reads retain the 160 KiB
cap. 401/403/429 on one discovery source prevents further pages from
that source and records a visible UNKNOWN; it never grants a retry,
owner reassignment, merge or release authority. The receipt records actual `get_attempts`, the sampled timeline page
numbers, whether the recent tail was observed, unobserved middle-page
coverage and the conservative `read_get_attempt_upper_bound`. A live PR reference is only an
owner-navigation lead until required exact-head CI, independent review,
active claims and protected-main rules have been separately verified
by an authorized operator.

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


## Single-request HTML decision card

The single-request policy mode now supports a standalone HTML decision card in addition to JSON and Markdown. It keeps the exact source link and observation time visible, lists every HOLD reason and missing evidence field, and clearly separates a local policy result from independently verified release authorization. The page has no scripts, remote resources, forms or embedded content.

```sh
install -d -m 700 "$HOME/.local/state/zsentinel"
python3 scripts/zsentinel_recovery_policy.py \
  --input private-policy-request.json --format html \
  --output "$HOME/.local/state/zsentinel/policy-decision.html"
```

Use a new output filename in an existing owner-controlled private directory. The existing output cap and create-only mode-0600 contract still apply. This is a presentation route only: it does not check live providers, acquire claims, retry CI, merge, deploy or restart runners.

## Claim-state gates for execution-impacting recovery

Retry, root-fix routing, independent task admission, parking/resuming a task, merging a PR, deploying and restarting a runner now all require the observed claim state to be explicitly none. Missing or unknown claim evidence is a HOLD; an active claim must not be displaced by a second recovery action. Human escalation is different: it may be routed to a known current owner, but unknown ownership remains blocked. These policy decisions are advisory only and do not change any live claim, queue, runner or release state.

## Actionable next steps in policy reports

Single-request JSON, Markdown and HTML reports now include a deterministic `recommended_next_step`. It directs stale evidence to refresh, active/unknown claims to the current owner, pending/red exact-head gates to the normal CI/review path, unhealthy capacity to wait, missing approval to the authorized owner, and dependency/overlap problems to evidence collection rather than mutation. Batch Markdown also carries the per-proposal recommendation; competing actions for one incident are explicitly routed to an owner to choose one route.

The recommendation is advisory and is recomputed after batch conflict detection adds a HOLD. It does not acquire claims, dispatch retries, change queue state, merge, deploy or restart a runner.

## Per-route guidance in the offline HTML workorder

The batch HTML workorder now retains the incident-level summary and adds a disclosure for each proposed action. It shows that route's decision, deterministic recommended next step, original source link, HOLD reasons and required evidence. This is especially useful when one incident has competing recovery routes: the report keeps them on HOLD and asks an authorized owner to choose one instead of implying both can proceed. The disclosure is rendered only from already validated batch input and performs no live reads or side effects.

## Per-route recovery guidance in CSV exports

The incident-level CSV keeps its stable owner-workorder row and adds `proposal_recommendations_json`. For ordinary policy batches this bounded JSON array carries each route's action, decision, recommended next step, blocker codes and required evidence. A historical case export without per-action batch results emits an empty array and retains its explicit incident-level next step; it does not invent route evidence. The column is informational only and does not authorize claims, retries, merges or deploys.

## Stable recommendation codes for integrations

Every single-action report now includes `recommended_next_step_code` alongside the human-readable recommendation. Codes are stable routing hints such as `refresh_source`, `resolve_claim_with_owner`, `refresh_exact_head_gates`, `wait_for_capacity_and_runtime_evidence`, and `choose_one_incident_route`. JSON exposes the field directly; Markdown/HTML show it next to the instruction; the CSV per-route JSON carries both. Competing routes update both fields after the batch HOLD is added. Codes never grant action authority.


Missing ownership is not treated as an approval request to an already-known person. When `owner_missing` or `human_owner_missing` is present, the next-step text instructs the operator to identify the responsible owner from current source evidence without inventing ownership; the machine code is `identify_authorized_owner`. Only a known owner with missing approval maps to `request_authorized_owner_approval`.


If the claim is active/unknown and its owner is also missing, the route is `identify_claim_owner` first; it must not assume that an identifiable current owner exists.

## Specific next steps for dependency, escalation and release holds

The recommendation map now gives explicit routes for unverified source receipts, missing blocked-task IDs, unknown dependency type, missing operator attestation, escalation-basis gaps, and post-merge health gaps. Release approval is separated from technical gate readiness. Unknown/missing owner and active-claim combinations remain owner-identification HOLDs; none of these recommendation codes performs a queue or runtime action.

## Portfolio issue desk — source-bound change tracking

The recovery role can compare **two bounded private GitHub issue-desk JSON observations** without writing queue, lease, CI or GitHub state. This is meant for the human owner to see newly visible P0/P1 work and changed urgency while the live runtime/release gates are still waiting.

Create a first snapshot into a private existing directory:

```sh
python3 scripts/zsentinel_recovery_policy.py \
  --portfolio-repo Zennay/zCloud --portfolio-repo Zennay/Supa \
  --portfolio-view all --format json --output /private/path/baseline.json
```

Later, create a distinct output file using exactly the same repository order and view:

```sh
python3 scripts/zsentinel_recovery_policy.py \
  --portfolio-repo Zennay/zCloud --portfolio-repo Zennay/Supa \
  --portfolio-view all --portfolio-previous-snapshot /private/path/baseline.json \
  --format html --output /private/path/changes.html
```

The dedicated CLI `scripts/zsentinel_portfolio_issue_desk.py` also accepts `--previous-snapshot` and the same four private output formats (JSON, offline CSP-locked HTML, Markdown and CSV). A previous snapshot is read with the existing bounded, regular-file/no-symlink, identity-checked local file reader; its JSON fields, UTC timestamps, issue links, repository coverage, duplicate IDs, observed rank and sample limits are checked before any GitHub GET. Missing or contradictory fields fail closed.

**Interpretation is intentionally conservative.** The comparison reports `NEWLY_OBSERVED_NOT_PROVEN_NEW`, `OBSERVED_PRIORITY_CHANGE`, `PERSISTENT_P0_P1_WITHOUT_GITHUB_ISSUE_UPDATE` (only if the same labelled P0/P1 issue is in both samples, its GitHub issue timestamp is unchanged, and observations are at least six hours apart), and `NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED`. Persistent GitHub issue metadata is not proof that coding, CI, review or worker activity stalled. A card disappearing from a limited window never means its issue was closed, its fix merged or its worker released. Both source windows must have been readable for a repository to compare it; an inaccessible repo stays UNKNOWN. The per-repository source transition also distinguishes GITHUB_SOURCE_LOST, GITHUB_SOURCE_RESTORED_NOT_BACKLOG_CLEARED, SOURCE_UNAVAILABLE_IN_BOTH_SNAPSHOTS, changed coverage and uninterrupted readable sources. Each transition has an explicit unexecuted human source action, including CSV source-transition rows. Returning GitHub access does not prove that blocked tasks or old issues have been resolved. The tool does not recheck historical issue state with extra GitHub API calls, infer claim ownership, retry workflows, grant reviews, merge, deploy or touch the VPS.

A comparison has a bounded 100-card operator window, counts and explicit omitted-card totals. Its output is **advisory only**, with `issues_resolved_or_closed_verified=false`, `ownership_claims_verified=false`, `ci_or_review_verified=false`, `merge_or_deploy_authorized=false`, and `actions_executed=false`. It is a planning aid, not an authority source. For actual action, recheck present claims, the exact existing PR, its exact-head mandatory checks, review, branch protection and owner authorization directly.

### Create the readable report and its next historical source in one pass

A separate GitHub fetch is **not** necessary just to preserve the source for tomorrow's comparison. Use the canonical recovery CLI with a second, previously non-existing private JSON filename:

```sh
python3 scripts/zsentinel_recovery_policy.py \
  --portfolio-repo Zennay/zCloud --portfolio-repo Zennay/Supa \
  --portfolio-view explicit-critical \
  --format html --output /private/path/desk-22.html \
  --portfolio-snapshot-output /private/path/snapshot-22.json
```

On the next operator run, use the previous JSON and produce both the new visible report and another reusable snapshot from **one bounded source observation**:

```sh
python3 scripts/zsentinel_recovery_policy.py \
  --portfolio-repo Zennay/zCloud --portfolio-repo Zennay/Supa \
  --portfolio-view explicit-critical \
  --portfolio-previous-snapshot /private/path/snapshot-22.json \
  --format html --output /private/path/desk-23.html \
  --portfolio-snapshot-output /private/path/snapshot-23.json
```

The direct `scripts/zsentinel_portfolio_issue_desk.py` command offers the equivalent `--snapshot-output` option. The two private outputs are produced from the **same** already bounded GitHub reads, with the raw source snapshot explicitly excluding any nested history comparison. JSON/HTML/Markdown/CSV are valid report formats; only `.json` is accepted for the paired snapshot. Both filenames must be distinct, be newly created (no overwrites), belong to safe existing owner-controlled directories, be size-bounded to 128 KiB, and be mode 0600. Output bytes and bounds are checked before creating either file. When the second artifact fails after the first snapshot was written (for example, because the report destination already exists), only the first snapshot may remain: this is preserved source evidence, **not** a successful paired report or a completed operational handoff. The command returns a failure and should be retried with new unused report filenames after resolving the path issue.

This snapshot is **not a new GitHub permission or independent claim attestation**: it records what the bounded live issue reader observed, still limited to 2–6 explicit repositories and sample windows. Any source errors remain UNKNOWN; no issue disappearance is interpreted as closure. No runner, worker, notifier, queue writer, retry, CI workflow or production deploy is invoked.

### Offline incident/history briefing during a GitHub access outage

When two private source snapshots already exist, generate a human-readable comparison **without making any GitHub request at all**:

```sh
python3 scripts/zsentinel_portfolio_history.py \
  --previous /private/path/snapshot-22.json \
  --current /private/path/snapshot-23.json \
  --format html --output /private/path/offline-issue-changes.html
```

The offline compare command also supports JSON, Markdown and a compact CSV schema, with the same create-only private output requirements. Both snapshots are read under the regular-file/no-symlink bounded admission contract. A comparison requires identical canonical project names/order and display view, a strictly later snapshot, and validated issue links, ranks and UTC times. The result explicitly distinguishes newly observed and re-ranked source cards, old cards absent from the new bounded window, >=6-hour unchanged P0/P1 GitHub update metadata, and source-coverage transitions. Every changed issue includes a distinct, non-executed human follow-up workorder: check existing PR/claims, verify a priority change, inspect present owner/CI activity before declaring a stall, or recheck the original issue before considering closure. These codes appear in private JSON and CSV alongside clear advisory-only HTML/Markdown guidance.

**Not a live audit:** an offline output states clearly that it is based only on operator-supplied observations. It cannot establish current issue closure, current worker state, rights to merge or deploy, or present GitHub availability. A source-access failure means the next live GitHub read must be checked by an authorized owner, rather than interpreting the last successful report as current.

### Optional source-bound GitHub closure-state reconciliation for missing issue cards

A prior issue card can disappear from the **bounded display window** even while its GitHub issue remains open. The private offline history command now has a deliberately **opt-in, read-only** detail source check for just those missing cards, not an automatic retry or fix:

```sh
python3 scripts/zsentinel_portfolio_history.py \
  --previous /private/path/snapshot-22.json \
  --current /private/path/snapshot-23.json \
  --verify-missing-live --live-recheck-limit 3 \
  --format html --output /private/path/rechecked-issues.html
```

Default is strictly offline (zero GitHub requests). When explicitly enabled, zSentinel makes at most `--live-recheck-limit` (1–6; default 3) sequential canonical single-issue **GET** requests to the existing fixed-host, no-redirect GitHub reader. It checks the most urgent previously observed missing cards first, using only the old rank as a **historical triage hint**, not a live priority. The request admits canonical `Zennay/<repo>` and positive issue numbers already validated in both snapshots. A canonical GitHub 404 leaves that issue `SOURCE_UNAVAILABLE_AT_LIVE_RECHECK` (never inferred deleted or closed), marks later cards in the **same repository** `NOT_CHECKED_REPOSITORY_SOURCE_ERROR`, and allows independent repositories to continue under the same bounded read budget. A provider-wide auth/quota/network/body-cap failure or unexpected issue identity stops further provider requests across all projects; remaining cards become `NOT_CHECKED_AFTER_SOURCE_ERROR`. A request limit yields `NOT_CHECKED_READ_LIMIT`, never a false closed status. Both failure types remain UNKNOWN and cannot authorize a worker claim, issue closure, CI retry or release.

One successful detail response may establish `OPEN_AT_LIVE_RECHECK` or `CLOSED_AT_LIVE_RECHECK` with a fresh source-observation timestamp and normalized GitHub state reason. A GitHub closed issue is **not evidence** that a fix was coded, reviewed, merged to the default branch, deployed, or verified live. The original sampled change remains `NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED`; the observed present-day issue state is an additional independent field, not a rewrite of source history. JSON, script-free HTML, Markdown and private CSV distinguish checked, uncheckable and not-checked states and show the bounded provider-read count. No worker claim, queue write, notification, PR action, CI dispatch or runner/service restart is ever performed.

For private GitHub issues a valid `GITHUB_TOKEN` may be supplied through the host environment rather than command arguments; no token, raw response body, issue body or private issue title is emitted into an operator report. Use this opt-in source read only when the appropriate owner has ordinary read access; an unavailable private repository is UNKNOWN rather than a request to escalate privileges.

### One-pass live portfolio recheck in the established recovery CLI

The same source-preserving reconciliation is now available directly within both established live portfolio entry points, so an operator need not separately run the history command after collecting a new snapshot:

```sh
python3 scripts/zsentinel_recovery_policy.py \
  --portfolio-repo Zennay/zCloud --portfolio-repo Zennay/Supa \
  --portfolio-previous-snapshot /private/path/snapshot-22.json \
  --portfolio-verify-missing-live --portfolio-recheck-limit 3 \
  --format html --output /private/path/current-issues.html \
  --portfolio-snapshot-output /private/path/snapshot-23.json
```

The dedicated `scripts/zsentinel_portfolio_issue_desk.py` offers equivalent `--previous-snapshot`, `--verify-missing-live` and `--live-recheck-limit` options. Both flags are **disabled by default** and require a valid previous snapshot; 1–6 additional detail GETs are permitted only when the user explicitly requests them. This means the portfolio issue-source budget remains bounded by the existing 7 GETs per repository (at most 42 across six repos) **plus** at most six opt-in, canonical single-issue GETs (48 total in the largest permitted portfolio view). The additional detail reads use the same fixed-host/no-redirect, response-size-limited GitHub source reader.

If the current portfolio discovery already recorded `provider_stopped_early=true`, opt-in detail checks are **not sent**; the missing-card state is `NOT_CHECKED_PRIOR_PROVIDER_STOP` instead. No repeated retries are used to disguise provider denial. Every candidate also carries one unexecuted human follow-up: continue an **OPEN** issue with the currently authorized owner; separately check merge, release and runtime evidence for **CLOSED**; or confirm source access for UNKNOWN. An explicit GitHub `state_reason=not_planned` routes to review of the original rejection/closure decision, **not** to a shipped-fix workorder. Closed without an explicit `completed` reason requires separate closure-history inspection. The reported GitHub state reason is visible alongside the operator action in JSON, HTML, Markdown and private CSV. These live detail annotations never edit the original historical change category, never issue a queue/claim command and do not authorize PR merge/deploy. The paired new JSON snapshot intentionally excludes the live-recheck/diff annotations, retaining only the source data fetched from the portfolio issue reader for future comparisons.

### One compact human workorder board from bounded portfolio history

Every two-snapshot history comparison now includes a `zsentinel-portfolio-human-workorders-v1` board. It is computed **only** from the previously admitted bounded source, without another network read, worker claim or background task. The board unifies:

- changed issues sorted by observed reader rank (P0/P1 first), with distinct source metadata for **current explicit GitHub P0/P1 label**, **current unverified title hint**, **current explicit P2/P3**, **current unknown** and **historical sampled rank only**. Only ranks 0/1 from the current observation set `critical_priority_verified_from_current_label=true`; a missing issue uses only its historical rank as a navigation hint;
- the original canonical issue URL, GitHub issue-detail state if explicitly rechecked, and an action code suitable for a human owner;
- source-access loss, restoration and partial-coverage transitions as separate **source** workorders, without assigning them a fabricated issue severity;
- counts for workorders by repository, plus explicit `omitted_issue_workorders` and `hidden_historical_changes_not_classified` counts.

For predictable local output size, the dedicated operator board presents up to 20 issue workorders and reserves room for up to six repository source-coverage workorders. The underlying historical comparison still retains up to 100 changed issue cards. A display cap is not a clean-backlog or resolution signal. JSON, script-free HTML, Markdown, dedicated offline CSV and portfolio-desk CSV all expose the human workorder view. All cards explicitly state `HUMAN_REVIEW_REQUIRED`, `active_claim_verified=false`, `fix_merged_main_verified=false`, `deployment_verified=false` and `action_executed=false`; neither historical issue priority nor a GitHub CLOSED state confers authority to change worker state, retry CI, merge or release.

If an operator opted into bounded live issue reads, the compact board is updated **after** the read to reflect `OPEN`, `CLOSED`, `not_planned` or UNKNOWN with the correct unexecuted next action. This does not alter the original historical event or authorize any mutation. The prior snapshot saved via `--portfolio-snapshot-output` remains raw evidence with no nested workorder history.

### Snapshot provenance and urgent-first operator routing

The source-history comparator now admits only internally consistent snapshot clocks and coverage: the report creation time cannot be more than two minutes in the future, repository observation and issue-update timestamps cannot be later than the report's source time plus that bound, and `SAMPLED` cannot contradict `source_partial_or_unknown=true` (nor can unread/incomplete source pretend `false`). Malformed/future operator-supplied observations fail closed before creating any new private operator artifact.

Within the separate compact recovery-workorder board, urgency is explicit and non-authorizing: **current verified GitHub P0**, **current verified P1**, historical P0/P1 labels, unverified title hints, lower priorities and UNKNOWN. A snapshot's original issue urgency is not silently turned into a present-day owner claim. At most 20 issue workorders are selected before separately including all up-to-six repository source-coverage workorders; omitted counts are retained. Reports do not hide the affected project's source-access needs behind a display cap. GitHub release authority and owner permissions are never inferred from any of these source labels.

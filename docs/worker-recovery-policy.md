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
- escalate_human: route to a named human only for a specifically evidenced permission requirement, irreversible/high-risk decision or exhausted safe recovery routes; unknown claim state remains HOLD.
- merge_pr: verified exact head, green required CI, independent approval, satisfied branch protection, zero unresolved review threads, verified not-yet-merged state and owner authorization.
- deploy: exact-head CI/review/protection, verified merge on main, healthy post-merge state, release approval, runtime-owner authorization and healthy capacity.
- restart_runner: verified unavailability, no running jobs, confirmed cause, runtime-owner authorization and explicit human approval.

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

The conversion ranks P0 before P1–P3, puts issue/PR examination first within
a priority, and requires checking real GitHub source/required CI plus current
Notion/worker/queue ownership. Grouped high queue claim attempts are not proof
of deterministic/transient failures or permission to retry. Output contains no
raw task IDs, prompt content, tokens or active-claim keys. This is an operator
handoff from #1550's *source branch*: it does not assert that either PR
has reached main, that the exporter or importer has been deployed, or that
the current protected-main gate permits any merge.


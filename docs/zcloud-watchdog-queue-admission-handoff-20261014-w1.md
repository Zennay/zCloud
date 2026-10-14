# zCloud queue-watchdog: safe evidence handoff (2026-10-14)

Owner handoff for [#1269](https://github.com/Zennay/zCloud/issues/1269), [#1326](https://github.com/Zennay/zCloud/issues/1326), and exclusive watchdog [PR #1091](https://github.com/Zennay/zCloud/pull/1091).

## What is actually wrong on current main?

The observe script in .github/workflows/zcloud-self-hosted-queue-watchdog.yml requests only the first per_page=100 page of queued runs and each run's jobs. It groups queued self-hosted jobs by path or name, keeps the lowest queue age, and attempts to cancel all other runs in each group. This can misclassify legitimate separate dispatch intentions as duplicates; a run may also contain unrelated or already-running jobs even if one job is queued. This is a static source finding, **not** proof any cancellation took place.

The existing branch/ref guards reduce some untrusted-event exposure. They do not establish complete inventories, identical intent or cancellation safety.

## Standalone source-only reference

- scripts/zcloud_watchdog_queue_admission_20261014_w1.py
- tests/test_zcloud_watchdog_queue_admission_20261014_w1.py

The tool only accepts local JSON input. It makes **zero HTTP requests, GitHub cancellations, service restarts, SQLite writes or worker pushes**. It is not wired into the watchdog. The run_pages and jobs_complete fields describe the evidence **claimed by the caller**; they are not independent or cryptographic API pagination proof. Thus even structurally complete input can never authorize mutations.

Example (informational only):

    python3 scripts/zcloud_watchdog_queue_admission_20261014_w1.py --snapshot /path/to/readonly-snapshot.json --expected-main-sha <40-lowercase-hex-sha>

**Never** treat NOOP, REVIEW_ONLY, inventory_complete=true or CLI exit zero as cancellation, restart or deployment permission. candidate_duplicate_groups are human-review hints. cancel_run_ids is always empty and mutation_authorized=false.

## GET-only snapshot collector (second deliverable)

- scripts/zcloud_watchdog_queue_collector_20261014_w1.py
- tests/test_zcloud_watchdog_queue_collector_20261014_w1.py

The GitHub reader is constrained to **three fixed GET route families only**: canonical `branches/main`, queued `actions/runs` pages, and numbered run `jobs` pages. It rejects arbitrary repository paths, unexpected queries, traversal, download endpoints, and redirects before any network request. Tests include positive/negative route fixtures.

The separate collector reads the **canonical main SHA before and after**, fetches every available queued run page and every selected run's job pages (all requests strictly HTTP GET to api.github.com), verifies total counts, rejects **missing, invalid or duplicate run/job identities across all pages before fetching dependent data**, verifies exact next-page links, and **re-reads every queue page** after the job inventory, rejecting same-count replacements on later pages and identity changes under the same run ID. Every job must also provide `run_id`, `run_attempt`, `head_sha` and `head_branch` exactly matching its parent run; absent, malformed, cross-attempt or cross-commit job evidence denies the entire snapshot. Every **first- and second-pass** run/job identity is type-checked so JSON `true` cannot impersonate numeric `1` (Python would otherwise consider them equal). Runner IDs permit documented unassigned `0` or `null`, but **never** booleans. Duplicate-looking queued candidates receive an additional bounded second GET of their full job lists; state, runner assignment or identity changes deny (at most 110 candidate jobs rechecked). Unknown/oversized totals, external links, duplicate JSON keys, moved main, missing jobs, race or timeout are denied with a fixed privacy-safe reason. It timestamps the **start** of the observation, rejects snapshots taking more than **45 seconds** before treating them as current, and has a separate **115-second** process wall-clock and maximum **640-request** budget. Proxy inheritance, HTTP redirects and dynamic destination hosts are disallowed. Tokens are never printed.

Run manually for observation only on a trusted shell with an appropriately scoped read-only token in the environment:

    GITHUB_TOKEN=<read-only-token> python3 scripts/zcloud_watchdog_queue_collector_20261014_w1.py

Do not place a real token in documentation, issue comments or command history. A complete GET snapshot still has no separately authenticated same-intent key and **cannot authorize cancellation**, even if identical queued runs are reported. CI only runs offline fakes and a fake HTTP opener, not real privileged API calls.

## Manual GitHub-hosted observer — never a production control

- .github/workflows/zcloud-watchdog-hosted-queue-readonly-20261014-w1.yml
- scripts/zcloud_watchdog_operator_summary_20261014_w1.py
- tests/test_zcloud_watchdog_operator_summary_20261014_w1.py
- tests/test_zcloud_watchdog_hosted_workflow_20261014_w1.py

This is a **new, separate, manual-only** workflow. It has no push, pull_request, schedule or workflow_run trigger, requires the exact canonical repository, actor Zennay, main ref and first attempt, and runs **only on GitHub-hosted ubuntu-latest**. GitHub permissions are exactly contents:read and actions:read; checkout is immutable with saved credentials disabled. The owner can start it from Actions after review and eventual main merge; **there is no currently deployed workflow to run** while this PR is draft.

Manual execution checks main's actual GitHub API SHA **both before and after** the bounded GET-only collector, and publishes a short, **redacted Dutch operator summary**. Reports with duplicate keys, oversized data, ambiguous exits, fake mutation permissions or alleged production verification are denied. Unexpected JSON types (including an array/object in place of a workflow event or run/job status) produce a DENY record, not an exception or unredacted traceback. Unexpected unhashable/oversized API error values are sanitized by a fixed reason allowlist. No raw API response, prompt or credentials are printed in the summary.

For structurally accepted NOOP/REVIEW_ONLY observations the Dutch summary shows only a **bounded run count (0–500)** and the **number of candidate groups**; duplicate group IDs must be unique, positive, noncontradictory, and cannot exceed the reported run count. If these numeric claims are inconsistent, output becomes DENY and the counts are suppressed. A DENY caused by incomplete acquisition never displays counts, even if they appear in the untrusted input. These are informational source-local observations, **not attested production health, authenticated intent, or cancellation authority**. A REVIEW_ONLY or DENY decision intentionally makes the hosted job fail rather than imply approval.

This workflow provides **no** restart, runner repair, run cancellation, SQLite write, Firefox mutation, worker push or deploy. It does **not** replace the existing privileged watchdog owner; the fact that an observation is structurally stable does not prove duplicate run intent.

## Separate read-only GitHub queue route triage for #1326

- scripts/zcloud_queue_route_triage_20261014_w1.py
- scripts/zcloud_queue_route_summary_20261014_w1.py
- tests/test_zcloud_queue_route_triage_20261014_w1.py
- tests/test_zcloud_queue_route_summary_20261014_w1.py

The original strict admission classifier **must deny** a mixed historical queue and a queued workflow with no returned jobs; relaxing it to treat these runs as safe cancellation targets would be a regression. The **new independent route triage does not share its admission result**. Instead, it fetches a maximum of 40 queued runs, including non-main and older-main runs, with two complete bounded GET passes of both the run census and every run's job records. An API result of zero jobs is displayed as **`no_job_records`**, *not* as a healthy worker or cancellable stale job. It identifies `ftmo_research`, `zcloud_vps`, other/mixed and GitHub-hosted routes using only allowlisted labels, and separates `not_assigned` from `assigned` and `indeterminate` without claiming listener liveness.

**Exact raw observation on 14 October 2026 (source GitHub GET, not privileged execution):** `actions/runs?status=queued` returned **8 runs**; the individual jobs API returned three queued `[self-hosted,ftmo-research]` jobs with `runner_id=0` and empty runner names, and five queued-run records whose jobs endpoint returned an empty list. These runs include historical main and other-branch workflows; they are **not** authenticated duplicates, evidence of a broken `[self-hosted,zcloud,vps]` listener, or license to cancel/requeue. See [#1326](https://github.com/Zennay/zCloud/issues/1326) and its exclusive recovery owner [PR #1317](https://github.com/Zennay/zCloud/pull/1317).

Triage deliberately does **not** use the strict `assess` admission result. It instead produces a separate privacy-safe observation `OBSERVED` or `DENY`, with generated canonical numeric GitHub run links, bounded aggregates, source scope (`current_main`, `historical_main`, `other_branch`, `foreign_repository`) and route/assignment categories. On any disagreement between two job-state scans, main drift, page drift, invalid run/job identity, oversized inventory or stale scan, it refuses to publish counts. Its output **always** carries `runner_recovery_authorized=false`, `cancel_run_ids=[]`, `production_verified=false`, `worker_generation_verified=false`. The separate report renderer checks every run URL against its numeric ID, rejects duplicate/boolean IDs and inconsistent counts, and never echoes raw job payloads, branch strings, prompts, runner names, labels, errors, or secrets. For humans it derives a single **Dutch route-total line** from independently validated per-run records: FTMO research, zCloud VPS, no-job-record, unassigned and current-main counts; source-provided counts cannot override the recomputed values. Every run gets a canonical numeric GitHub Actions link for an operator to inspect, not a cancellation command. The report renderer also requires syntactically valid matching `main_sha_before/main_sha_after` and a UTC-Z observation timestamp; a contradictory or missing reported source identity suppresses all counts. These fields are *self-declared source observations*, never independently authenticated authorization.

The existing new **manual-only GitHub-hosted** read-only workflow now runs both references before a final main SHA recheck, publishes both sanitized Dutch reports, and exits non-green if either fails. Its hosted runtime timeout was increased from four to **six minutes** to bound the two collectors; permission scope remains exactly `contents:read` plus `actions:read`. No automatic trigger, GitHub write, cancellation, VPS contact, worker push or deploy has been introduced. This workflow is still **draft/unmerged and not runnable from main**. Actual runner recovery remains entirely with #1317 and serialized release approval.

### Additional source admission hardening — 14 October

The separate route observer now rejects **cross-run job-ID reuse**, even when both per-run job pages are individually well formed. It also rejects a non-queued run returned by GitHub's queued-only search, and unrecognized job-state strings. These contradictions no longer become plausible-looking route totals.

The report renderer requires canonical calendar-valid UTC second timestamps and a causally consistent triple of **route / runner assignment / job state**. A missing job record cannot simultaneously be reported as assigned, queued or containing a nonzero job count. Contradictory source-local buckets suppress all numbers and links rather than producing a misleading operator dashboard.

Only a single explicitly recognized GitHub-hosted image label is classified as `github_hosted`; empty, combined, private or unfamiliar labels go to `other_or_mixed`. This is deliberately conservative and may undercount hosted capacity. It is **not** an assertion that the zCloud VPS listener is offline. Focused adversarial regressions are included in the existing independent triage and summary suites. None of these checks authorizes cancel/restart/deploy or changes the exclusive queue-watchdog, listener, control-plane or SQLite owners.

### Fresh operator report and immutable nonauthorization receipt

Even a structurally self-consistent queue report is *time-bounded*. The read-only route summary now revalidates the source-local `observed_at` against its own **current UTC clock** and rejects a timestamp more than 60 seconds old or in the future. It also rejects invalid current clocks (naive or non-UTC) when tested with injected time. These checks protect an operator from treating a cached/replayed observation as an up-to-date queue count; they are **not authenticated freshness proof**. The standalone collector still has its independent 45-second acquisition cap and canonical-main before/after GET.

The triage receipt now explicitly includes `mutation_authorized=false` in both OBSERVED and DENY outcomes. Rendering an OBSERVED count additionally requires that exact false value and the sole fixed observational reason `read_only_route_counts_not_runner_liveness`. A malformed/missing/replayed timestamp, forged reason or positive mutation claim produces **no run links or counts and a failing exit**. Offline regressions cover 60-second equality, the subsecond expiry boundary, future timestamps, invalid UTC clock, forged authorization and CLI-level real clock behavior. A passing hosted test does not attest the latest production queue, worker generation, or runner readiness.

### Exact self-hosted pool labels (false attribution safety)

The route observer treats GitHub's standard OS/architecture self-hosted labels (`Linux`, `X64`, `ARM64`, etc.) as non-pool metadata; only an exact remaining pool label set `self-hosted + zcloud + vps` or `self-hosted + ftmo-research` yields the corresponding diagnosis. Unknown additional routing labels, combined FTMO/zCloud labels and duplicated label strings are `other_or_mixed`, **not** a confident statement that a particular VPS runner is targeted. This deliberately sacrifices optimistic classification for trustworthy incident triage. No change to actual workflow runner selection or any runner registration has been made.

### Bearer token header safety

The GET-only GitHub reader now admits only bounded printable ASCII token characters (`A-Z`, `a-z`, `0-9`, underscore, dot and hyphen). It rejects whitespace (including tabs), HTTP header control bytes, Unicode, malformed token shapes and oversized values **before constructing an HTTP Authorization header or attempting network access**. The exact token is neither serialized into diagnostic receipts nor printed in logs. The test matrix exercises header-injection, Unicode and long-value denial while retaining common scoped GitHub token layouts and the existing read-only path allowlist. This does **not** grant new GitHub permissions or change the production workflow token.

### Nederlandse operatorroute zonder technische overclaim

After allowlist, identity, timestamp and count validation, the summary prints a Dutch description of each run's branch provenance, runner pool, runner assignment and queued/active state. It then supplies **one** conditional troubleshooting checkpoint: inspect the current-main zCloud runner label/repo grant for eligible unassigned zCloud jobs, otherwise inspect the FTMO repository route for unassigned FTMO jobs, otherwise inspect workflow conditions when queued run entries have no job records. These are read-only manual review suggestions, **never** `systemctl`, cancellation, retry, or dispatch steps. Mixed/stale/malformed evidence still emits no claims or links. The explanatory priority is deterministic and tested; no runner-offline or AI-generation assertion is inferred from these counts.

### Evidence-accurate queue pressure and job life-cycle (follow-up)

A run returned by GitHub's `status=queued` listing does not prove **every** job inside it is waiting for an available self-hosted runner. The observer now distinguishes the actual bounded job-status set (`queued`, `in_progress`, `completed`, `waiting`, `requested`, `pending`) and `mixed_job_states` instead of collapsing all non-queued states into a vague "mixed or active" bucket. An empty jobs collection remains `no_job_records`, **not** a queued job. All recognized phases receive plain-language Dutch labels and keep the run link tied to the original numeric GitHub ID.

The headline "**wachtende runs zonder runnertoewijzing**" now counts **run records**, not individual jobs: each record must have **both** `job_state=queued` and `assignment=not_assigned`. A run with three queued jobs contributes exactly one run; multi-job totals are not misrepresented as three independent incidents. Completed, running, waiting-for-approval or requested jobs do not inflate this capacity warning. The manual FTMO or current-main zCloud routing suggestion likewise requires a *queued, unassigned* job. Operator evidence remains an **observation**; the source snapshot cannot establish listener liveness, identical dispatch intent or actual AI generation, and never grants restart, cancellation or deployment authority.

Offline matrix coverage distinguishes all six statuses plus mixed jobs, the Dutch report and queued-only capacity count. Production `server.py`, runners, privileged watchdog and all other owners are unchanged.

### GitHub null runner references are not proof of listener failure

GitHub Actions job records can carry an *explicit JSON null* `runner_id` and `runner_name` while the job is still queued. The route observer now accepts this exact, present-but-unassigned representation, alongside the previously observed `0 / ""` shape. It separately rejects missing runner fields, Boolean/float/string impersonations of numeric runner IDs, malformed names and negative IDs. Contradictions such as a positive runner ID with no name or a runner name with ID 0/null are **`indeterminate`**, never certified as assigned or evidence of a failed listener.

The reader rechecks each observed job in the second pass; a transition from null/null to an assigned named runner invalidates the initial observation instead of publishing a stale queue figure. End-to-end tests confirm a real-shaped `ftmo-research` queued job with `null` runner metadata remains **read-only**, generates a sanitized Dutch summary, reveals no job name, and does not infer a zCloud VPS outage. The positive count is *only* evidence of an observed pending assignment — not live availability, restart authorization, or verified AI generation.

### Numeric observation envelopes cannot use truthy JSON placeholders

The read-only route summary independently recomputes every queue route total from strict run records. The reported `route_counts` object must now also contain **only positive, exact JSON integers**, never booleans or floats. Python compares `True == 1` and `1.0 == 1`; a plain dictionary equality check alone would accept those malformed source claims. Adversarial offline tests include JSON `true`, `false`, float, negative, zero, null, string and compound values. All noninteger buckets fail closed, with **no link or count printed to the operator**.

This is evidence integrity, not an authorization mechanism: valid numerics are not proof that two queued runs have identical dispatch intent, that the zCloud VPS listener is online, or that any production action is safe. Live recovery remains exclusively owned by the existing runner, SQLite and serialized production-release gates.

### Bounded GitHub identifiers and meaningful runner-name evidence

The GET-only route triage now treats GitHub run and job IDs as positive **signed 64-bit integers**. It refuses an out-of-range run ID **before requesting any job endpoint**, and an out-of-range job ID before generating route totals or starting the second pass. The operator summary independently refuses out-of-range run IDs rather than printing plausible-looking GitHub links made from arbitrary giant integers. Exact boundary and oversized adversarial fixtures exercise both sides.

A positive runner ID is **not** a trustworthy named assignment when GitHub's `runner_name` consists only of whitespace: that is recorded as `indeterminate`, never a healthy assigned runner. Control bytes (CR, LF, tab, NUL, DEL) inside otherwise bounded runner names are denied as malformed source evidence before a second pass or sanitized operator report. Legitimate explicit `null/null` and `0/""` *unassigned* records remain permitted; no raw runner names are printed. None of this is proof of a listener outage, generation activity, safe cancellation, or permission to restart/deploy.

### Signed runner-ID boundary and invisible-name denial

The same conservative signed-64-bit boundary now also applies to **runner IDs**, not just job/run IDs. A recorded runner ID above that range is rejected as malformed evidence, rather than proving a job has been assigned. A positive in-range runner ID with a whitespace-only or nonprinting/invisible Unicode runner name (for example bidirectional controls or a zero-width suffix) is not a credible named assignment. The collector denies these malformed names; the direct classification helper returns `indeterminate` if invoked separately. Regular printable internationalized runner names remain valid, but their raw value is never reflected in public operator summaries.

These checks guard against forged observations and ambiguous audit logs only: a valid positive runner ID and printable name still do **not** prove the listener is healthy or a zCloud worker is generating text. Existing exclusive restart, SQLite and production gates remain untouched.

## Exclusive owner integration criteria

1. Gate all event forms against a freshly fetched canonical repository main SHA, with a second after-read SHA. Require authenticated owner and serialized #580/PWQ-41 + #1089 + #1135 release controls. Context branch/SHA alone cannot attest current main.
2. Implement a separate GET-only API pagination collector following all GitHub run/job pages, validating Link navigation and totals, detecting cursor cycles, caps, HTTP errors, stale snapshots, duplicate IDs and main drift. Do not rely solely on caller-provided has_next or jobs_complete flags.
3. Do not cancel runs merely because workflow ID, path, event, ref and SHA match. Require independently authenticated same-intent identity and non-overlapping jobs; preserve all active, FTMO/HaxLab and manually requested work.
4. Negative offline tests must cover worker branch, PR, fork, manual actor, stale SHA, second-page overlaps, malformed headers, duplicate IDs, in-progress jobs, mixed runner labels and zero privilege use.
5. Real integration requires independent review, exact-head hosted acceptance, owner-admitted guarded VPS read-only proof, rollback plan and verified production status. The existing branch does not satisfy those gates.

**Disposition:** draft/unmerged/undeployed reference; privileged watchdog workflow not modified.

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

The separate collector reads the **canonical main SHA before and after**, fetches every available queued run page and every selected run's job pages (all requests strictly HTTP GET to api.github.com), verifies total counts and exact next-page links, and **re-reads every queue page** after the job inventory, rejecting same-count replacements on later pages and identity changes under the same run ID. Every job must also provide a valid `run_id` exactly matching its parent run, otherwise the entire snapshot is denied. Unknown/oversized totals, external links, duplicate JSON keys, moved main, missing jobs, race or timeout are denied with a fixed privacy-safe reason. It timestamps the **start** of the observation, rejects snapshots taking more than **45 seconds** before treating them as current, and has a separate **115-second** process wall-clock and maximum **640-request** budget. Proxy inheritance, HTTP redirects and dynamic destination hosts are disallowed. Tokens are never printed.

Run manually for observation only on a trusted shell with an appropriately scoped read-only token in the environment:

    GITHUB_TOKEN=<read-only-token> python3 scripts/zcloud_watchdog_queue_collector_20261014_w1.py

Do not place a real token in documentation, issue comments or command history. A complete GET snapshot still has no separately authenticated same-intent key and **cannot authorize cancellation**, even if identical queued runs are reported. CI only runs offline fakes and a fake HTTP opener, not real privileged API calls.

## Manual GitHub-hosted observer — never a production control

- .github/workflows/zcloud-watchdog-hosted-queue-readonly-20261014-w1.yml
- scripts/zcloud_watchdog_operator_summary_20261014_w1.py
- tests/test_zcloud_watchdog_operator_summary_20261014_w1.py
- tests/test_zcloud_watchdog_hosted_workflow_20261014_w1.py

This is a **new, separate, manual-only** workflow. It has no push, pull_request, schedule or workflow_run trigger, requires the exact canonical repository, actor Zennay, main ref and first attempt, and runs **only on GitHub-hosted ubuntu-latest**. GitHub permissions are exactly contents:read and actions:read; checkout is immutable with saved credentials disabled. The owner can start it from Actions after review and eventual main merge; **there is no currently deployed workflow to run** while this PR is draft.

Manual execution checks main's actual GitHub API SHA **both before and after** the bounded GET-only collector, and publishes a short, **redacted Dutch operator summary**. Reports with duplicate keys, oversized data, ambiguous exits, fake mutation permissions or alleged production verification are denied. No raw API response, prompt or credentials are printed in the summary. A REVIEW_ONLY or DENY decision intentionally makes the hosted job fail rather than imply approval.

This workflow provides **no** restart, runner repair, run cancellation, SQLite write, Firefox mutation, worker push or deploy. It does **not** replace the existing privileged watchdog owner; the fact that an observation is structurally stable does not prove duplicate run intent.

## Exclusive owner integration criteria

1. Gate all event forms against a freshly fetched canonical repository main SHA, with a second after-read SHA. Require authenticated owner and serialized #580/PWQ-41 + #1089 + #1135 release controls. Context branch/SHA alone cannot attest current main.
2. Implement a separate GET-only API pagination collector following all GitHub run/job pages, validating Link navigation and totals, detecting cursor cycles, caps, HTTP errors, stale snapshots, duplicate IDs and main drift. Do not rely solely on caller-provided has_next or jobs_complete flags.
3. Do not cancel runs merely because workflow ID, path, event, ref and SHA match. Require independently authenticated same-intent identity and non-overlapping jobs; preserve all active, FTMO/HaxLab and manually requested work.
4. Negative offline tests must cover worker branch, PR, fork, manual actor, stale SHA, second-page overlaps, malformed headers, duplicate IDs, in-progress jobs, mixed runner labels and zero privilege use.
5. Real integration requires independent review, exact-head hosted acceptance, owner-admitted guarded VPS read-only proof, rollback plan and verified production status. The existing branch does not satisfy those gates.

**Disposition:** draft/unmerged/undeployed reference; privileged watchdog workflow not modified.

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

## Exclusive owner integration criteria

1. Gate all event forms against a freshly fetched canonical repository main SHA, with a second after-read SHA. Require authenticated owner and serialized #580/PWQ-41 + #1089 + #1135 release controls. Context branch/SHA alone cannot attest current main.
2. Implement a separate GET-only API pagination collector following all GitHub run/job pages, validating Link navigation and totals, detecting cursor cycles, caps, HTTP errors, stale snapshots, duplicate IDs and main drift. Do not rely solely on caller-provided has_next or jobs_complete flags.
3. Do not cancel runs merely because workflow ID, path, event, ref and SHA match. Require independently authenticated same-intent identity and non-overlapping jobs; preserve all active, FTMO/HaxLab and manually requested work.
4. Negative offline tests must cover worker branch, PR, fork, manual actor, stale SHA, second-page overlaps, malformed headers, duplicate IDs, in-progress jobs, mixed runner labels and zero privilege use.
5. Real integration requires independent review, exact-head hosted acceptance, owner-admitted guarded VPS read-only proof, rollback plan and verified production status. The existing branch does not satisfy those gates.

**Disposition:** draft/unmerged/undeployed reference; privileged watchdog workflow not modified.

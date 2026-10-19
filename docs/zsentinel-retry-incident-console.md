# zSentinel · Queue retry incident console (#677)

Source: [scripts/zsentinel_retry_incident_console.py](../scripts/zsentinel_retry_incident_console.py)

## Product behavior

A standalone, **operator-initiated** snapshot of the actual `portfolio_queue` SQLite table, without importing `server.py`, starting the scheduler, or mutating the queue. Useful during incident #677, where `attempts` counts claim cycles and can reach thousands; it is not an error counter and must not authorize forced retries.

Reports total eligible and active work, work items with excessive claim attempts, per-project/P0–P3 and status breakdown, expired active leases, and a limited priority-sorted sample. Each sampled row carries a concrete next human/operator action; existing active claims are never treated as free ownership.

Three complete artifact formats:
- **HTML**: a self-contained responsive report, local/offline and without script, remote assets, credentials, or live API access.
- **Markdown**: handoff-friendly backlog and decision summary.
- **JSON**: bounded machine-readable evidence with explicit source, observation time, coverage and non-authorizing limitations.

## Safe invocation

Run only with explicit access to an existing local database path. The CLI is independent of deployment and intentionally **never discovers paths or service credentials**.

```sh
python3 scripts/zsentinel_retry_incident_console.py \
  --database /path/to/existing/zcloud.sqlite3 --format html \
  --output ./zsentinel-retry-report.html

python3 scripts/zsentinel_retry_incident_console.py \
  --database /path/to/existing/zcloud.sqlite3 --project supa \
  --threshold 3 --limit 40 --format markdown
```

The database opens using SQLite `mode=ro` with `PRAGMA query_only=ON`; grouped and sample reads share one read transaction. Read-only queries may read existing WAL content; no `immutable=1` flag hides pending WAL. The only optional filesystem write is the operator-selected **report output** path.

Reports include a short pseudonymous `queue_ref` so operators can correlate the same item across different snapshots without exporting raw IDs. To locate the exact task for authorized **local** investigation, explicitly resolve one reference:

```sh
python3 scripts/zsentinel_retry_incident_console.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --resolve-ref 0123456789abcdef
```

This drill-down prints the current raw queue ID and stored task title to the **local console only**, does not support file export, refuses missing/ambiguous references and inventories over 50,000 records, and never conveys ownership or retry permission. Do not paste its sensitive result into public PR comments.

Use `--include-titles` only for an explicitly approved **local** JSON/Markdown review; by default reports exclude task titles, raw `queue_id`, lease ownership IDs, prompt/criteria, evidence, metadata, and tokens. HTML does not offer the title override and has all displayed values escaped.

If the DB schema is missing required columns, the path does not exist, or SQLite cannot establish a read-only consistent snapshot, the command exits non-zero and produces no fake empty success report. Maximum sample size is 100; threshold and project arguments are validated; it does not scan external providers or fetch GitHub API.

## Operational interpretation

1. Review **P0/P1 high-attempt queued** rows first. A large attempts count suggests potential churn, not necessarily a failed job. Compare with exact queue status and worker/claim evidence before proposing a change.
2. For **claimed/running/verifying** rows, respect the current owner and lease, including expired timestamps until actual reconciliation authorization is verified. Never take over work based only on this report.
3. For **expired leases**, request a separate authorized controller evaluation. This command does not expire, delete, requeue, reset attempts, restart a runner or dispatch an Actions job.
4. Source-only report availability is not runner recovery, automatic backoff, merge approval, CI acceptance or production deployment.

## Remaining writer fix

Issue [#677](https://github.com/Zennay/zCloud/issues/677) still requires a serialized runtime transition model: known-transient-only retry dispositions, durable `retry_not_before`, finite retry budget and stable claim/correlation identifiers, with exhausted retries admitted to `NEEDS_AI`. This standalone read model deliberately does **not** implement an unauthorized queue writer during the separate owner/release window. No new QA or regression suite was authored or invoked for this change.

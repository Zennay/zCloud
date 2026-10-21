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

The database opens using SQLite `mode=ro` with `PRAGMA query_only=ON`; grouped and sample reads share one read transaction. Read-only queries may read existing WAL content; no `immutable=1` flag hides pending WAL. The only optional filesystem write is the operator-selected **report output**
path. Reports are now **create-only by default**: both the live-SQLite
snapshot CLI and the offline trend CLI first write a mode-0600 temporary file
in the selected report directory, then atomically create the final report
filename **only if it does not already exist**. Neither CLI opens a prior
report, source snapshot, SQLite database, WAL/SHM sidecar, hardlink alias,
existing symlink or unrelated file for writing. A conflicting destination
returns a clear error; choose a fresh report filename. A failed publication
cleans up its temporary file. This protects local source evidence even if an
operator accidentally chooses the SQLite database or one of the trend input
JSON files as `--output` — previous direct `write_text` behavior could
truncate those files despite the read-only database connection. It does not
grant any permission to mutate queue or worker state.

**Operator sequence:** choose unique output names for each observation
(e.g. `retry-before-20261019.json` and `retry-after-20261019.json`).
Use `scripts/zsentinel_retry_trend.py --previous ... --current ...
--format html --output ...` with a *third new* report name. Keep the two
source snapshots for later comparison, and use the resulting HTML, Markdown
or JSON only as bounded non-authorizing evidence.

Reports include a short pseudonymous `queue_ref` so operators can correlate the same item across different snapshots without exporting raw IDs. To locate the exact task for authorized **local** investigation, explicitly resolve one reference:

```sh
python3 scripts/zsentinel_retry_incident_console.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --resolve-ref 0123456789abcdef
```

This drill-down prints the current raw queue ID and stored task title to the **local console only**, does not support file export, refuses missing/ambiguous references and inventories over 50,000 records, and never conveys ownership or retry permission. Do not paste its sensitive result into public PR comments.

Use `--include-titles` only for an explicitly approved **local** JSON/Markdown review; by default reports exclude task titles, raw `queue_id`, lease ownership IDs, prompt/criteria, evidence, metadata, and tokens. HTML does not offer the title override and has all displayed values escaped.

If the DB schema is missing required columns, the path does not exist, or SQLite cannot establish a read-only consistent snapshot, the command exits non-zero and produces no fake empty success report. The console reads at most one extra row beyond the requested sample so `sample_is_complete` is true even when the complete set contains exactly `--limit` entries. It never exports that lookahead row. Maximum exported sample size is 100; threshold and project arguments are validated; it does not scan external providers or fetch GitHub API.

## Show change over time (independent offline trend console)

Two operator-saved JSON snapshots with **exactly the same project and threshold**
can be compared without opening the running database:

```sh
python3 scripts/zsentinel_retry_incident_console.py \
  --database /path/to/existing/zcloud.sqlite3 --format json \
  --output ./retry-before.json
# At a later explicitly operator-chosen time, run the same command with
# --output ./retry-after.json; there is NO implicit scheduler or poller.
python3 scripts/zsentinel_retry_trend.py \
  --previous ./retry-before.json --current ./retry-after.json \
  --format html --output ./retry-trend.html
```

The trend console in [scripts/zsentinel_retry_trend.py](../scripts/zsentinel_retry_trend.py)
provides current-vs-previous active-item counts, high attempts, expired leases,
and a per-project rise/fall ranking. **It now also correlates the same pseudonymous
queue reference in both bounded samples**, highlights individual claim-cycle increases,
status/priority changes and suspicious counter decreases, and shows explicit matched,
earlier-only and later-only coverage in standalone HTML, Markdown and structured JSON.
This is an operational answer to “which item kept churning?” rather than only
“which project's aggregate got bigger?”. Raw queue IDs and optional task titles
are never copied into the trend outputs.

The per-item movement is **observed only for rows in BOTH samples**. A sampled
item disappearing from the top-N list does **not** establish completion, deletion,
claim release or authority to retry; a newly sampled item does not prove queue
creation. The command reports sample limits and complete/partial coverage;
claim counters reflect dispatch/claim cycles, not confirmed failures. The
`record_changes` list is capped at the saved snapshot sample limit (maximum 100).
It also exports Markdown and JSON.
It rejects incomplete/noncanonical schema, unbounded >1 MB snapshots, missing or
unordered timestamps, and mismatched scope/threshold instead of displaying an
invented trend. Local JSON inputs and a selected output path are the only file
operations. No network, worker/queue, claim, sqlite or production mutations.
Two observations do **not** establish why counts changed or grant retry authority.

## Operational interpretation

1. Review **P0/P1 high-attempt queued** rows first. A large attempts count suggests potential churn, not necessarily a failed job. Compare with exact queue status and worker/claim evidence before proposing a change.
2. For **claimed/running/verifying** rows, respect the current owner and lease, including expired timestamps until actual reconciliation authorization is verified. Never take over work based only on this report.
3. For **expired leases**, request a separate authorized controller evaluation. This command does not expire, delete, requeue, reset attempts, restart a runner or dispatch an Actions job.
4. Source-only report availability is not runner recovery, automatic backoff, merge approval, CI acceptance or production deployment.

## Remaining writer fix

Issue [#677](https://github.com/Zennay/zCloud/issues/677) still requires a serialized runtime transition model: known-transient-only retry dispositions, durable `retry_not_before`, finite retry budget and stable claim/correlation identifiers, with exhausted retries admitted to `NEEDS_AI`. This standalone read model deliberately does **not** implement an unauthorized queue writer during the separate owner/release window. No new QA or regression suite was authored or invoked for this change.

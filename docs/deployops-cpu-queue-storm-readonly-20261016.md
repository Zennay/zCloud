# zCloud VPS CPU diagnostic queue-storm evidence (read-only)

Scope: `Zennay/zCloud` [issue #1326](https://github.com/Zennay/zCloud/issues/1326) and repeated `vps-cpu-diagnostic.yml` PR-triggered self-hosted snapshots; **not** listener-recovery ownership. Existing PR #1497 owns the listener-gap-vs-approval classifier and is untouched. Issue #1447 owns production writer locks.

This independent tool detects duplicate *queued* CPU snapshots sharing identical head SHA, branch and event. It also measures **cross-PR pressure**, even if every queued head is different: bounded counts of distinct unassigned heads, PR-versus-manual events, and oldest unassigned job age. These are sample-only statistics, never the entire runner backlog. One queued snapshot is **not** proof of a dead listener; duplicates are **never** authorization to cancel jobs, restart the listener, dispatch, merge or deploy. The live CPU diagnostic workflow remains owned by its existing admission branches.

## Usage by an authorized operator

Supply a read-only token through the existing secret mechanism; never print or commit it.

```bash
GH_TOKEN=<read-only-token> python3 scripts/zcloud_cpu_queue_storm_audit_20261016.py --sample 20
```

Only bounded GitHub GET calls are made for queued CPU workflow runs and each run's single job. No VPS execution, SQLite, Firefox, runner, worker or production mutation. `--sample` is bounded to 1–30. Results are observed twice against one timestamp. If source state changes, the result is `EVIDENCE_CHANGED` and duplicate-group evidence is suppressed. Partial samples explicitly set `complete=false`, status `PARTIAL_READONLY_SAMPLE` and **exit code 2**; no full-population inference is allowed. Exit code 0 requires BOTH an unchanged double observation and `complete=true`. Invalid/malformed source responses return sanitized denial JSON, not a traceback. Invalid or ambiguous GitHub evidence exits 2 with a sanitized `EVIDENCE_UNAVAILABLE` receipt. Boolean IDs/job-page counts, executing unassigned jobs and jobs assigned or executing during parent approval/prestart all fail closed; job API URLs must bind to the exact run and job IDs. The tool never prints token values or GitHub responses.

The following receipt fields **always** remain false: `runner_offline_verified`, `vps_health_verified` (in stable receipts), `cancel_authorized`, `deploy_authorized`, `mutation_performed`.

Do not interpret shared-SHA groups as cancel candidates. A queued job may be waiting for another GitHub admission condition or an in-progress runner assignment. Ask the #1326 recovery owner to check repository runner registration, group/label access and active `Runner.Worker` on a trusted independent VPS channel. Do not turn the auditor into another queued VPS diagnostic workflow.

## Offline verification

```bash
python3 -m unittest -v tests.test_zcloud_cpu_queue_storm_audit_20261016
```

The own PR workflow runs *offline tests only*, on `ubuntu-latest` without a token written to checkout. It does not collect live queue data automatically. Merge and production decisions remain serialized under #580/PWQ-41, #1089, #922, #1447 and runner issue #1326.

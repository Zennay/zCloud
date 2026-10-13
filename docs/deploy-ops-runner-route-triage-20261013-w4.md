# zCloud deploy-ops — runner route triage (#1326)

A GitHub Actions job queued with `runner_id: 0` is **not proof** that the VPS listener is offline. The runner may be busy, labels may not match, repository access may be missing, or the job may not be assigned despite an available runner.

## Safe standalone diagnostic

```bash
python3 scripts/zcloud_runner_route_triage_20261013.py --fixture snapshot.json
# Real GitHub read-only GET calls (requires a token authorized for Actions and repository runners):
GITHUB_TOKEN=... python3 scripts/zcloud_runner_route_triage_20261013.py --live
```

The offline snapshot object must contain `repository` (`Zennay/zCloud`), UTC `captured_at`, GitHub API `runners` and `queued_runs` arrays, plus `jobs_by_run` keyed by decimal run ID. `--fixture` never touches GitHub or the VPS. `--live` calls only `GET /actions/runners`, `GET /actions/runs?status=queued`, and `GET /actions/runs/{id}/jobs`, with bounded pages, payload sizes and timeouts. A token unable to read runners returns *unknown*, never an inferred offline status.

The emitted JSON contains only aggregate counts and a coarse classification, not issue bodies, workflow log text, GitHub token, private job payload, tab URL or prompt. Codes: **0** = no old unassigned self-hosted jobs observed; **1** = stale unassigned jobs found; **2** = incomplete/invalid evidence (deny). This is *not* a runner-health guarantee or deploy readiness.

## Release / ownership

Do not auto-cancel old queued runs based on workflow names: multiple distinct jobs in the same workflow may be intentional. Do not automatically restart `actions.runner.*`, touch Firefox, run SQLite reconciliation, dispatch prompts, mutate zCloud state or deploy. All four authorization flags in the report remain false, including when the runner appears available.

Runner recovery remains owned by [#1326](https://github.com/Zennay/zCloud/issues/1326). Production safety constraints and high load/swap pressure are tracked in the [Notion handoff](https://app.notion.com/p/3e79e19ac955819e9ccee5bec98bbb9c); exclusive `server.py` / release ownership belongs to #580/PWQ-41 and #1089. This branch changes no shared workflows, server, worker, scheduler, services or production database.

**Tests**: `python3 -m unittest discover -s tests -p 'test_zcloud_runner_route_triage_20261013.py' -v`. An explicit runner-group binding on a queued job is classified as unverified even when labels appear to match; the standalone snapshot cannot authorize runner-group admission. Job timestamps more than two minutes in the future are rejected. Runner-group membership/label availability is inferred only from the authenticated repository-specific GitHub API runner listing at observation time, never from another repository's runner success. No automatic remediation should consume this output as authority.

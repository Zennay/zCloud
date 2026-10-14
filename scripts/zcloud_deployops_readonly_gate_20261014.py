#!/usr/bin/env python3
"""Read-only exact-main deploy health receipt; NEVER authorizes recovery or release.

The GitHub endpoints and repo are fixed, observations are bounded, and unknown,
contradictory or incomplete evidence fails closed instead of guessing VPS health.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from typing import Any

REPO = "Zennay/zCloud"
WORKFLOW = ".github/workflows/zcloud-vps-deploy.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")
ID_MAX = 2**53 - 1
VALID_JOB_STATUS = {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
VALID_RUN_STATUS = VALID_JOB_STATUS
VALID_CONCLUSIONS = {"success", "failure", "cancelled", "timed_out", "skipped",
                     "neutral", "action_required", "stale", "startup_failure"}
VALID_PRODUCTION_STATUS = {"success", "failure", "pending", "error"}
ROOT = "https://api.github.com/repos/" + REPO
MAX_RUNS = 15
MAX_BYTES = 1_000_000


class EvidenceError(Exception):
    pass


def positive_id(value: Any) -> int:
    if type(value) is not int or not 0 < value <= ID_MAX:
        raise EvidenceError("invalid_identity")
    return value


def sha(value: Any) -> str:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise EvidenceError("invalid_sha")
    return value


def utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EvidenceError("invalid_timestamp")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("invalid_timestamp") from exc
    if result.utcoffset() != dt.timedelta(0):
        raise EvidenceError("invalid_timestamp")
    return result


def strict_pairs(items: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def reject_nonfinite(value: str) -> None:
    raise EvidenceError("nonfinite_json")


def load_json(data: bytes | str) -> dict:
    try:
        payload = json.loads(data, object_pairs_hook=strict_pairs, parse_constant=reject_nonfinite)
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise EvidenceError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("invalid_root")
    return payload


def classify(source: dict) -> dict:
    """Classify supplied *read-only* GitHub snapshot, never approve a mutation."""
    main_sha = sha(source.get("main_sha"))
    if source.get("repo") != REPO or sha(source.get("main_sha_after")) != main_sha:
        raise EvidenceError("unbound_main")
    status = source.get("status")
    if not isinstance(status, dict) or sha(status.get("sha")) != main_sha:
        raise EvidenceError("unbound_status")
    statuses = status.get("statuses")
    if not isinstance(statuses, list) or not 1 <= len(statuses) <= 100:
        raise EvidenceError("incomplete_statuses")
    seen_status_ids: set[int] = set()
    production = []
    for item in statuses:
        if not isinstance(item, dict):
            raise EvidenceError("invalid_status")
        ident = positive_id(item.get("id"))
        if ident in seen_status_ids:
            raise EvidenceError("duplicate_status_id")
        seen_status_ids.add(ident)
        if item.get("context") == "zcloud/vps-production":
            state = item.get("state")
            if state not in VALID_PRODUCTION_STATUS or not isinstance(state, str):
                raise EvidenceError("invalid_production_state")
            production.append((utc(item.get("created_at")), ident, state))
    # No status is a genuine absence, not a successful deployment.
    latest = sorted(production)[-1][2] if production else "absent"

    runs_payload = source.get("runs")
    if not isinstance(runs_payload, dict):
        raise EvidenceError("missing_runs")
    total = runs_payload.get("total_count")
    runs = runs_payload.get("workflow_runs")
    if type(total) is not int or not 0 <= total <= MAX_RUNS:
        raise EvidenceError("unbounded_runs")
    if not isinstance(runs, list) or len(runs) != total:
        raise EvidenceError("incomplete_runs")
    jobs_by_run = source.get("jobs_by_run")
    if not isinstance(jobs_by_run, dict):
        raise EvidenceError("missing_jobs")
    seen_runs: set[int] = set()
    verified = []
    for run in runs:
        if not isinstance(run, dict):
            raise EvidenceError("invalid_run")
        ident = positive_id(run.get("id"))
        if ident in seen_runs:
            raise EvidenceError("duplicate_run_id")
        seen_runs.add(ident)
        if run.get("path") != WORKFLOW or run.get("head_branch") != "main":
            raise EvidenceError("wrong_workflow")
        sha(run.get("head_sha"))
        attempt = positive_id(run.get("run_attempt"))
        phase = run.get("status")
        conclusion = run.get("conclusion")
        if phase not in VALID_RUN_STATUS or not isinstance(phase, str):
            raise EvidenceError("unknown_run_state")
        if phase == "completed":
            if conclusion not in VALID_CONCLUSIONS or not isinstance(conclusion, str):
                raise EvidenceError("unknown_conclusion")
        elif conclusion is not None:
            raise EvidenceError("contradictory_conclusion")
        created = utc(run.get("created_at"))
        jobs = jobs_by_run.get(str(ident))
        if not isinstance(jobs, dict):
            raise EvidenceError("missing_job_snapshot")
        count = jobs.get("total_count")
        rows = jobs.get("jobs")
        if type(count) is not int or not 0 <= count <= 100 or not isinstance(rows, list) or len(rows) != count:
            raise EvidenceError("incomplete_job_snapshot")
        seen_jobs: set[int] = set()
        queued_unassigned = 0
        failed_jobs = 0
        for job in rows:
            if not isinstance(job, dict) or positive_id(job.get("run_id")) != ident:
                raise EvidenceError("cross_run_job")
            job_id = positive_id(job.get("id"))
            if job_id in seen_jobs:
                raise EvidenceError("duplicate_job_id")
            seen_jobs.add(job_id)
            job_phase = job.get("status")
            if not isinstance(job_phase, str) or job_phase not in VALID_JOB_STATUS:
                raise EvidenceError("unknown_job_state")
            runner_id = job.get("runner_id")
            runner_name = job.get("runner_name")
            if runner_id is not None and (type(runner_id) is not int or runner_id < 0):
                raise EvidenceError("invalid_runner")
            if runner_name is not None and not isinstance(runner_name, str):
                raise EvidenceError("invalid_runner")
            if job_phase == "queued" and runner_id in (None, 0) and not runner_name:
                queued_unassigned += 1
            if job_phase == "completed":
                final = job.get("conclusion")
                if final not in VALID_CONCLUSIONS or not isinstance(final, str):
                    raise EvidenceError("invalid_job_conclusion")
                if final == "failure":
                    failed_jobs += 1
            elif job.get("conclusion") is not None:
                raise EvidenceError("job_contradiction")
        verified.append((created, ident, attempt, run, queued_unassigned, failed_jobs))
    if set(jobs_by_run) != {str(x) for x in seen_runs}:
        raise EvidenceError("extra_job_snapshot")
    verified.sort(reverse=True)
    current = [v for v in verified if v[3]["head_sha"] == main_sha]
    latest_run = current[0] if current else None
    outcome = ("STATUS_GREEN_UNVERIFIED" if latest == "success"
               else "PRODUCTION_STATUS_" + latest.upper())
    if latest_run:
        run = latest_run[3]
        if run["status"] == "completed" and run["conclusion"] != "success":
            outcome = "LATEST_MAIN_DEPLOY_" + run["conclusion"].upper()
        elif latest_run[4] and run["status"] == "queued":
            outcome = "DEPLOY_JOB_QUEUED_UNASSIGNED"
    return {
        "policy": "zcloud-deployops-read-only-gate-v1",
        "repo": REPO,
        "main_sha": main_sha,
        "production_status": latest,
        "observation": outcome,
        "current_main_deploy_run": latest_run[1] if latest_run else None,
        "current_main_deploy_attempt": latest_run[2] if latest_run else None,
        "latest_run_failed_jobs": latest_run[5] if latest_run else None,
        "latest_run_queued_unassigned_jobs": latest_run[4] if latest_run else None,
        "scanned_deploy_runs": len(verified),
        "status_is_runtime_proof": False,
        "runner_outage_verified": False,
        "release_authorized": False,
        "mutation_authorized": False,
        "production_verified": False,
    }


def safe_report(source: dict) -> dict:
    try:
        return classify(source)
    except (EvidenceError, TypeError, KeyError, OverflowError):
        return {
            "policy": "zcloud-deployops-read-only-gate-v1",
            "observation": "DENY_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE",
            "release_authorized": False,
            "mutation_authorized": False,
            "production_verified": False,
            "runner_outage_verified": False,
        }


def api(path: str, token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", token, flags=re.ASCII):
        raise EvidenceError("invalid_token")
    permitted = (
        "/branches/main",
        "/commits/main/status",
        "/actions/workflows/zcloud-vps-deploy.yml/runs?branch=main&per_page=15",
    )
    if path not in permitted and not re.fullmatch(r"/actions/runs/[1-9][0-9]{0,15}/jobs\?per_page=100", path):
        raise EvidenceError("unknown_api_route")
    request = urllib.request.Request(
        ROOT + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "zcloud-deployops-readonly-gate"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise EvidenceError("oversized_response")
    return load_json(content)


def live_snapshot(token: str) -> dict:
    before = sha(api("/branches/main", token)["commit"]["sha"])
    status = api("/commits/main/status", token)
    runs = api("/actions/workflows/zcloud-vps-deploy.yml/runs?branch=main&per_page=15", token)
    count = runs.get("total_count")
    # A large historical workflow is expected; only request a bounded newest page.
    entries = runs.get("workflow_runs")
    if not isinstance(entries, list) or len(entries) > MAX_RUNS or type(count) is not int or count < len(entries):
        raise EvidenceError("unbounded_runs")
    jobs = {}
    for item in entries:
        ident = positive_id(item.get("id"))
        jobs[str(ident)] = api(f"/actions/runs/{ident}/jobs?per_page=100", token)
    after = sha(api("/branches/main", token)["commit"]["sha"])
    return {"repo": REPO, "main_sha": before, "main_sha_after": after,
            "status": status, "runs": {"total_count": len(entries), "workflow_runs": entries},
            "jobs_by_run": jobs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", help="Offline complete evidence snapshot JSON")
    group.add_argument("--live", action="store_true", help="GET-only, GitHub token with actions:read")
    args = parser.parse_args(argv)
    try:
        if args.snapshot:
            from pathlib import Path
            raw = Path(args.snapshot).read_bytes()
            if len(raw) > MAX_BYTES:
                raise EvidenceError("oversized_input")
            snapshot = load_json(raw)
        else:
            snapshot = live_snapshot(os.environ.get("GITHUB_TOKEN", ""))
        result = safe_report(snapshot)
    except (OSError, EvidenceError, ValueError, KeyError, TypeError, TimeoutError):
        result = safe_report({})
    print(json.dumps(result, sort_keys=True))
    return 0 if result["observation"] != "DENY_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE" else 2


if __name__ == "__main__":
    sys.exit(main())

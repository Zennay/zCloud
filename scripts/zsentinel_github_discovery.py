#!/usr/bin/env python3
"""zSentinel read-only source discovery: queued jobs within queued/running workflows, then failures.

The caller gets one verified (project/run/job/SHA) candidate per repository.
All requests are canonical GitHub GETs, bounded, without redirect replay.
This is an INCOMPLETE sample, never proof that other workflows are healthy.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from zsentinel_product_issue_inbox import bounded_get
from zsentinel_recovery_router import utc_string
from zsentinel_runner_route import runner_route

UTC = timezone.utc
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_PROJECTS = 3
MAX_RUNS = 5
MAX_JOBS = 10
RUN_PAGES_PER_WINDOW = 2
RUN_WINDOWS = ("queued", "in_progress", "failure")
MAX_GETS_PER_PROJECT = len(RUN_WINDOWS) * (1 + RUN_PAGES_PER_WINDOW)
RESULTS = {"failure", "timed_out", "action_required"}


def parse_time(value):
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("invalid GitHub job clock")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid GitHub job clock") from exc
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("GitHub job clock requires UTC offset")
    return date.astimezone(UTC)


def recent_runs(root, canonical_repository, token, status):
    query = urlencode({"status": status, "per_page": MAX_RUNS, "page": 1})
    result = bounded_get(f"{root}/actions/runs?{query}", token)
    if (not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list)
            or len(result["workflow_runs"]) > MAX_RUNS):
        raise ValueError("GitHub workflow list shape or bounds invalid")
    selected = []
    for run in result["workflow_runs"]:
        if not isinstance(run, dict):
            continue
        rid, sha = run.get("id"), run.get("head_sha")
        repository = run.get("repository")
        if (type(rid) is not int or not 1 <= rid <= 2**53 - 1
                or not isinstance(sha, str) or not SHA.fullmatch(sha)
                or not isinstance(repository, dict)
                or repository.get("full_name") != canonical_repository):
            continue
        selected.append((rid, sha))
    return selected[:RUN_PAGES_PER_WINDOW]


def run_job_candidates(root, run_id, sha, token, kind):
    query = urlencode({"per_page": MAX_JOBS, "page": 1})
    result = bounded_get(f"{root}/actions/runs/{run_id}/jobs?{query}", token)
    if (not isinstance(result, dict) or not isinstance(result.get("jobs"), list)
            or len(result["jobs"]) > MAX_JOBS):
        raise ValueError("GitHub jobs shape or bounds invalid")
    candidates = []
    for job in result["jobs"]:
        if not isinstance(job, dict):
            continue
        jid, actual_run = job.get("id"), job.get("run_id")
        status, conclusion, runner = (
            job.get("status"), job.get("conclusion"), job.get("runner_id")
        )
        if (type(jid) is not int or not 1 <= jid <= 2**53 - 1
                or actual_run != run_id
                or (runner is not None and
                    (type(runner) is not int or runner < 0))):
            continue
        created = parse_time(job.get("created_at"))
        now = datetime.now(UTC)
        if created > now + timedelta(minutes=2):
            continue
        if kind in ("queued", "in_progress"):
            if status != "queued" or bool(runner) or now-created < timedelta(minutes=30):
                continue
            rank = 0
        else:
            if status != "completed" or conclusion not in RESULTS:
                continue
            rank = 1
        route = runner_route(job)
        candidates.append((rank, created, run_id, jid, sha, status, conclusion, runner, route, kind))
    return candidates


def discover(registry, selected, token=None):
    """At most nine GitHub GETs per project and one actionable sampled job."""
    if (not isinstance(selected, list) or not 1 <= len(selected) <= MAX_PROJECTS
            or len(set(selected)) != len(selected)
            or any(pid not in registry for pid in selected)):
        raise ValueError("invalid canonical CI-discovery project selection")
    receipts = {}
    coverage = []
    request_count = 0
    for pid in selected:
        canonical_repository = registry[pid]
        root = f"https://api.github.com/repos/{canonical_repository}"
        candidates = []
        checked = 0
        try:
            # Jobs can remain queued even after other jobs make the *workflow*
            # in_progress. Inspect both workflow windows before old failures;
            # otherwise a stuck self-hosted job can disappear from P0 view.
            for status in RUN_WINDOWS:
                request_count += 1
                run_ids = recent_runs(root, canonical_repository, token, status)
                for run_id, sha in run_ids:
                    request_count += 1
                    checked += 1
                    candidates.extend(run_job_candidates(root, run_id, sha, token, status))
                if candidates:
                    break
            if not candidates:
                coverage.append({
                    "project_id": pid, "status": "no_actionable_job_in_sampled_windows",
                    "run_pages_checked": checked, "authoritative_all_clear": False
                })
                continue
            candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
            _, created, run_id, job_id, sha, status, conclusion, runner, route, window = candidates[0]
            receipt = {
                "run_id": run_id, "job_id": job_id, "head_sha": sha,
                "job_status": status, "queued_at": utc_string(created),
                "observed_at": utc_string(datetime.now(UTC)),
                "runner_assigned": bool(runner),
                "verified_receipt": "github_bounded_run_jobs_discovery",
                "runner_route": route["runner_route"],
                "route_labels": route["route_labels"],
            }
            if status == "completed":
                receipt["conclusion"] = conclusion
            receipts[pid] = receipt
            coverage.append({
                "project_id": pid, "status": "selected_one_source_bound_job",
                "run_id": run_id, "job_id": job_id,
                "run_pages_checked": checked, "workflow_status_window": window,
                "authoritative_all_clear": False
            })
        except (OSError, ValueError):
            coverage.append({
                "project_id": pid, "status": "degraded_unavailable",
                "run_pages_checked": checked, "authoritative_all_clear": False
            })
    return receipts, {
        "source": "github_canonical_readonly_bounded_discovery",
        "request_count": request_count,
        "maximum_requests": len(selected) * MAX_GETS_PER_PROJECT,
        "run_windows": list(RUN_WINDOWS),
        "max_runs_per_window": MAX_RUNS,
        "run_pages_per_window": RUN_PAGES_PER_WINDOW,
        "max_jobs_per_run": MAX_JOBS,
        "coverage": coverage,
        "full_repo_ci_inventory": False,
        "jobs_restarted": False, "runner_mutations": False,
    }

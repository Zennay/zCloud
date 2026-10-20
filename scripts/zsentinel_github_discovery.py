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
    partial = False
    for run in result["workflow_runs"]:
        if not isinstance(run, dict):
            partial = True
            continue
        rid, sha = run.get("id"), run.get("head_sha")
        repository = run.get("repository")
        if (type(rid) is not int or not 1 <= rid <= 2**53 - 1
                or not isinstance(sha, str) or not SHA.fullmatch(sha)
                or not isinstance(repository, dict)
                or repository.get("full_name") != canonical_repository):
            partial = True
            continue
        selected.append((rid, sha))
    return selected[:RUN_PAGES_PER_WINDOW], partial


def run_job_candidates(root, run_id, sha, token, kind):
    """Keep usable jobs when a *different* returned job has corrupt metadata.

    A malformed record degrades coverage; it never turns partial results into
    a zero-incident finding and cannot suppress valid queued runner evidence.
    """
    query = urlencode({"per_page": MAX_JOBS, "page": 1})
    result = bounded_get(f"{root}/actions/runs/{run_id}/jobs?{query}", token)
    if (not isinstance(result, dict) or not isinstance(result.get("jobs"), list)
            or len(result["jobs"]) > MAX_JOBS):
        raise ValueError("GitHub jobs shape or bounds invalid")
    candidates = []
    partial = False
    for job in result["jobs"]:
        if not isinstance(job, dict):
            partial = True
            continue
        jid, actual_run = job.get("id"), job.get("run_id")
        status, conclusion, runner = (
            job.get("status"), job.get("conclusion"), job.get("runner_id")
        )
        if (type(jid) is not int or not 1 <= jid <= 2**53 - 1
                or actual_run != run_id
                or (runner is not None and
                    (type(runner) is not int or not 0 <= runner <= 2**53 - 1))):
            partial = True
            continue
        if kind in ("queued", "in_progress"):
            if status != "queued" or bool(runner):
                continue
        elif status != "completed" or conclusion not in RESULTS:
            continue
        try:
            created = parse_time(job.get("created_at"))
            route = runner_route(job)
        except ValueError:
            partial = True
            continue
        now = datetime.now(UTC)
        if created > now + timedelta(minutes=2):
            partial = True
            continue
        if status == "queued" and now - created < timedelta(minutes=30):
            continue
        # A verified self-hosted queue has higher priority than a hosted
        # queue. Invalid labels do not erase unrelated well-formed jobs.
        rank = ({"self_hosted": 0, "unknown": 1, "hosted_or_custom": 2}[
            route["runner_route"]] if status == "queued" else 3)
        candidates.append((rank, created, run_id, jid, sha, status, conclusion, runner, route, kind))
    return candidates, partial


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
        incomplete_windows = []
        attempted_windows = []
        stop_reason = None
        try:
            # Jobs can remain queued even after other jobs make the *workflow*
            # in_progress. Inspect both workflow windows before old failures;
            # otherwise a stuck self-hosted job can disappear from P0 view.
            for status in RUN_WINDOWS:
                attempted_windows.append(status)
                try:
                    request_count += 1
                    run_ids, partial_runs = recent_runs(
                        root, canonical_repository, token, status)
                    if partial_runs and status not in incomplete_windows:
                        incomplete_windows.append(status)
                    for run_id, sha in run_ids:
                        request_count += 1
                        checked += 1
                        discovered_jobs, partial_jobs = run_job_candidates(
                            root, run_id, sha, token, status)
                        candidates.extend(discovered_jobs)
                        if partial_jobs and status not in incomplete_windows:
                            incomplete_windows.append(status)
                except (OSError, ValueError) as exc:
                    # Keep independently observed exact job candidates. Repeating
                    # the same forbidden or rate-limited repository request
                    # across every source window provides no new operator value.
                    # Other canonical projects still receive their own bounded
                    # attempt; a 403 may be repository-specific.
                    if status not in incomplete_windows:
                        incomplete_windows.append(status)
                    if str(exc) in (
                            "github_permission_or_rate_limit",
                            "repository_inaccessible",
                            "github_network_unavailable"):
                        stop_reason = str(exc)
                        break
                # A queued-workflow candidate must not suppress discovery
                # of a stronger self-hosted queue in a RUNNING workflow.
                # Once both waiting windows were sampled, failures are a
                # fallback only if no queued job was found.
                if status == "in_progress" and candidates:
                    break
            if not candidates:
                coverage.append({
                    "project_id": pid, "status": (
                        "degraded_unavailable" if incomplete_windows
                        else "no_actionable_job_in_sampled_windows"),
                    "run_pages_checked": checked,
                    "incomplete_windows": incomplete_windows,
                    "not_scanned_windows": [
                        win for win in RUN_WINDOWS if win not in attempted_windows],
                    "repository_stop_reason": stop_reason,
                    "authoritative_all_clear": False
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
                "job_assignment_stage": route["job_assignment_stage"],
                "assignment_source_complete": route["assignment_source_complete"],
                "runner_healthy_verified": False,
                "worker_resumed_verified": False,
            }
            if status == "completed":
                receipt["conclusion"] = conclusion
            receipts[pid] = receipt
            coverage.append({
                "project_id": pid, "status": (
                    "selected_one_source_bound_job_partial_window" if incomplete_windows
                    else "selected_one_source_bound_job"),
                "run_id": run_id, "job_id": job_id,
                "run_pages_checked": checked, "workflow_status_window": window,
                "incomplete_windows": incomplete_windows,
                "not_scanned_windows": [
                    win for win in RUN_WINDOWS if win not in attempted_windows],
                "repository_stop_reason": stop_reason,
                "authoritative_all_clear": False
            })
        except (OSError, ValueError):
            coverage.append({
                "project_id": pid, "status": "degraded_unavailable",
                "run_pages_checked": checked,
                "not_scanned_windows": [
                    win for win in RUN_WINDOWS if win not in attempted_windows],
                "repository_stop_reason": stop_reason,
                "authoritative_all_clear": False
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
        "provider_admission_denials": sum(
            bool(item.get("repository_stop_reason"))
            for item in coverage),
        "per_repository_denial_stops_further_requests": True,
        "jobs_restarted": False, "runner_mutations": False,
    }

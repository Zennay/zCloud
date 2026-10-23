#!/usr/bin/env python3
"""Read-only GitHub-hosted queue watch, never proof of VPS health or repair authority."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

REPO = "Zennay/zCloud"
PATH = ".github/workflows/vps-cpu-diagnostic.yml"
NAME = "VPS CPU diagnostic snapshot"
LIMIT = 30
MIN_AGE = 1200
MAX_AGE = 24 * 3600
URL = f"https://api.github.com/repos/{REPO}/actions/workflows/vps-cpu-diagnostic.yml/runs?per_page={LIMIT}"
SHA = re.compile(r"^[a-f0-9]{40}$")
EXPECTED_LABELS = {"self-hosted", "zcloud", "vps"}
STANDARD_LABELS = {"linux", "x64", "arm64"}


class Unavailable(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise Unavailable("redirect_denied")


def strict_json(raw):
    if len(raw) > 1024 * 1024:
        raise Unavailable("oversized_reply")
    def unique(items):
        output = {}
        for name, value in items:
            if name in output:
                raise Unavailable("duplicate_key")
            output[name] = value
        return output
    def finite_float(value):
        converted = float(value)
        if not isfinite(converted):
            raise Unavailable("nonfinite")
        return converted
    try:
        result = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(Unavailable("nonfinite")),
                            parse_float=finite_float)
    except Unavailable:
        # Preserve the exact fail-closed reason raised by strict validation.
        # Unavailable subclasses ValueError and must not be misclassified.
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Unavailable("invalid_json") from exc
    if not isinstance(result, dict):
        raise Unavailable("invalid_payload")
    return result


def _utc(value):
    if not isinstance(value, str) or len(value) > 40:
        raise Unavailable("invalid_timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Unavailable("invalid_timestamp") from exc
    if dt.tzinfo is None:
        raise Unavailable("naive_timestamp")
    return dt.astimezone(timezone.utc)


def summarize(runs, jobsets, now, *, total_count=None):
    """Fail closed on incomplete evidence. This never calls a production endpoint."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise Unavailable("clock_unknown")
    if not isinstance(runs, list) or not 1 <= len(runs) <= LIMIT:
        raise Unavailable("run_sample_absent")
    if not isinstance(jobsets, dict):
        raise Unavailable("jobs_missing")
    if total_count is None:
        total_count = len(runs)
    if type(total_count) is not int or total_count < len(runs):
        raise Unavailable("invalid_total_count")
    seen = set()
    seen_job_ids = set()
    observed = 0
    stale_unassigned = 0
    recent_assigned = 0
    queued_assigned = 0
    unassigned_cancelled = 0
    approval_or_prestart = 0
    other = 0
    oldest_queued_seconds = 0
    stale_over_2h = 0
    assigned_failures = 0
    inspected = 0
    for run in runs:
        if not isinstance(run, dict):
            raise Unavailable("run_shape")
        number = run.get("id")
        if type(number) is not int or number <= 0 or number in seen:
            raise Unavailable("run_identity")
        seen.add(number)
        path = run.get("path")
        repository = run.get("repository")
        head_repository = run.get("head_repository")
        if (type(repository) is not dict or type(head_repository) is not dict
                or not isinstance(path, str) or path.split("@", 1)[0] != PATH
                or run.get("name") != NAME
                or run.get("event") not in ("pull_request", "workflow_dispatch")
                or repository.get("full_name") != REPO
                or head_repository.get("full_name") != REPO
                or not isinstance(run.get("head_sha"), str)
                or not SHA.fullmatch(run["head_sha"])):
            raise Unavailable("run_provenance")
        run_status = run.get("status")
        # GitHub can show a workflow awaiting environment approval or an
        # upstream start while its job still has runner_id=0. That is NOT
        # evidence of a missing VPS listener. Treat it separately.
        if run_status is not None and run_status not in (
                "queued", "in_progress", "completed", "waiting", "pending", "requested"):
            raise Unavailable("unknown_workflow_run_status")
        if run_status == "completed" and run.get("conclusion") not in (
                "success", "failure", "cancelled", "timed_out", "neutral",
                "skipped", "action_required", "stale"):
            raise Unavailable("unknown_workflow_run_conclusion")
        if run_status in ("queued", "in_progress", "waiting", "pending", "requested") and run.get("conclusion") is not None:
            raise Unavailable("nonterminal_workflow_run_conclusion")
        run_created = _utc(run.get("created_at"))
        run_age = (now - run_created).total_seconds()
        if run_age < -5:
            raise Unavailable("future_run")
        # A GitHub workflow_run's created_at is the ORIGINAL run time.
        # Rerunning that run schedules new jobs without changing its age.
        # Judge the live queue window by the bound JOB creation timestamp.
        jobs = jobsets.get(number)
        if not isinstance(jobs, list) or not len(jobs) == 1:
            raise Unavailable("incomplete_job_inventory")
        job = jobs[0]
        job_id = job.get("id") if isinstance(job, dict) else None
        run_attempt = run.get("run_attempt")
        if (type(job) is not dict or type(job_id) is not int
                or job_id in seen_job_ids
                or not 0 < job_id <= 9223372036854775807
                or type(run_attempt) is not int or not 0 < run_attempt <= 100
                or type(job.get("run_id")) is not int or job["run_id"] != number
                or type(job.get("run_attempt")) is not int
                or job.get("name") != "cpu-snapshot"
                or job.get("workflow_name") != NAME
                or job.get("head_sha") != run.get("head_sha")
                or job.get("head_branch") != run.get("head_branch")
                or job.get("run_attempt") != run_attempt
                or job.get("run_url") != f"https://api.github.com/repos/{REPO}/actions/runs/{number}"
                or job.get("url") != f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}"
                or job.get("html_url") != f"https://github.com/{REPO}/actions/runs/{number}/job/{job_id}"):
            raise Unavailable("unbound_job_provenance")
        seen_job_ids.add(job_id)
        labels = job.get("labels")
        if not isinstance(labels, list) or any(type(v) is not str for v in labels):
            raise Unavailable("unknown_labels")
        normalized = [v.lower() for v in labels]
        if len(normalized) != len(set(normalized)):
            raise Unavailable("duplicate_labels")
        if not set(normalized).issubset(EXPECTED_LABELS | STANDARD_LABELS) or not EXPECTED_LABELS.issubset(set(normalized)):
            raise Unavailable("unknown_runner_target")
        status = job.get("status")
        runner_id = job.get("runner_id")
        if runner_id is None:
            runner_id = 0
        if type(runner_id) is not int or runner_id < 0:
            raise Unavailable("invalid_runner_id")
        runner_name = job.get("runner_name")
        if runner_id == 0 and runner_name not in (None, ""):
            raise Unavailable("contradictory_runner_identity")
        if runner_id > 0 and (runner_id != 21 or runner_name != "zcloud-vps-1"):
            raise Unavailable("unattested_runner_assignment")
        if status == "completed" and job.get("conclusion") not in (
                "success", "failure", "cancelled", "timed_out", "neutral", "skipped"):
            raise Unavailable("ambiguous_conclusion")
        if status != "completed" and job.get("conclusion") is not None:
            raise Unavailable("nonterminal_conclusion")
        job_created = _utc(job.get("created_at"))
        # A workflow attempt may have new jobs created long after its original
        # workflow run (rerun). A job cannot predate its owning run. Allow a
        # small GitHub clock/timestamp precision tolerance, then fail closed.
        if (job_created - run_created).total_seconds() < -5:
            raise Unavailable("job_before_workflow_run")
        age = (now - job_created).total_seconds()
        if age < -5:
            raise Unavailable("future_job")
        if age > MAX_AGE:
            continue
        inspected += 1
        if run_status == "completed" and status != "completed":
            raise Unavailable("terminal_run_has_unfinished_job")
        if run_status in ("waiting", "pending", "requested"):
            if status != "queued" or runner_id != 0:
                raise Unavailable("non_schedulable_run_job_contradiction")
            approval_or_prestart += 1
            continue
        if status == "queued" and runner_id == 0 and job.get("conclusion") is None:
            observed += 1
            oldest_queued_seconds = max(oldest_queued_seconds, int(age))
            if age >= MIN_AGE:
                stale_unassigned += 1
            if age >= 7200:
                stale_over_2h += 1
        elif status == "queued" and runner_id > 0:
            # GitHub can attach a runner before a job leaves queued status.
            # This is an observed assignment, NOT proof of a live listener.
            observed += 1
            queued_assigned += 1
            if age < MIN_AGE:
                recent_assigned += 1
        elif status in ("in_progress", "completed") and runner_id > 0:
            observed += 1
            if age < MIN_AGE:
                recent_assigned += 1
            if status == "completed" and job["conclusion"] not in ("success", "skipped"):
                assigned_failures += 1
        elif (status == "completed" and runner_id == 0
              and job.get("conclusion") in ("skipped", "cancelled")):
            # A job cancelled before assignment never exercised a VPS runner.
            # Keep it out of both the stale queue and recovery signal.
            if job["conclusion"] == "cancelled":
                unassigned_cancelled += 1
            else:
                other += 1
        else:
            raise Unavailable("ambiguous_job_state")
    # Even repeated long queues cannot establish a dead listener without
    # independently authenticated service and runner-registration evidence.
    # A truncated run page can hide an assignment on another page. Do not
    # emit a listener-gap warning from an incomplete negative sample.
    sample_truncated = total_count > len(seen)
    if recent_assigned > 0:
        # Positive evidence of a real assignment remains meaningful even
        # when additional unsampled historical runs exist.
        classification = "RECENT_RUNNER_ASSIGNMENT_OBSERVED"
    elif sample_truncated:
        classification = "INCOMPLETE_QUEUE_SAMPLE"
    elif stale_unassigned >= 3:
        classification = "POSSIBLE_LISTENER_GAP"
    else:
        classification = "INSUFFICIENT_QUEUE_EVIDENCE"
    return {"schema": "zcloud-hosted-runner-queue-watch-v1",
            "classification": classification, "sampled_runs": len(seen),
            "inspected_recent_runs": inspected,
            "total_workflow_runs": total_count,
            "sample_truncated": sample_truncated,
            "queue_jobs_observed": observed, "stale_unassigned": stale_unassigned,
            "oldest_queued_minutes": oldest_queued_seconds // 60 if oldest_queued_seconds else None,
            "queued_over_2h": stale_over_2h,
            "recent_assignments": recent_assigned, "assigned_failures": assigned_failures,
            "queued_with_assigned_runner": queued_assigned,
            "cancelled_before_assignment": unassigned_cancelled,
            "approval_or_prestart": approval_or_prestart, "skipped": other,
            "runner_offline_verified": False, "production_verified": False,
            "mutation_authorized": False, "mutations_performed": False}


def _get(url, token, opener):
    request = Request(url, headers={"Authorization": f"Bearer {token}",
              "Accept": "application/vnd.github+json",
              "X-GitHub-Api-Version": "2022-11-28",
              "User-Agent": "zcloud-hosted-queue-watch"})
    try:
        with opener.open(request, timeout=12) as response:
            # A GitHub Actions bearer token must not accept evidence returned
            # from any alternate destination, even if a custom opener
            # accidentally permits a redirect.
            if response.geturl() != url:
                raise Unavailable("unexpected_final_url")
            if response.headers.get_content_type() != "application/json":
                raise Unavailable("not_json")
            return strict_json(response.read(1024 * 1024 + 1))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise Unavailable("github_unavailable") from exc


def collect(token, opener=None):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,2048}", token):
        raise Unavailable("missing_token")
    opener = opener or build_opener(NoRedirect())
    root = _get(URL, token, opener)
    runs = root.get("workflow_runs")
    if not isinstance(runs, list) or not 1 <= len(runs) <= LIMIT:
        raise Unavailable("invalid_run_inventory")
    total_count = root.get("total_count")
    if type(total_count) is not int or total_count < len(runs):
        raise Unavailable("invalid_run_count")
    data = {}
    for run in runs:
        rid = run.get("id") if isinstance(run, dict) else None
        if type(rid) is not int or rid < 1 or rid in data:
            raise Unavailable("duplicate_run")
        page = _get(f"https://api.github.com/repos/{REPO}/actions/runs/{rid}/jobs?per_page=100", token, opener)
        jobs = page.get("jobs")
        if type(page.get("total_count")) is not int or not isinstance(jobs, list) or len(jobs) != page["total_count"]:
            raise Unavailable("incomplete_job_page")
        data[rid] = jobs
    first = summarize(runs, data, datetime.now(timezone.utc), total_count=total_count)
    # A listener-gap warning is a NEGATIVE claim about runner assignment.
    # Repeat the entire bounded observation to reject jobs that were assigned
    # while the first pass was reading other runs. A positive assignment
    # observation does not authorize recovery and does not need this sweep.
    if first["classification"] != "POSSIBLE_LISTENER_GAP":
        return first
    second_root = _get(URL, token, opener)
    second_runs = second_root.get("workflow_runs")
    if (type(second_root.get("total_count")) is not int
            or second_root["total_count"] != total_count
            or not isinstance(second_runs, list)
            or second_runs != runs):
        raise Unavailable("queue_run_inventory_changed")
    second_data = {}
    for run in second_runs:
        rid = run["id"]
        page = _get(f"https://api.github.com/repos/{REPO}/actions/runs/{rid}/jobs?per_page=100",
                    token, opener)
        jobs = page.get("jobs")
        if (type(page.get("total_count")) is not int
                or not isinstance(jobs, list)
                or len(jobs) != page["total_count"]):
            raise Unavailable("queue_job_inventory_changed")
        second_data[rid] = jobs
    second = summarize(second_runs, second_data, datetime.now(timezone.utc),
                       total_count=total_count)
    if (second["classification"] != "POSSIBLE_LISTENER_GAP"
            or second_data != data):
        raise Unavailable("queue_state_changed")
    return second


def main():
    try:
        report = collect(os.getenv("GH_TOKEN", ""))
        status = 0
    except (Unavailable, ValueError, TypeError):
        report = {"schema": "zcloud-hosted-runner-queue-watch-v1",
                  "classification": "EVIDENCE_UNAVAILABLE",
                  "runner_offline_verified": False, "production_verified": False,
                  "mutation_authorized": False, "mutations_performed": False}
        status = 2
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())

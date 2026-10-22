#!/usr/bin/env python3
"""Read-only, bounded GitHub Actions runner-assignment triage for zSentinel.

A queued job with no assigned runner is NOT proof the VPS is offline.
The live GitHub source may change between sequential GETs. No runner,
workflow, claim or release action is performed by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import re

from zsentinel_pr_scope_guard import github_json, REPO, SHA

SCHEMA = "zsentinel-github-runner-queue-observation-v1"
STATUS_WINDOWS = ("queued", "in_progress")
MAX_RUNS_PER_WINDOW = 2
MAX_JOBS_PER_RUN = 16
JOBS_PAGE_SIZE = 50
LABEL = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")


def _age(value, now):
    """Optional age derived from GitHub's claimed UTC timestamp, not a heartbeat."""
    if type(value) is not str or len(value) > 48:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    age = (now - timestamp.astimezone(timezone.utc)).total_seconds()
    return int(age) if 0 <= age <= 30 * 86400 else None


def collect(repo, token=None):
    if type(repo) is not str or REPO.fullmatch(repo) is None:
        raise ValueError("invalid canonical runner triage repository")
    now = datetime.now(timezone.utc)
    selected = []
    seen_ids = set()
    windows = []
    for status in STATUS_WINDOWS:
        result = github_json(
            repo, "/actions/runs?status=" + status +
            "&per_page=" + str(MAX_RUNS_PER_WINDOW) + "&page=1", token)
        if (type(result) is not dict or type(result.get("total_count")) is not int
                or result["total_count"] < 0 or type(result.get("workflow_runs")) is not list
                or len(result["workflow_runs"]) > MAX_RUNS_PER_WINDOW):
            raise ValueError("invalid GitHub Actions run list")
        rows = result["workflow_runs"]
        windows.append({
            "status_requested": status,
            "rows_observed": len(rows),
            "all_status_runs_sampled": result["total_count"] == len(rows)
                and len(rows) < MAX_RUNS_PER_WINDOW,
        })
        for row in rows:
            if (type(row) is not dict or type(row.get("id")) is not int
                    or row["id"] <= 0 or row.get("status") not in
                    ("queued", "in_progress", "requested", "waiting", "pending")
                    or type(row.get("head_sha")) is not str
                    or SHA.fullmatch(row["head_sha"]) is None):
                raise ValueError("invalid GitHub Actions run identity")
            if row["id"] in seen_ids:
                raise ValueError("duplicate GitHub Actions run across windows")
            seen_ids.add(row["id"])
            selected.append(row)
    jobs = []
    run_receipts = []
    for run in selected:
        run_id = run["id"]
        data = github_json(
            repo, "/actions/runs/" + str(run_id) +
            "/jobs?per_page=" + str(JOBS_PAGE_SIZE) + "&page=1", token)
        if (type(data) is not dict or type(data.get("total_count")) is not int
                or data["total_count"] < 0 or type(data.get("jobs")) is not list
                or len(data["jobs"]) > JOBS_PAGE_SIZE):
            raise ValueError("invalid GitHub Actions jobs page")
        rows = data["jobs"]
        run_receipts.append({
            "run_url": "https://github.com/" + repo + "/actions/runs/" + str(run_id),
            "run_id": run_id,
            "head_sha_at_read": run["head_sha"],
            "workflow_age_seconds_observed": _age(run.get("created_at"), now),
            "jobs_sample_complete": (
                data["total_count"] == len(rows) and
                len(rows) < JOBS_PAGE_SIZE and len(rows) <= MAX_JOBS_PER_RUN),
        })
        seen_job_ids = set()
        for row in rows[:MAX_JOBS_PER_RUN]:
            if (type(row) is not dict or type(row.get("id")) is not int
                    or row["id"] <= 0 or row["id"] in seen_job_ids or
                    row.get("run_id") not in (None, run_id)):
                raise ValueError("invalid GitHub Actions job source identity")
            seen_job_ids.add(row["id"])
            if row.get("status") not in ("queued", "in_progress", "waiting", "pending"):
                continue
            rider, rname = row.get("runner_id"), row.get("runner_name")
            # Neither missing assignment nor missing labels proves a listener outage.
            if type(rider) is int and rider > 0 and type(rname) is str and 0 < len(rname) <= 100:
                assignment = "ASSIGNED_AT_READ"
            elif (rider is None or rider == 0) and (rname is None or rname == ""):
                assignment = "NO_ASSIGNMENT_OBSERVED"
            else:
                assignment = "UNKNOWN"
            labels = row.get("labels")
            valid_labels = type(labels) is list and all(
                type(label) is str and LABEL.fullmatch(label) for label in labels[:12])
            labels_sample = labels[:8] if valid_labels else []
            name = row.get("name")
            if type(name) is not str or not 1 <= len(name) <= 160:
                name = "GitHub job #" + str(row["id"])
            jobs.append({
                "run_url": "https://github.com/" + repo + "/actions/runs/" + str(run_id),
                "job_url": "https://github.com/" + repo + "/actions/runs/" +
                    str(run_id) + "/job/" + str(row["id"]),
                "run_id": run_id, "job_id": row["id"],
                "name": name, "status_at_read": row["status"],
                "assignment": assignment,
                "labels_observed": labels_sample,
                "labels_complete": valid_labels and len(labels) <= 8,
                "job_age_seconds_observed": _age(row.get("created_at"), now),
            })
    jobs.sort(key=lambda x: (
        x["assignment"] != "NO_ASSIGNMENT_OBSERVED",
        x["status_at_read"] != "queued",
        -(x["job_age_seconds_observed"] or 0), x["run_id"], x["job_id"]))
    return {
        "schema": SCHEMA, "repository": repo,
        "observed_at_utc": now.isoformat(),
        "source": "bounded_read_only_github_actions_runs_and_jobs",
        "run_windows": windows, "run_receipts": run_receipts,
        "sampled_jobs": jobs,
        "unassigned_queued_jobs_observed": sum(
            x["assignment"] == "NO_ASSIGNMENT_OBSERVED"
            and x["status_at_read"] == "queued" for x in jobs),
        "observed_pending_jobs": len(jobs),
        "all_repository_jobs_inspected": False,
        "source_nonatomic": True,
        "runner_health_verified": False,
        "repo_runner_permissions_verified": False,
        "claim_authorized": False, "merge_authorized": False,
        "restart_authorized": False, "writes_performed": False,
        "maximum_github_gets": len(STATUS_WINDOWS) +
            MAX_RUNS_PER_WINDOW * len(STATUS_WINDOWS),
    }


def unavailable(repo):
    """Explicit UNKNOWN snapshot, not a zero-job or healthy-runner report."""
    return {
        "schema": SCHEMA, "repository": repo,
        "source": "github_actions_source_unavailable",
        "source_status": "unavailable", "sampled_jobs": [],
        "run_windows": [], "run_receipts": [],
        "unassigned_queued_jobs_observed": None,
        "observed_pending_jobs": None,
        "all_repository_jobs_inspected": False,
        "source_nonatomic": True, "runner_health_verified": False,
        "repo_runner_permissions_verified": False,
        "claim_authorized": False, "merge_authorized": False,
        "restart_authorized": False, "writes_performed": False,
    }


def lines(receipt):
    if receipt.get("schema") != SCHEMA or receipt.get("writes_performed") is not False:
        raise ValueError("invalid runner assignment source")
    result = ["Runnerwachtrij · " + receipt["repository"] +
              " · geen service-/workflowingreep"]
    if receipt.get("source_status") == "unavailable":
        return result + ["GitHub Actions jobbron ONBEKEND; geen uitspraak over runnergezondheid."]
    result.append("Niet toegewezen queued jobs in begrensde bron: " +
                  str(receipt["unassigned_queued_jobs_observed"]) +
                  " (geen bewijs dat listener offline is).")
    for row in receipt["sampled_jobs"][:16]:
        age = row["job_age_seconds_observed"]
        result.append(
            row["assignment"] + " · " + row["status_at_read"] + " · " +
            row["name"] + " · leeftijd jobbron " +
            (str(age // 60) + "m" if age is not None else "ONBEKEND") +
            " · labels " + (",".join(row["labels_observed"]) or "ONBEKEND") +
            " · " + row["job_url"])
    if any(not w["all_status_runs_sampled"] for w in receipt["run_windows"]) or any(
            not r["jobs_sample_complete"] for r in receipt["run_receipts"]):
        result.append("BRON ONVOLLEDIG: meer runs of jobs buiten de limiet.")
    result.append("Eigenaaractie: verifieer repo-/groeprechten en runnerlabels in GitHub; "
                  "inspecteer listener pas via geautoriseerde zSSH-route, zonder actieve jobs te raken.")
    return result


def html(receipt):
    content = lines(receipt)
    return ("<section class='runner-queue'><h2>GitHub Actions runnerwachtrij</h2><ul>" +
            "".join("<li>" + escape(line) + "</li>" for line in content) +
            "</ul><p>Read-only, geen restart, retry, claim of mergevrijgave.</p></section>")

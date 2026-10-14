#!/usr/bin/env python3
"""GitHub-hosted, GET-only queue route triage for #1326 (NO repair authority).

Unlike the strict #1269 *admission* reference, triage deliberately counts
foreign-branch and zero-job queued runs as diagnostics rather than pretending
all queued workflows are actionable copies of current main. It never invokes
GitHub writes, deploys, cancellation, runner recovery or SSH.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import signal
import time

try:
    from scripts import zcloud_watchdog_queue_collector_20261014_w1 as source
    from scripts import zcloud_watchdog_queue_admission_20261014_w1 as policy
except ModuleNotFoundError:
    import zcloud_watchdog_queue_collector_20261014_w1 as source
    import zcloud_watchdog_queue_admission_20261014_w1 as policy

IDENTITY = "zcloud-actions-queue-route-triage-v1"
RUN_ROUTE = "/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100"
_LIMIT_RUNS = 40
_LIMIT_JOBS = 100
_MAX_SECONDS = 45
_MAX_GITHUB_ID = (1 << 63) - 1
# Only unambiguous, familiar GitHub-hosted image requests merit that label.
# Unknown/custom/missing labels are observational OTHER, never proof that
# GitHub-hosted capacity or the dedicated VPS listener is healthy.
_HOSTED_LABELS = {
    "ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04", "ubuntu-24.04-arm",
    "windows-latest", "windows-2022", "windows-2025",
    "macos-latest", "macos-14", "macos-15", "macos-15-intel",
}
# GitHub's built-in self-hosted OS/architecture labels do not name the
# project runner pool. Never silently ignore arbitrary extra labels when
# telling an operator that a queued job specifically targets zCloud/FTMO.
_RUNNER_META_LABELS = {"Linux", "Windows", "macOS", "X64", "ARM", "ARM64"}


def _deny(reason: str = "unverified_github_inventory") -> dict:
    # Even a detailed read-only route diagnosis NEVER admits side effects.
    return {
        "policy": IDENTITY, "decision": "DENY",
        "reason": reason, "observed_runs": 0, "route_counts": {},
        "runs": [], "mutation_authorized": False,
        "runner_recovery_authorized": False,
        "cancel_run_ids": [], "production_verified": False,
        "worker_generation_verified": False,
    }


def _route(jobs: list[dict]) -> str:
    if not jobs:
        return "no_job_records"
    candidates = []
    for job in jobs:
        labels = job["labels"]
        labels_set = set(labels)
        pool = labels_set - _RUNNER_META_LABELS
        if len(labels_set) != len(labels):
            candidates.append("other_or_mixed")
        elif len(labels) == 1 and labels[0] in _HOSTED_LABELS:
            candidates.append("github_hosted")
        elif pool == {"self-hosted", "zcloud", "vps"}:
            candidates.append("zcloud_vps")
        elif pool == {"self-hosted", "ftmo-research"}:
            candidates.append("ftmo_research")
        else:
            candidates.append("other_or_mixed")
    return candidates[0] if all(v == candidates[0] for v in candidates) else "mixed_job_routes"


def _assignment(jobs: list[dict]) -> str:
    if not jobs:
        return "no_job_records"
    values = []
    for job in jobs:
        runner_id = job.get("runner_id")
        name = job.get("runner_name")
        # GitHub may serialize an unassigned runner as null/null or 0/""
        # (also mixed null/empty). None never counts as a *known* runner.
        unassigned_id = runner_id is None or (type(runner_id) is int and runner_id == 0)
        unassigned_name = name is None or (type(name) is str and name == "")
        if unassigned_id and unassigned_name:
            values.append("not_assigned")
        elif (
            type(runner_id) is int and 0 < runner_id <= _MAX_GITHUB_ID
            and type(name) is str and bool(name.strip())
            and name.isprintable()
        ):
            values.append("assigned")
        else:
            values.append("indeterminate")
    return values[0] if all(x == values[0] for x in values) else "mixed_assignment"


def _validated_jobs(jobs: list[dict], run: dict) -> tuple:
    if len(jobs) > _LIMIT_JOBS:
        raise source.InventoryError("route_job_cap")
    signatures = []
    for job in jobs:
        signature = source._job_signature(job)
        if job["id"] > _MAX_GITHUB_ID:
            raise source.InventoryError("oversized_job_identity")
        if (
            job["run_id"] != run["id"]
            or job["run_attempt"] != run["run_attempt"]
            or job["head_sha"] != run["head_sha"]
            or job["head_branch"] != run["head_branch"]
        ):
            raise source.InventoryError("cross_run_job_evidence")
        labels = job["labels"]
        if type(job["status"]) is not str or job["status"] not in policy._ALLOWED_STATES:
            raise source.InventoryError("unknown_queue_job_state")
        if (
            len(labels) > 16 or any(len(label) > 64 for label in labels)
            # A key that is present with a JSON null value differs from
            # missing runner evidence. Only null and bounded strings are valid.
            or "runner_id" not in job or "runner_name" not in job
            or (type(job["runner_id"]) is int
                and job["runner_id"] > _MAX_GITHUB_ID)
            or not (job["runner_name"] is None
                    or (type(job["runner_name"]) is str
                        and len(job["runner_name"]) <= 128
                        and job["runner_name"].isprintable()))
        ):
            raise source.InventoryError("invalid_runner_evidence")
        signatures.append((signature, job["runner_name"]))
    return tuple(signatures)


def _jobs(api_get, run: dict) -> list[dict]:
    route = f"/repos/Zennay/zCloud/actions/runs/{run['id']}/jobs?per_page=100"
    jobs = source._pages(api_get, route, "jobs", max_pages=1,
                         max_count=_LIMIT_JOBS, query={"per_page": "100"})
    _validated_jobs(jobs, run)
    return jobs


def collect(api_get, *, clock=None, monotonic_clock=None) -> dict:
    """Audit at most 40 queued runs twice without treating any as cancelable."""
    monotonic = monotonic_clock or time.monotonic
    began = monotonic()
    timestamp = clock if clock is not None else datetime.now(timezone.utc)
    if (
        type(began) not in (int, float) or
        not isinstance(timestamp, datetime) or timestamp.tzinfo is None or
        timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0
    ):
        raise source.InventoryError("invalid_triage_clock")
    before = source._main(api_get)
    run_rows = source._pages(api_get, RUN_ROUTE, "workflow_runs",
                             max_pages=1, max_count=_LIMIT_RUNS,
                             query={"status": "queued", "per_page": "100"})
    # Validate the entire census before fetching individual jobs.
    identities = [source._run_identity(run) for run in run_rows]
    # Do not request a job endpoint for a forged, non-GitHub-size run ID.
    if any(run["id"] > _MAX_GITHUB_ID for run in run_rows):
        raise source.InventoryError("oversized_run_identity")
    if any(run["status"] != "queued" for run in run_rows):
        raise source.InventoryError("nonqueued_run_in_queued_snapshot")
    first_jobs = [_jobs(api_get, run) for run in run_rows]
    # GitHub job IDs are globally unique in the repository. Reusing one ID
    # under two different parent runs is conflicting API evidence, even when
    # each run's own pagination is internally consistent.
    global_job_ids = set()
    for group in first_jobs:
        for job in group:
            job_id = job["id"]
            if job_id in global_job_ids:
                raise source.InventoryError("job_identity_reused_across_runs")
            global_job_ids.add(job_id)
    latest = source._pages(api_get, RUN_ROUTE, "workflow_runs",
                           max_pages=1, max_count=_LIMIT_RUNS,
                           query={"status": "queued", "per_page": "100"})
    if [source._run_identity(run) for run in latest] != identities:
        raise source.InventoryError("queue_changed_during_inventory")
    for run, original in zip(run_rows, first_jobs):
        again = _jobs(api_get, run)
        if _validated_jobs(again, run) != _validated_jobs(original, run):
            raise source.InventoryError("job_state_changed_during_inventory")
    after = source._main(api_get)
    if after != before or not 0 <= monotonic() - began <= _MAX_SECONDS:
        raise source.InventoryError("main_moved_or_triage_stale")

    categories: dict[str, int] = {}
    observations = []
    for run, jobs in zip(run_rows, first_jobs):
        repo = run["head_repository"]["full_name"]
        if repo != source.REPO:
            scope = "foreign_repository"
        elif run["head_branch"] != "main":
            scope = "other_branch"
        elif run["head_sha"] != before:
            scope = "historical_main"
        else:
            scope = "current_main"
        route = _route(jobs)
        assignment = _assignment(jobs)
        # Do not collapse completed, actively running and approval-pending
        # jobs into one "mixed_or_active" bucket: that would make a queued
        # workflow look like its jobs are all waiting for a runner.
        states = {job["status"] for job in jobs}
        state = (
            "no_job_records" if not jobs else
            next(iter(states)) if len(states) == 1 else "mixed_job_states"
        )
        bucket = f"{scope}/{route}/{assignment}/{state}"
        categories[bucket] = categories.get(bucket, 0) + 1
        # Canonical, generated numeric hyperlink. Never echo GitHub job
        # names, labels, branches, raw responses or token contents.
        observations.append({
            "run_id": run["id"],
            "url": f"https://github.com/Zennay/zCloud/actions/runs/{run['id']}",
            "scope": scope, "route": route, "assignment": assignment,
            "job_state": state, "job_count": len(jobs),
        })
    return {
        "policy": IDENTITY, "decision": "OBSERVED",
        "observed_at": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "observed_runs": len(run_rows), "route_counts": dict(sorted(categories.items())),
        "runs": observations, "main_sha_before": before, "main_sha_after": after,
        "reason": "read_only_route_counts_not_runner_liveness",
        "mutation_authorized": False,
        "runner_recovery_authorized": False, "cancel_run_ids": [],
        "production_verified": False, "worker_generation_verified": False,
    }


def main() -> int:
    count = 0
    deadline = time.monotonic() + 100

    def on_alarm(_number, _frame):
        raise source.InventoryError("triage_wallclock_exceeded")

    enabled_alarm = hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM")
    if enabled_alarm:
        signal.signal(signal.SIGALRM, on_alarm)
        signal.setitimer(signal.ITIMER_REAL, 100)

    try:
        reader = source.ReadOnlyAPI(os.environ.get("GITHUB_TOKEN", ""))

        def bounded_get(path):
            nonlocal count
            count += 1
            if count > 90 or time.monotonic() >= deadline:
                raise source.InventoryError("triage_budget_exceeded")
            return reader.get(path)

        report = collect(bounded_get)
    except (source.InventoryError, OSError, ValueError, TypeError, RecursionError, AttributeError):
        report = _deny()
    finally:
        if enabled_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0 if report["decision"] == "OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

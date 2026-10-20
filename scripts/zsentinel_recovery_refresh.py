#!/usr/bin/env python3
"""zSentinel: bounded, no-redirect GitHub run/job refresh before routing.

Only GETs explicit existing run/job IDs from canonical configured repositories.
No CI dispatch, runner/queue mutation, credential logging or state promotion.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import bounded_get, canonical_projects, collect
from zsentinel_recovery_router import (
    MAX_INPUT_BYTES, MAX_STATE_BYTES, plan, read_json, write_json_atomic,
    operator_text, utc_string, load_state, acquire_private_ledger_lock,
)
from zsentinel_runner_route import runner_route

UTC = timezone.utc
MAX_TARGETS = 8
STATUS = {"queued", "in_progress", "completed", "waiting", "pending"}
CONCLUSION = {"success", "failure", "cancelled", "timed_out", "action_required"}
SHA = re.compile(r"^[0-9a-f]{40}$")


def positive_integer(value):
    return type(value) is int and 1 <= value <= 2**53 - 1


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing GitHub source date")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid GitHub source date") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("GitHub source date without timezone")
    return dt.astimezone(UTC)


def refresh(bundle, registry, token=None):
    """Refresh only explicitly pinned job receipts; never re-stamp Notion/runtime."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("evidence"), dict):
        raise ValueError("missing evidence")
    items = bundle["evidence"].get("projects")
    if not isinstance(items, list) or len(items) > 200:
        raise ValueError("invalid evidence projects")
    new_bundle = copy.deepcopy(bundle)
    output = []
    targets = []
    for entry in new_bundle["evidence"]["projects"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid source project")
        gh = entry.get("github", {})
        if not isinstance(gh, dict):
            raise ValueError("invalid github project evidence")
        if "run_id" not in gh and "job_id" not in gh:
            continue
        if (not positive_integer(gh.get("run_id"))
                or not positive_integer(gh.get("job_id"))):
            raise ValueError("pinned GitHub run_id and job_id required together")
        project = entry.get("id")
        if project not in registry:
            raise ValueError("pinned project is not in canonical repository registry")
        targets.append(entry)
    if len(targets) > MAX_TARGETS:
        raise ValueError("at most eight pinned jobs may be refreshed per invocation")
    for project in targets:
        pid = project["id"]
        repository = registry[pid]
        gh = project["github"]
        run_id, job_id = gh["run_id"], gh["job_id"]
        prefix = f"https://api.github.com/repos/{repository}"
        try:
            run = bounded_get(f"{prefix}/actions/runs/{run_id}", token)
            job = bounded_get(f"{prefix}/actions/jobs/{job_id}", token)
            if not isinstance(run, dict) or not isinstance(job, dict):
                raise ValueError("github_run_or_job_shape_invalid")
            if (run.get("id") != run_id or job.get("id") != job_id
                    or job.get("run_id") != run_id):
                raise ValueError("github_run_job_identity_mismatch")
            repo = run.get("repository")
            if not isinstance(repo, dict) or repo.get("full_name") != repository:
                raise ValueError("github_repository_identity_mismatch")
            sha = run.get("head_sha")
            if not isinstance(sha, str) or not SHA.fullmatch(sha):
                raise ValueError("github_head_sha_invalid")
            if gh.get("head_sha") and gh["head_sha"] != sha:
                raise ValueError("pinned_head_sha_mismatch")
            status = job.get("status")
            if status not in STATUS:
                raise ValueError("github_job_status_unknown")
            queued_at = timestamp(job.get("created_at"))
            observed = datetime.now(UTC)
            if queued_at > observed:
                raise ValueError("github_created_at_in_future")
            runner_id = job.get("runner_id", "missing")
            if runner_id == "missing" or (runner_id is not None
                    and (not isinstance(runner_id, int) or isinstance(runner_id, bool)
                         or runner_id < 0)):
                raise ValueError("github_runner_assignment_unavailable")
            runner_name = job.get("runner_name")
            if runner_name is not None and (
                    type(runner_name) is not str or len(runner_name) > 80
                    or any(ord(char) < 32 or ord(char) > 126
                           for char in runner_name)):
                raise ValueError("github_runner_name_untrusted")
            # Exact job metadata may prove a historic assignment to a named
            # runner. It never proves current health, registration or safe idle.
            canonical_runner_assigned = bool(
                runner_id and runner_name == "zcloud-vps-1"
                and runner_route(job)["runner_route"] == "self_hosted")
            conclusion = job.get("conclusion")
            if status == "completed" and conclusion not in CONCLUSION:
                raise ValueError("github_job_conclusion_unsupported")
            route = runner_route(job)
            updated = {
                "run_id": run_id, "job_id": job_id,
                "head_sha": sha, "job_status": status,
                "queued_at": utc_string(queued_at),
                "observed_at": utc_string(observed),
                "runner_assigned": bool(runner_id),
                "canonical_zcloud_runner_assignment_observed": canonical_runner_assigned,
                "verified_receipt": "github_exact_run_job",
                "runner_route": route["runner_route"],
                "route_labels": route["route_labels"],
                "job_assignment_stage": route["job_assignment_stage"],
                "assignment_source_complete": route["assignment_source_complete"],
                "runner_healthy_verified": False,
                "worker_resumed_verified": False,
            }
            if status == "completed":
                updated["conclusion"] = conclusion
                # GitHub can explicitly return steps=null when the platform
                # finishes a job before it executes any runner step. A missing
                # property is UNKNOWN: do not invent a failed admission.
                if "steps" in job:
                    steps = job["steps"]
                    if steps is None:
                        updated["no_job_steps"] = True
                    elif isinstance(steps, list):
                        updated["no_job_steps"] = len(steps) == 0
                    else:
                        raise ValueError("github_job_steps_evidence_invalid")
                if (conclusion in ("failure", "timed_out", "action_required")
                        and updated.get("no_job_steps") is True
                        and not runner_id):
                    # This is NOT a billing verdict. Platform annotations
                    # and account-owner permissions must be checked separately.
                    updated["verified_failure_category"] = "job_unstarted"
            # Drop an old unproven diagnosis instead of replaying 'billing' as fact.
            # Only an independently verified fresh source may set the category.
            project["github"] = updated
            output.append({"project_id": pid, "source": "github_exact_run_job",
                           "repository": repository,
                           "status": "refreshed", "run_id": run_id, "job_id": job_id,
                           "head_sha": sha, "observed_at": utc_string(observed),
                           "queued_at": utc_string(queued_at),
                           "job_status": status, "runner_assigned": bool(runner_id),
                           "canonical_zcloud_runner_assignment_observed": canonical_runner_assigned,
                           "runner_id_observed": runner_id if runner_id else None,
                           "runner_route": route["runner_route"],
                           "route_labels": route["route_labels"],
                           "job_assignment_stage": route["job_assignment_stage"],
                           "assignment_source_complete": route["assignment_source_complete"],
                           "no_job_steps": updated.get("no_job_steps"),
                           "job_start_evidence": (
                               "completed_without_steps" if status == "completed"
                               and updated.get("no_job_steps") is True
                               else "completed_with_steps" if status == "completed"
                               and updated.get("no_job_steps") is False
                               else "unknown_not_proven"),
                           "may_restart_runner": False})
        except ValueError as exc:
            # Unavailable/partial GitHub must not cause stale old receipts to
            # become fresh or create a false runner outage/CI failure.
            project["github"] = {}
            output.append({"project_id": pid, "source": "github_exact_run_job",
                           "status": "degraded", "reason": str(exc)[:100]})
    return new_bundle, {
        "requests_attempted_max": len(targets) * 2,
        "refreshed_projects": output,
        "github_coverage": "partial" if len(output) < len(items) or
                           any(row["status"] != "refreshed" for row in output)
                           else "selected_jobs_only",
        "notion_and_runtime_refresh_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only exact GitHub job refresh + zSentinel routing")
    parser.add_argument("bundle", help="trusted fresh zSentinel observation bundle")
    parser.add_argument("--registry", type=Path, required=True,
                        help="existing canonical projects registry JSON")
    parser.add_argument("--state", help="optional private local dedup ledger")
    parser.add_argument("--recheck-historical-jobs", action="store_true",
                        help="opt-in: use private unresolved exact run/job SHA to perform bounded GET-only reconciliation")
    parser.add_argument("--queue-database", type=Path,
                        help="read existing live SQLite queue pressure with mode=ro only")
    parser.add_argument("--read-product-issues", action="store_true",
                        help="read up to three selected project P0/P1 GitHub issue inboxes")
    parser.add_argument("--issue-project", action="append",
                        help="canonical issue-source project ID (up to 3, repeatable)")
    parser.add_argument("--format", choices=("json", "text", "html", "ics", "desk"), default="text")
    parser.add_argument("--desk-output", type=Path,
                        help="new, create-only private .html output (required only for --format desk)")
    args = parser.parse_args()
    ledger_lock = None
    try:
        if args.format == "desk" and args.desk_output is None:
            raise ValueError("--format desk requires --desk-output")
        if args.desk_output is not None and args.format not in ("desk", "html"):
            raise ValueError("--desk-output accepts only desk or html operator formats")
        if args.desk_output is not None:
            # Do not create a private HTML report on top of a pending
            # cursor or dedup ledger: a subsequent atomic JSON write could
            # otherwise clobber the report after its exclusive creation.
            private_inputs = [args.state]
            if any(item is not None and args.desk_output.resolve() == item.resolve()
                   for item in private_inputs):
                raise ValueError("desk-output must differ from private ledger/cursor paths")
        if args.state is not None:
            ledger_lock = acquire_private_ledger_lock(args.state)
        registry = canonical_projects(args.registry)
        bundle = read_json(args.bundle, MAX_INPUT_BYTES, "bundle")
        old = read_json(args.state, MAX_STATE_BYTES, "state") if args.state and Path(args.state).exists() else None
        selected_historical = None
        if args.recheck_historical_jobs:
            if old is None:
                raise ValueError("--recheck-historical-jobs requires an existing private --state ledger")
            from zsentinel_historical_backlog import pin_historical_jobs
            bundle, selected_historical = pin_historical_jobs(
                bundle, load_state(old), registry, limit=3)
        token = os.environ.get("GITHUB_TOKEN")
        enriched, source = refresh(bundle, registry, token)
        if selected_historical is not None:
            source["historical_recheck"] = selected_historical
        if args.issue_project and not args.read_product_issues:
            raise ValueError("--issue-project requires --read-product-issues")
        if args.read_product_issues:
            requested = (args.issue_project
                         if args.issue_project else [
                             row["id"] for row in bundle["evidence"]["projects"]
                             if isinstance(row, dict) and row.get("id") in registry
                         ][:3])
            if (not requested or len(requested) > 3 or len(set(requested)) != len(requested)
                    or any(name not in registry for name in requested)):
                raise ValueError("issue source requires one to three distinct canonical projects")
            # Existing collector spends at most three public GETs per project
            # (oldest, newest, linked PR window). It never claims ownership.
            try:
                enriched["issue_inbox"] = collect(registry, requested, token, max_items=40)
                source["issue_source"] = "github_readonly_selected_projects"
                source["issue_projects"] = requested
                source["issue_requests_attempted"] = enriched["issue_inbox"]["request_count"]
            except (ValueError, OSError) as exc:
                enriched.pop("issue_inbox", None)
                source["issue_source"] = "degraded_not_fetched"
                source["issue_source_reason"] = str(exc)[:80]
        if args.queue_database is not None:
            from zsentinel_queue_observer import observe
            try:
                enriched["queue_pressure"] = observe(args.queue_database)
                source["queue_source"] = "local_sqlite_transaction_query_only"
            except (OSError, ValueError, sqlite3.Error):
                enriched.pop("queue_pressure", None)
                source["queue_source"] = "degraded_unavailable_no_mutation"
        routed, updated = plan(enriched, old)
        routed["live_github_read"] = source
        routed["private_ledger_written"] = bool(args.state)
        if args.format == "html":
            from zsentinel_recovery_view import html_view
            rendered = html_view(routed)
        elif args.format == "ics":
            from zsentinel_human_agenda import human_calendar
            rendered = human_calendar(routed)
        elif args.format == "desk":
            from zsentinel_incident_desk import review_html
            rendered = review_html(routed)
        else:
            rendered = (json.dumps(routed, sort_keys=True, indent=2)
                        if args.format == "json" else
                        operator_text(routed) + "\\nGitHub coverage: " + source["github_coverage"])
        if args.desk_output is not None:
            from zsentinel_incident_desk import write_private_html, review_html
            private_desk = rendered if args.format == "desk" else review_html(routed)
            write_private_html(args.desk_output, private_desk)
        # A broken HTML/source cannot silently acknowledge incident history.
        if args.state:
            write_json_atomic(args.state, updated)
        if args.format != "desk":
            print(rendered)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"zSentinel read-only refresh refused: {exc}\n")
    finally:
        if ledger_lock is not None:
            ledger_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

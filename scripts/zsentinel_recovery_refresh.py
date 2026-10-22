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

from zsentinel_product_issue_inbox import (
    GithubAdmissionError, bounded_get, canonical_projects, collect,
)
from zsentinel_recovery_router import (
    MAX_INPUT_BYTES, MAX_STATE_BYTES, plan, read_json, write_json_atomic,
    operator_text, utc_string, load_state, acquire_private_ledger_lock,
)
from zsentinel_runner_route import runner_route

UTC = timezone.utc
MAX_TARGETS = 8
STATUS = {"queued", "in_progress", "completed", "waiting", "pending"}
CONCLUSION = {"success", "failure", "cancelled", "timed_out", "action_required", "neutral", "skipped", "stale", "startup_failure"}
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
    requests_attempted = 0
    stop_after_provider_denial = None
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
        if stop_after_provider_denial is not None:
            # A 401 or proven rate-budget exhaustion is shared across this
            # token, not a new incident for every remaining pinned job.
            # Invalidate their historical job receipts rather than reusing
            # a stale green/failure signal as a newly refreshed source.
            project["github"] = {}
            output.append({"project_id": pid, "source": "github_exact_run_job",
                           "status": "degraded",
                           "reason": "github_read_skipped_after_admission_denial",
                           "admission_category": stop_after_provider_denial})
            continue
        prefix = f"https://api.github.com/repos/{repository}"
        try:
            requests_attempted += 1
            run = bounded_get(f"{prefix}/actions/runs/{run_id}", token)
            requests_attempted += 1
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
            # The run id alone does not license a mismatched job receipt.
            # When GitHub exposes its job SHA/attempt, both must agree with
            # the requested immutable run before the operator sees a link.
            if "head_sha" in job and job["head_sha"] != sha:
                raise ValueError("github_job_head_sha_mismatch")
            run_attempt = run.get("run_attempt")
            job_attempt = job.get("run_attempt")
            if (run_attempt is not None and
                    (type(run_attempt) is not int or
                     not 1 <= run_attempt <= 10**6)):
                raise ValueError("invalid_workflow_run_attempt")
            if (job_attempt is not None and
                    (type(job_attempt) is not int or
                     not 1 <= job_attempt <= 10**6)):
                raise ValueError("invalid_workflow_job_attempt")
            if (run_attempt is not None and job_attempt is not None
                    and job_attempt != run_attempt):
                raise ValueError("github_run_job_attempt_mismatch")
            attempt_verified = run_attempt is not None and job_attempt is not None
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
            if status != "completed" and conclusion is not None:
                # Two provider endpoints are read sequentially. If the job
                # changed lifecycle mid-read, suppress contradictory data:
                # never report a final failure as a waiting/runner incident.
                raise ValueError("github_job_status_conclusion_inconsistent")
            # Complete job timestamps are useful provenance, but never an
            # authorization to requeue or restart a self-hosted runner.
            started_at = job.get("started_at")
            completed_at = job.get("completed_at")
            started = timestamp(started_at) if started_at is not None else None
            completed = timestamp(completed_at) if completed_at is not None else None
            if (started is not None and started < queued_at
                    or completed is not None and
                    (status != "completed" or completed < queued_at
                     or (started is not None and completed < started))
                    or started is not None and started > observed
                    or completed is not None and completed > observed):
                raise ValueError("github_job_timeline_inconsistent")
            route = runner_route(job)
            updated = {
                "run_id": run_id, "job_id": job_id,
                "head_sha": sha, "job_status": status,
                "verified_matching_run_attempt": attempt_verified,
                "run_attempt": run_attempt if attempt_verified else None,
                "queued_at": utc_string(queued_at),
                "started_at": utc_string(started) if started is not None else None,
                "completed_at": utc_string(completed) if completed is not None else None,
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
                           "head_sha": sha,
                           "verified_matching_run_attempt": attempt_verified,
                           "run_attempt": run_attempt if attempt_verified else None,
                           "observed_at": utc_string(observed),
                           "queued_at": utc_string(queued_at),
                           "started_at": utc_string(started) if started is not None else None,
                           "completed_at": utc_string(completed) if completed is not None else None,
                           "job_status": status, "runner_assigned": bool(runner_id),
                           "canonical_zcloud_runner_assignment_observed": canonical_runner_assigned,
                           "runner_id_observed": runner_id if runner_id else None,
                           "runner_route": route["runner_route"],
                           "route_labels": route["route_labels"],
                           "job_assignment_stage": route["job_assignment_stage"],
                           "assignment_source_complete": route["assignment_source_complete"],
                           "no_job_steps": updated.get("no_job_steps"),
                           "conclusion": updated.get("conclusion") if status == "completed" else None,
                           "job_start_evidence": (
                               "completed_without_steps" if status == "completed"
                               and updated.get("no_job_steps") is True
                               else "completed_with_steps" if status == "completed"
                               and updated.get("no_job_steps") is False
                               else "unknown_not_proven"),
                           "may_restart_runner": False})
        except ValueError as exc:
            # Unauthorized GitHub reads and exhausted provider rate budgets
            # cannot become per-project stale evidence or an N-times retry
            # loop. 403 without an explicit rate-limit header stays scoped:
            # it could be repository permissions rather than global quota.
            denial = exc.category if isinstance(exc, GithubAdmissionError) else None
            if denial in ("authentication_required", "rate_limit_confirmed"):
                stop_after_provider_denial = denial
            # Unavailable/partial GitHub must not cause stale old receipts to
            # become fresh or create a false runner outage/CI failure.
            project["github"] = {}
            row = {"project_id": pid, "source": "github_exact_run_job",
                   "status": "degraded", "reason": str(exc)[:100]}
            if denial is not None:
                row["admission_category"] = denial
            output.append(row)
    return new_bundle, {
        "requests_attempted_max": len(targets) * 2,
        "requests_attempted_actual": requests_attempted,
        "provider_admission_denial": stop_after_provider_denial,
        "skipped_due_to_provider_denial": sum(
            row.get("reason") == "github_read_skipped_after_admission_denial"
            for row in output),
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
    parser.add_argument("--worker-events-database", type=Path,
                        help="opt-in existing indexed history.db: read-only worker events and queue-lease observations")
    parser.add_argument("--worker-project", action="append", default=[],
                        help="canonical project ID for worker observation; 1-3 distinct projects, repeatable")
    parser.add_argument("--read-notion-claims", action="store_true",
                        help="read bounded live Notion Claimed/Running/Verifying status windows; no claim authority")
    parser.add_argument("--notion-data-source",
                        help="Notion queue data-source UUID, or NOTION_QUEUE_DATA_SOURCE_ID environment variable")
    parser.add_argument("--notion-claims-export", type=Path,
                        help="private saved Notion connector snapshot; origin/current ownership remain unverified")
    parser.add_argument("--read-product-issues", action="store_true",
                        help="read up to three selected project P0/P1 GitHub issue inboxes")
    parser.add_argument("--issue-project", action="append",
                        help="canonical issue-source project ID (up to 3, repeatable)")
    parser.add_argument("--read-existing-prs", action="store_true",
                        help="inspect exact-head CI/review of up to three existing product PRs")
    parser.add_argument("--pr-project", action="append", default=[],
                        help="discover one bounded current open-PR window for a canonical project")
    parser.add_argument("--existing-pr", action="append", default=[],
                        help="prioritize explicit existing PR as PROJECT:NUMBER, up to three")
    parser.add_argument("--format", choices=("json", "text", "html", "ics", "desk"), default="text")
    parser.add_argument("--output", type=Path,
                        help="optional create-only owner-private 0600 report; avoids printing private source details")
    parser.add_argument("--desk-output", type=Path,
                        help="new, create-only private .html output (required only for --format desk)")
    parser.add_argument("--action-output", type=Path,
                        help="create-only 0600 private .md merge-first owner briefing from same read-only decision")
    parser.add_argument("--owner-handoff-output", type=Path,
                        help="private create-only .md/.json/.csv merged recovery + exact-PR owner handoff")
    parser.add_argument("--previous-pr-report", type=Path,
                        help="private prior live/refresh JSON, show exact PR gate changes and HOLD continuity")
    args = parser.parse_args()
    ledger_lock = None
    try:
        # The refresh CLI shares the exact same pre-observation path safety
        # contract as live/portfolio: all outputs and mutable state are unique,
        # and none may alias the original evidence or saved PR source.
        from zsentinel_private_path_admission import admit_private_paths
        admit_private_paths(
            destinations={
                "output": args.output,
                "desk_output": args.desk_output,
                "action_output": args.action_output,
                "owner_handoff_output": args.owner_handoff_output,
                "state": Path(args.state) if args.state is not None else None,
            },
            sources={
                "bundle": Path(args.bundle),
                "registry": args.registry,
                "queue_database": args.queue_database,
                "worker_events_database": args.worker_events_database,
                "notion_claims_export": args.notion_claims_export,
                "previous_pr_report": args.previous_pr_report,
            },
        )
        if args.format == "desk" and args.desk_output is None:
            raise ValueError("--format desk requires --desk-output")
        if args.output is not None:
            extensions = {"json": ".json", "text": ".txt", "html": ".html", "ics": ".ics"}
            if args.format not in extensions or args.output.suffix != extensions[args.format]:
                raise ValueError("--output extension must match json, text, html or ics format")
            if args.desk_output is not None:
                raise ValueError("choose --output or --desk-output, not both")
            protected = (
                Path(args.bundle), args.registry, args.state, args.queue_database,
                args.worker_events_database, args.notion_claims_export, args.desk_output,
                args.action_output, args.owner_handoff_output,
            )
            if any(item is not None and args.output.resolve() == Path(item).resolve()
                   for item in protected):
                raise ValueError("--output overlaps a source, ledger or other private report")
        if (args.pr_project or args.existing_pr) and not args.read_existing_prs:
            raise ValueError("--pr-project/--existing-pr requires --read-existing-prs")
        if args.previous_pr_report is not None:
            if not args.read_existing_prs or args.previous_pr_report.suffix != ".json":
                raise ValueError("--previous-pr-report requires --read-existing-prs and private JSON")
            protected = (
                Path(args.bundle), args.registry, args.state,
                args.queue_database, args.worker_events_database,
                args.notion_claims_export, args.output, args.desk_output,
                args.action_output, args.owner_handoff_output,
            )
            if any(p is not None and
                   args.previous_pr_report.resolve() == Path(p).resolve()
                   for p in protected):
                raise ValueError("previous PR report aliases input, ledger or output")
        if args.read_existing_prs and args.format not in ("json", "text", "html", "desk"):
            raise ValueError("PR source supports JSON, text, HTML or private desk output")
        if args.worker_project and args.worker_events_database is None:
            raise ValueError("--worker-project requires --worker-events-database")
        if args.worker_events_database is not None:
            if args.format not in ("json", "text", "html", "desk"):
                raise ValueError("--worker-events-database supports json, text, html or private desk output")
        if args.read_notion_claims and args.notion_claims_export is not None:
            raise ValueError("choose either live Notion token or saved connector snapshot")
        if args.notion_data_source and not (
                args.read_notion_claims or args.notion_claims_export is not None):
            raise ValueError("--notion-data-source requires a Notion observation mode")
        if args.read_notion_claims or args.notion_claims_export is not None:
            if args.format not in ("json", "text", "html", "desk"):
                raise ValueError("Notion status observation supports json, text, html or private desk output")
        if ((args.worker_events_database is not None or args.read_notion_claims
             or args.notion_claims_export is not None or args.read_existing_prs)
             and args.output is None
             and (args.format != "desk" or args.desk_output is None)):
            # --desk-output is a private sink only when the selected output
            # format itself is desk. HTML/text still prints to stdout unless
            # --output is supplied; refuse that possible private-data leak.
            raise ValueError("private Notion/worker observations require --output or --format desk with --desk-output")
        if args.desk_output is not None and args.format not in ("desk", "html"):
            raise ValueError("--desk-output accepts only desk or html operator formats")
        if args.desk_output is not None:
            # Never let a report path alias a source, registry or private
            # ledger; output creation must not destroy the evidence it summarizes.
            private_inputs = [
                Path(args.bundle), args.registry, args.state, args.queue_database,
                args.worker_events_database, args.notion_claims_export, args.output,
                args.action_output, args.owner_handoff_output,
            ]
            if any(item is not None and args.desk_output.resolve() == Path(item).resolve()
                   for item in private_inputs):
                raise ValueError("desk-output must differ from every source, ledger and report path")
        if args.action_output is not None:
            if args.action_output.suffix != ".md":
                raise ValueError("--action-output requires a new private .md path")
            inputs = (Path(args.bundle), args.registry, args.state, args.queue_database,
                      args.worker_events_database, args.notion_claims_export,
                      args.output, args.desk_output, args.owner_handoff_output)
            if any(p is not None and args.action_output.resolve() == Path(p).resolve()
                   for p in inputs):
                raise ValueError("action-output cannot alias a source, ledger or report")
        if args.owner_handoff_output is not None:
            if args.owner_handoff_output.suffix not in (".md", ".json", ".csv"):
                raise ValueError("--owner-handoff-output must be .md, .json or .csv")
            protected = (
                Path(args.bundle), args.registry, args.state,
                args.queue_database, args.worker_events_database,
                args.notion_claims_export, args.output, args.desk_output,
                args.action_output,
            )
            if any(p is not None and
                   args.owner_handoff_output.resolve() == Path(p).resolve()
                   for p in protected):
                raise ValueError("owner handoff cannot alias a source, ledger or report")
        if args.state is not None:
            ledger_lock = acquire_private_ledger_lock(args.state)
        registry = canonical_projects(args.registry)
        pr_selections = []
        pr_discovery_projects = []
        if args.read_existing_prs:
            for value in args.existing_pr:
                match = re.fullmatch(r"([a-z][a-z0-9_-]{0,39}):([1-9][0-9]{0,8})", value)
                if match is None or match.group(1) not in registry:
                    raise ValueError("existing PR requires canonical PROJECT:NUMBER")
                pr_selections.append({"project_id": match.group(1),
                                      "pr_number": int(match.group(2))})
            if (len(pr_selections) > 3
                    or len({(row["project_id"], row["pr_number"])
                            for row in pr_selections}) != len(pr_selections)):
                raise ValueError("at most three distinct existing PRs")
            if args.pr_project:
                pr_discovery_projects = list(args.pr_project)
            if (len(pr_discovery_projects) > 3
                    or len(set(pr_discovery_projects)) != len(pr_discovery_projects)
                    or any(pid not in registry for pid in pr_discovery_projects)):
                raise ValueError("existing PR source requires up to three distinct canonical projects")
        if args.worker_events_database is not None and (
                not 1 <= len(args.worker_project) <= 3
                or len(set(args.worker_project)) != len(args.worker_project)
                or any(name not in registry for name in args.worker_project)):
            raise ValueError("worker source requires one to three distinct canonical --worker-project IDs")
        bundle = read_json(args.bundle, MAX_INPUT_BYTES, "bundle")
        if args.read_existing_prs:
            if not pr_selections and not pr_discovery_projects:
                # One validated read for discovery and routing: never
                # attribute PR evidence to a different bundle inode/epoch.
                projects = bundle.get("evidence", {}).get("projects")
                if type(projects) is not list:
                    raise ValueError("existing PR source requires bounded evidence projects")
                pr_discovery_projects = [
                    row["id"] for row in projects
                    if type(row) is dict and row.get("id") in registry
                ][:3]
            if not (pr_selections or pr_discovery_projects):
                raise ValueError("existing PR source requires canonical projects")
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
        provider_denial = source.get("provider_admission_denial")
        if provider_denial not in (
                None, "authentication_required", "rate_limit_confirmed"):
            raise ValueError("unknown upstream GitHub admission source")
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
                if provider_denial is not None:
                    # One confirmed token-wide refusal halts sibling issue
                    # reads. Missing issues remain UNKNOWN, never zero.
                    source["issue_source"] = "degraded_skipped_after_provider_denial"
                    source["issue_source_reason"] = provider_denial
                    source["issue_projects"] = requested
                    source["issue_requests_attempted"] = 0
                else:
                    enriched["issue_inbox"] = collect(registry, requested, token, max_items=40)
                    source["issue_source"] = "github_readonly_selected_projects"
                    source["issue_projects"] = requested
                    source["issue_requests_attempted"] = enriched["issue_inbox"]["request_count"]
                    if enriched["issue_inbox"].get("stopped_after_rate_or_auth_denial") is True:
                        # Issue collector's global stop is reserved for a
                        # confirmed provider quota. Do not send another PR GET
                        # on the same token after this source receipt.
                        provider_denial = "rate_limit_confirmed"
                        source["provider_admission_denial"] = provider_denial
                        source["provider_denial_discovered_in"] = "github_issue_inbox"
            except (ValueError, OSError) as exc:
                enriched.pop("issue_inbox", None)
                source["issue_source"] = "degraded_not_fetched"
                source["issue_source_reason"] = str(exc)[:80]
        pr_delivery = None
        if args.read_existing_prs:
            from zsentinel_pr_delivery import discover_open_prs, observe_prs
            discovered = None
            if provider_denial is not None and pr_discovery_projects:
                discovered = {
                    "coverage": [{
                        "project_id": project,
                        "status": "not_queried_after_auth_or_rate_limit",
                        "provider_admission": provider_denial,
                    } for project in pr_discovery_projects],
                    "calls": 0, "selection": [],
                    "all_open_prs_observed": False,
                    "merge_authorized": False, "writes_performed": False,
                    "owner_verified": False,
                }
                source["pr_discovery"] = "degraded_skipped_after_provider_denial"
            elif pr_discovery_projects and len(pr_selections) < 3:
                try:
                    discovered = discover_open_prs(
                        registry, pr_discovery_projects, token=token,
                        limit=3 - len(pr_selections), exclude=pr_selections)
                    pr_selections.extend(discovered["selection"])
                    if any(
                        type(entry) is dict
                        and entry.get("provider_admission") == "rate_limit_confirmed"
                        for entry in discovered["coverage"]
                    ):
                        provider_denial = "rate_limit_confirmed"
                        source["provider_admission_denial"] = provider_denial
                        source["provider_denial_discovered_in"] = "github_pr_discovery"
                except (OSError, ValueError, TypeError, KeyError):
                    # Preserve explicit pins; missing discovery NEVER
                    # advertises a complete portfolio or an empty backlog.
                    source["pr_discovery"] = "degraded_unavailable_no_owner_proof"
                    discovered = {
                        "coverage": [{"project_id": project, "status": "degraded"}
                                     for project in pr_discovery_projects],
                        "calls": 0, "selection": [],
                        "all_open_prs_observed": False,
                        "merge_authorized": False,
                        "writes_performed": False, "owner_verified": False,
                    }
            pr_delivery = observe_prs(
                registry, pr_selections, token=token,
                skip_provider_denial=provider_denial)
            pr_delivery["open_pr_discovery"] = discovered if discovered is not None else {
                "coverage": "not_requested",
                "all_open_prs_observed": False, "merge_authorized": False,
            }
            source["pr_source"] = ("degraded_skipped_after_provider_denial"
                                   if provider_denial is not None else
                                   "github_bounded_existing_prs_readonly")
            source["pr_requests_attempted"] = pr_delivery["request_count"] + (
                discovered["calls"] if discovered is not None else 0)
            source["pr_selected"] = len(pr_selections)
            source["pr_coverage"] = "sample_only_never_owner_or_merge_authority"
        if args.queue_database is not None:
            from zsentinel_queue_observer import observe
            try:
                enriched["queue_pressure"] = observe(args.queue_database)
                source["queue_source"] = "local_sqlite_transaction_query_only"
            except (OSError, ValueError, sqlite3.Error):
                enriched.pop("queue_pressure", None)
                source["queue_source"] = "degraded_unavailable_no_mutation"
        if args.worker_events_database is not None:
            try:
                from zsentinel_worker_progress_observer import observe_worker_progress
                enriched["worker_progress_observation"] = observe_worker_progress(
                    args.worker_events_database, args.worker_project)
                source["worker_progress"] = "read_only_indexed_events"
                source["worker_projects"] = list(args.worker_project)
            except (OSError, ValueError, sqlite3.Error):
                enriched.pop("worker_progress_observation", None)
                source["worker_progress"] = "degraded_unavailable_no_recovery_proof"
                source["worker_projects"] = list(args.worker_project)
                source["worker_progress_reason"] = "read_only_source_unavailable_or_schema_incompatible"
        notion_status = "not_observed_no_ownership_claim"
        if args.read_notion_claims or args.notion_claims_export is not None:
            data_source_id = (args.notion_data_source
                              or os.environ.get("NOTION_QUEUE_DATA_SOURCE_ID"))
            try:
                from zsentinel_notion_claim_observer import project_labels, observe_claims
                known_projects = project_labels(args.registry, registry)
                if args.read_notion_claims:
                    enriched["notion_claims_observation"] = observe_claims(
                        data_source_id, os.environ.get("NOTION_TOKEN"), known_projects)
                    notion_status = "claim_status_observed_no_file_ownership"
                else:
                    from zsentinel_notion_connector_snapshot import observe_saved_connector
                    enriched["notion_claims_observation"] = observe_saved_connector(
                        args.notion_claims_export, data_source_id, known_projects)
                    notion_status = "private_connector_snapshot_unverified_origin"
            except (OSError, ValueError, TypeError, KeyError):
                enriched.pop("notion_claims_observation", None)
                notion_status = "degraded_unavailable_no_claim_authority"
                source["notion_claims_reason"] = "notion_source_unavailable_or_incomplete"
        source["notion_claims"] = notion_status
        pr_continuity = None
        if args.previous_pr_report is not None:
            from zsentinel_portfolio_pr_delta import load_previous_portfolio
            from zsentinel_pr_hold_continuity import compare
            previous_report, prior_digest = load_previous_portfolio(
                args.previous_pr_report)
            previous_pr = previous_report.get("pr_delivery")
            if type(previous_pr) is not dict:
                raise ValueError("previous private JSON lacks PR delivery evidence")
            pr_continuity = compare(pr_delivery, previous_pr, prior_digest)
            source["pr_gate_comparison"] = "private_saved_exact_pr_source_no_gets"
        routed, updated = plan(enriched, old)
        if pr_delivery is not None:
            routed["pr_delivery"] = pr_delivery
        if pr_continuity is not None:
            routed["pr_gate_continuity"] = pr_continuity
        routed["live_github_read"] = source
        routed["live_sources"] = {
            "notion": notion_status,
            "runtime": "not_observed_no_worker_recovery_claim",
            "current_claim_inventory": "unknown_and_not_used_for_routing",
            "worker_progress": source.get("worker_progress", "not_requested"),
        }
        if "worker_progress_observation" in enriched:
            routed["worker_progress_observation"] = enriched["worker_progress_observation"]
        if "notion_claims_observation" in enriched:
            routed["notion_claims_observation"] = enriched["notion_claims_observation"]
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
            if args.format == "text" and pr_delivery is not None:
                from zsentinel_pr_delivery import handoff_markdown
                rendered += "\n\n" + handoff_markdown(pr_delivery)
            if args.format == "text" and pr_continuity is not None:
                from zsentinel_pr_hold_continuity import text_summary
                rendered += "\n\n" + text_summary(pr_continuity)
        handoff_content = None
        if args.owner_handoff_output is not None:
            from zsentinel_runtime_handoff import build_handoff, serialize
            # Reuse a single canonical router and exact-HEAD PR read. The
            # owner packet is non-executable; no second source observation.
            handoff_content = serialize(
                build_handoff(routed, registry=registry, pr_desk=pr_delivery),
                args.owner_handoff_output.suffix)
        action_content = None
        if args.action_output is not None:
            from zsentinel_incident_desk import private_action_output
            action_content = private_action_output(routed)
        if args.desk_output is not None:
            from zsentinel_incident_desk import write_private_html, review_html
            private_desk = rendered if args.format == "desk" else review_html(routed)
            write_private_html(args.desk_output, private_desk)
        if args.output is not None:
            from zsentinel_private_output import write_private_output
            output_format = "txt" if args.format == "text" else args.format
            write_private_output(args.output, rendered, output_format)
        if action_content is not None:
            from zsentinel_private_output import write_private_output
            write_private_output(args.action_output, action_content, "md")
        if handoff_content is not None:
            from zsentinel_private_output import write_private_output
            write_private_output(args.owner_handoff_output, handoff_content,
                                 args.owner_handoff_output.suffix.removeprefix("."))
        # A broken HTML/source cannot silently acknowledge incident history.
        if args.state:
            write_json_atomic(args.state, updated)
        if args.format != "desk":
            if args.output is not None:
                print("Privé zSentinel-rapport veilig aangemaakt: " + args.output.name +
                      " (0600, create-only; niets overschreven).")
            else:
                print(rendered)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"zSentinel read-only refresh refused: {exc}\n")
    finally:
        if ledger_lock is not None:
            ledger_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

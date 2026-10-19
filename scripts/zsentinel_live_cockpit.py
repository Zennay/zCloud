#!/usr/bin/env python3
"""zSentinel one-command live cockpit: observed queue + bounded public GitHub.

Runs once and exits; does not require a hand-authored evidence bundle. Explicitly
marks Notion/worker claims unavailable rather than inventing ownership. NEVER
mutates production SQLite, a worker, a claim, a runner, or a GitHub action.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import canonical_projects, collect
from zsentinel_queue_observer import observe
from zsentinel_recovery_refresh import refresh
from zsentinel_recovery_router import (
    MAX_STATE_BYTES, operator_text, plan, read_json, write_json_atomic, utc_string,
)

ROOT = Path(__file__).resolve().parent.parent
MAX_PROJECTS = 3
MAX_JOBS = 3
HEX_SHA = re.compile(r"^[a-f0-9]{40}$")
UTC = timezone.utc


def parse_jobs(raw, names):
    """Only canonical explicit (project, run, job, SHA) receipt pins."""
    if len(raw) > MAX_JOBS:
        raise ValueError("at most three explicitly pinned job receipts")
    found = {}
    for text in raw:
        if not isinstance(text, str) or len(text) > 180:
            raise ValueError("invalid pinned job receipt length")
        fields = text.split(":")
        if len(fields) != 4:
            raise ValueError("use --job PROJECT:RUN_ID:JOB_ID:COMMIT_SHA")
        pid, run, job, sha = fields
        if (pid not in names or pid in found or not run.isascii()
                or not job.isascii() or not run.isdecimal()
                or not job.isdecimal() or not HEX_SHA.fullmatch(sha)):
            raise ValueError("invalid or duplicate canonical pinned job identity")
        run_id, job_id = int(run), int(job)
        if not (1 <= run_id <= 2**53-1 and 1 <= job_id <= 2**53-1):
            raise ValueError("pinned GitHub IDs out of safe integer range")
        found[pid] = {"run_id":run_id, "job_id":job_id, "head_sha":sha}
    return found


def assemble(registry, selected, jobs, queue_path, token=None, include_issues=True):
    if (not isinstance(selected, list) or not 1 <= len(selected) <= MAX_PROJECTS
            or len(selected) != len(set(selected))
            or any(name not in registry for name in selected)):
        raise ValueError("select one to three distinct canonical projects")
    pins = parse_jobs(jobs, selected)
    queue = observe(queue_path)
    observed = datetime.now(UTC)
    # The baseline lacks Notion/worker provenance by design; absence is NOT
    # proof that a task has no owner or that a project is recovered.
    bundle = {
        "schema_version": 1,
        "evidence": {
            "schema_version": 1,
            "observed_at": utc_string(observed),
            "projects": [
                {"id": pid, "notion": {}, "github": pins.get(pid, {}), "runtime": {}}
                for pid in selected
            ],
        },
        "tasks": [], "active_claims": [],
        "task_inventory_complete": False,
        "claim_inventory_complete": False,
        "queue_pressure": queue,
    }
    source = {
        "queue": "sqlite_readonly_live_observation",
        "notion": "not_observed_no_ownership_claim",
        "runtime": "not_observed_no_worker_recovery_claim",
        "current_claim_inventory": "unknown_and_not_used_for_routing",
        "max_github_gets": 2*len(pins) + (3*len(selected) if include_issues else 0),
        "github_budget_is_per_invocation_not_global": True,
    }
    if pins:
        bundle, github = refresh(bundle, registry, token)
        source["github_jobs"] = github
    else:
        source["github_jobs"] = "no_pinned_jobs_not_a_zero_incident_claim"
    if include_issues:
        try:
            inbox = collect(registry, selected, token, max_items=40)
            bundle["issue_inbox"] = inbox
            source["issue_inbox"] = "bounded_readonly"
            source["issue_requests"] = inbox.get("request_count")
        except (OSError, ValueError):
            source["issue_inbox"] = "degraded_unavailable"
    else:
        source["issue_inbox"] = "explicitly_not_requested"
    return bundle, source


def main():
    parser = argparse.ArgumentParser(
        description="One command read-only queue + issue/GitHub zSentinel cockpit")
    parser.add_argument("--database", required=True, type=Path,
                        help="existing zCloud SQLite path; query_only/mode=ro")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append", required=True,
                        help="canonical project ID, repeat up to three times")
    parser.add_argument("--job", action="append", default=[],
                        help="optional PROJECT:RUN_ID:JOB_ID:EXACT_HEAD_SHA")
    parser.add_argument("--skip-github-issues", action="store_true",
                        help="explicitly skip public GitHub issue/PR API reads")
    parser.add_argument("--state", type=Path,
                        help="optional local private incident ledger file")
    parser.add_argument("--format", choices=("text", "json", "html"), default="html")
    args = parser.parse_args()
    try:
        registry = canonical_projects(args.registry)
        bundle, provenance = assemble(
            registry, args.project, args.job, args.database,
            token=os.environ.get("GITHUB_TOKEN"),
            include_issues=not args.skip_github_issues,
        )
        previous = (read_json(args.state, MAX_STATE_BYTES, "state")
                    if args.state and args.state.exists() else None)
        decision, ledger = plan(bundle, previous)
        decision["live_sources"] = provenance
        if args.state:
            write_json_atomic(args.state, ledger)
        decision["private_ledger_written"] = bool(args.state)
        if args.format == "html":
            from zsentinel_recovery_view import html_view
            print(html_view(decision))
        elif args.format == "json":
            print(json.dumps(decision, ensure_ascii=True, sort_keys=True, indent=2))
        else:
            print(operator_text(decision))
            print("NOTION / WORKER CLAIMS: UNOBSERVED. No routing or recovery authorized.")
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, f"zSentinel live cockpit unavailable: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

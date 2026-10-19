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
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import bounded_get, canonical_projects
from zsentinel_recovery_router import (
    MAX_INPUT_BYTES, MAX_STATE_BYTES, plan, read_json, write_json_atomic,
    operator_text, utc_string,
)

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
            conclusion = job.get("conclusion")
            if status == "completed" and conclusion not in CONCLUSION:
                raise ValueError("github_job_conclusion_unsupported")
            updated = {
                "run_id": run_id, "job_id": job_id,
                "head_sha": sha, "job_status": status,
                "queued_at": utc_string(queued_at),
                "observed_at": utc_string(observed),
                "runner_assigned": bool(runner_id),
            }
            if status == "completed":
                updated["conclusion"] = conclusion
                steps = job.get("steps")
                if isinstance(steps, list):
                    updated["no_job_steps"] = len(steps) == 0
            # Drop an old unproven diagnosis instead of replaying 'billing' as fact.
            # Only an independently verified fresh source may set the category.
            project["github"] = updated
            output.append({"project_id": pid, "source": "github_exact_run_job",
                           "status": "refreshed", "run_id": run_id, "job_id": job_id})
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
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args()
    try:
        registry = canonical_projects(args.registry)
        bundle = read_json(args.bundle, MAX_INPUT_BYTES, "bundle")
        old = read_json(args.state, MAX_STATE_BYTES, "state") if args.state and Path(args.state).exists() else None
        enriched, source = refresh(bundle, registry, os.environ.get("GITHUB_TOKEN"))
        routed, updated = plan(enriched, old)
        routed["live_github_read"] = source
        if args.state:
            write_json_atomic(args.state, updated)
        print(json.dumps(routed, sort_keys=True, indent=2) if args.format == "json"
              else operator_text(routed) + "\nGitHub coverage: " + source["github_coverage"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"zSentinel read-only refresh refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

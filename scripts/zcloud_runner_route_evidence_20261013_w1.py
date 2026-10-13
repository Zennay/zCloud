#!/usr/bin/env python3
"""Read-only, fail-closed GitHub Actions job-to-runner route evidence for zCloud.

This diagnostic cannot restart runners, dispatch jobs, mutate queues or authorize
a deployment. It deliberately keeps zcloud/vps distinct from ftmo-research.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request

REPO = "Zennay/zCloud"
CANONICAL_REPOSITORY_ID = 1389227570
CANONICAL_DEPLOY_WORKFLOW_ID = 369569470
CANONICAL_DEPLOY_WORKFLOW_PATH = ".github/workflows/zcloud-vps-deploy.yml"
CANONICAL_VPS_RUNNER_NAME = "zcloud-vps-1"
API = f"https://api.github.com/repos/{REPO}/actions/runs"
MAX_PAGES = 10
MAX_BYTES = 1024 * 1024
ALLOWED_STATUS = {"queued", "in_progress", "completed", "waiting", "pending", "requested"}
ALLOWED_CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "skipped",
                       "timed_out", "action_required", "startup_failure", "stale"}
LANES = {"zcloud_vps": {"self-hosted", "zcloud", "vps"},
         "ftmo_research": {"self-hosted", "ftmo-research"}}


class InvalidEvidence(ValueError):
    """A proof is unavailable or contradictory; never treat it as success."""


def strict_json(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise InvalidEvidence("oversized_payload")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise InvalidEvidence("duplicate_json_key")
            out[key] = value
        return out
    def bad_constant(_):
        raise InvalidEvidence("nonfinite_json")
    def finite_float(raw):
        num = float(raw)
        if not math.isfinite(num):
            raise InvalidEvidence("nonfinite_json")
        return num
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=bad_constant, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise InvalidEvidence("invalid_json") from exc
    if type(value) is not dict:
        raise InvalidEvidence("nonobject_json")
    remaining = [100000]
    def check(node, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 48:
            raise InvalidEvidence("json_structure_limit")
        if type(node) is str:
            if any(0xd800 <= ord(char) <= 0xdfff for char in node):
                raise InvalidEvidence("invalid_unicode")
        elif type(node) is dict:
            for key, child in node.items():
                check(key, depth + 1)
                check(child, depth + 1)
        elif type(node) is list:
            for child in node:
                check(child, depth + 1)
        elif type(node) is float and not math.isfinite(node):
            raise InvalidEvidence("nonfinite_json")
    check(value)
    return value


def _age(timestamp: object, now: datetime) -> int:
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        raise InvalidEvidence("invalid_job_timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEvidence("invalid_job_timestamp") from exc
    if parsed.tzinfo is None or now.tzinfo is None:
        raise InvalidEvidence("timezone_missing")
    seconds = (now - parsed).total_seconds()
    if seconds < -5:
        raise InvalidEvidence("future_job_timestamp")
    return max(0, int(seconds))


# Only fixed categories leave this diagnostic; GitHub step titles remain private.
FAILED_STEP_CATEGORIES = {
    "Enter browser safe-idle for guarded promotion": "browser_safe_idle",
    "Promote backend runtime core": "runtime_promotion",
    "Re-arm VPS-native self-heal after guarded deploy": "self_heal_rearm",
    "Mark production deploy pending": "production_status",
    "Resolve latest green main revision": "regression_provenance",
    "Reconfirm green main before VPS writes": "regression_provenance",
    "Enforce permanent zCloud VPS runner identity": "runner_identity",
}


def _failure_phase(job: dict) -> str | None:
    """Explain terminal failure from job steps; never expose arbitrary step names."""
    if job.get("conclusion") != "failure":
        return None
    steps = job.get("steps")
    if steps is None or steps == []:
        # GitHub may omit steps entirely even for terminal failed jobs.
        return "step_evidence_missing"
    if type(steps) is not list or len(steps) > 150:
        raise InvalidEvidence("invalid_step_evidence")
    failures = []
    for step in steps:
        if type(step) is not dict or type(step.get("name")) is not str:
            raise InvalidEvidence("invalid_step_evidence")
        if len(step["name"]) > 200:
            raise InvalidEvidence("invalid_step_evidence")
        c = step.get("conclusion")
        if c is not None and (type(c) is not str or c not in
                              {"success", "failure", "skipped", "cancelled", "timed_out",
                               "neutral", "action_required"}):
            raise InvalidEvidence("invalid_step_evidence")
        if c == "failure":
            failures.append(FAILED_STEP_CATEGORIES.get(step["name"], "other_step"))
    if len(failures) == 0:
        # A terminal failure with no failed step is contradictory/incomplete.
        return "step_evidence_inconsistent"
    if len(set(failures)) > 1:
        return "multiple_failure_phases"
    return failures[0]


def classify_job(job: dict, now: datetime) -> dict:
    if type(job) is not dict or type(job.get("id")) is not int or job["id"] <= 0:
        raise InvalidEvidence("invalid_job_identity")
    status = job.get("status")
    if type(status) is not str or status not in ALLOWED_STATUS:
        raise InvalidEvidence("invalid_job_status")
    labels = job.get("labels")
    if not isinstance(labels, list) or not all(type(x) is str and 0 < len(x) < 80 for x in labels):
        raise InvalidEvidence("invalid_job_labels")
    label_set = {x.lower() for x in labels}
    matches = [name for name, required in LANES.items() if required <= label_set]
    if len(matches) > 1:
        raise InvalidEvidence("mixed_runner_lane")
    lane = matches[0] if matches else "other"
    runner_id = job.get("runner_id")
    runner_name = job.get("runner_name")
    if runner_id is None:
        runner_id = 0
    if type(runner_id) is not int or runner_id < 0:
        raise InvalidEvidence("invalid_runner_identity")
    if runner_id > 0 and (not isinstance(runner_name, str) or not runner_name.strip()):
        raise InvalidEvidence("partial_runner_identity")
    if runner_id == 0 and isinstance(runner_name, str) and runner_name.strip():
        raise InvalidEvidence("partial_runner_identity")
    age = _age(job.get("created_at"), now)
    conclusion = job.get("conclusion")
    if conclusion is not None and (
            type(conclusion) is not str or conclusion not in ALLOWED_CONCLUSIONS):
        raise InvalidEvidence("invalid_conclusion")
    if conclusion == "skipped" and runner_id > 0:
        raise InvalidEvidence("skipped_job_claims_runner")
    if status == "completed" and conclusion is None:
        raise InvalidEvidence("completed_without_conclusion")
    if status != "completed" and conclusion is not None:
        raise InvalidEvidence("nonterminal_with_conclusion")
    if runner_id and status in {"in_progress", "completed"}:
        route = "runner_assigned"
    elif runner_id:
        route = "inconsistent_runner_state"
    elif status == "queued":
        route = "unassigned"
    elif status in {"waiting", "pending", "requested"}:
        # Pending authorization/prerequisites are not an offline runner proof.
        route = "not_yet_scheduled"
    elif status == "completed" and conclusion == "skipped":
        route = "not_dispatched"
    else:
        route = "missing_runner_identity"
    canonical_vps_runner = (lane == "zcloud_vps" and route == "runner_assigned"
                            and runner_name == CANONICAL_VPS_RUNNER_NAME)
    return {"job_id": job["id"], "lane": lane, "status": status, "route": route,
            "queue_age_seconds": age if route == "unassigned" else None,
            "runner_id": runner_id if route == "runner_assigned" else None,
            "canonical_vps_runner": bool(canonical_vps_runner),
            "conclusion": conclusion, "failure_phase": _failure_phase(job)}


def classify_run(run_id: int, jobs: list, now: datetime | None = None) -> dict:
    if type(run_id) is not int or run_id < 1:
        raise InvalidEvidence("invalid_run_id")
    if not isinstance(jobs, list) or not jobs:
        raise InvalidEvidence("missing_jobs")
    now = now or datetime.now(timezone.utc)
    observed = [classify_job(job, now) for job in jobs]
    ids = [job["job_id"] for job in observed]
    if len(set(ids)) != len(ids):
        raise InvalidEvidence("duplicate_job_id")
    lanes = {key: [job for job in observed if job["lane"] == key] for key in LANES}
    results = {}
    for name, relevant in lanes.items():
        # A completed failed job proves scheduling worked, not deployment success.
        results[name] = {
            "job_count": len(relevant),
            "assigned_count": sum(j["route"] == "runner_assigned" for j in relevant),
            "canonical_vps_assigned_count": sum(j["canonical_vps_runner"] for j in relevant),
            "unexpected_vps_assigned_count": sum(
                j["route"] == "runner_assigned" and not j["canonical_vps_runner"]
                for j in relevant) if name == "zcloud_vps" else 0,
            "unassigned_count": sum(j["route"] == "unassigned" for j in relevant),
            "not_yet_scheduled_count": sum(
                j["route"] == "not_yet_scheduled" for j in relevant),
            "not_dispatched_count": sum(
                j["route"] == "not_dispatched" for j in relevant),
            "unproven_runner_count": sum(
                j["route"] in {"inconsistent_runner_state", "missing_runner_identity"}
                for j in relevant),
            "max_unassigned_seconds": max(
                (j["queue_age_seconds"] for j in relevant if j["queue_age_seconds"] is not None),
                default=None),
            "failed_jobs": sum(j["conclusion"] == "failure" for j in relevant),
            "non_successful_terminal_count": sum(
                j["status"] == "completed" and
                j["conclusion"] not in {"success", "skipped"} for j in relevant),
            "failure_phases": {
                phase: sum(j["failure_phase"] == phase for j in relevant)
                for phase in sorted(set(j["failure_phase"] for j in relevant)
                                    - {None})
            },
        }
    return {"schema": "zcloud-runner-route-evidence-v1",
            "run_id": run_id, "authorizes_mutation": False,
            "production_deploy_verified": False,
            "lanes": results, "jobs": observed}


def _github_api_http_error(exc: urllib.error.HTTPError) -> InvalidEvidence:
    """An authorization/API failure is not evidence of an offline VPS runner."""
    if exc.code in (401, 403):
        return InvalidEvidence("github_api_permission_or_limit")
    if exc.code == 404:
        return InvalidEvidence("github_api_missing_or_inaccessible")
    if exc.code == 429:
        return InvalidEvidence("github_api_rate_limited")
    return InvalidEvidence("github_api_unavailable")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise InvalidEvidence("unexpected_redirect")


def _verified_job_provenance(job: dict, requested_run_id: int) -> str:
    """Bind a job receipt to its requested run, not just the runner labels."""
    if type(job) is not dict or type(job.get("run_id")) is not int:
        raise InvalidEvidence("untrusted_job_provenance")
    if job["run_id"] != requested_run_id:
        raise InvalidEvidence("cross_run_job_evidence")
    sha = job.get("head_sha")
    if type(sha) is not str or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise InvalidEvidence("untrusted_job_provenance")
    name = job.get("workflow_name")
    attempt = job.get("run_attempt")
    job_id = job.get("id")
    if (type(name) is not str or not 0 < len(name) <= 180
            or type(attempt) is not int or attempt < 1
            or type(job_id) is not int or job_id < 1):
        raise InvalidEvidence("untrusted_job_provenance")
    if (job.get("run_url") != f"{API}/{requested_run_id}"
            or job.get("url") != f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}"
            or job.get("html_url") !=
            f"https://github.com/{REPO}/actions/runs/{requested_run_id}/job/{job_id}"):
        raise InvalidEvidence("untrusted_job_provenance")
    return (sha, attempt)


def _validated_token(token: str) -> None:
    """Reject malformed bearer credentials before constructing any HTTP header."""
    if not token:
        raise InvalidEvidence("missing_actions_token")
    if (type(token) is not str or len(token) > 2048
            or re.fullmatch(r"[A-Za-z0-9_.-]+", token) is None):
        raise InvalidEvidence("invalid_actions_token")


def fetch_jobs(run_id: int, token: str) -> list:
    _validated_token(token)
    opener = urllib.request.build_opener(NoRedirect())
    jobs = []
    count = None
    run_head_sha = None
    run_workflow_name = None
    run_attempt = None
    for page in range(1, MAX_PAGES + 1):
        req = urllib.request.Request(
            f"{API}/{run_id}/jobs?filter=latest&per_page=100&page={page}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "zcloud-deployops-runner-route-evidence"})
        try:
            with opener.open(req, timeout=15) as response:
                if response.headers.get_content_type() != "application/json":
                    raise InvalidEvidence("nonjson_api_reply")
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise _github_api_http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise InvalidEvidence("github_api_unavailable") from exc
        payload = strict_json(raw)
        page_jobs = payload.get("jobs")
        total = payload.get("total_count")
        if type(total) is not int or total < 0 or type(page_jobs) is not list:
            raise InvalidEvidence("malformed_pagination")
        if count is None:
            count = total
        if total != count or len(page_jobs) > 100:
            raise InvalidEvidence("unstable_pagination")
        for job in page_jobs:
            sha, attempt = _verified_job_provenance(job, run_id)
            if run_head_sha is not None and sha != run_head_sha:
                raise InvalidEvidence("mixed_run_head_sha")
            if run_workflow_name is not None and job["workflow_name"] != run_workflow_name:
                raise InvalidEvidence("mixed_run_workflow_name")
            if run_attempt is not None and attempt != run_attempt:
                raise InvalidEvidence("mixed_run_attempts")
            run_head_sha = sha
            run_workflow_name = job["workflow_name"]
            run_attempt = attempt
        jobs.extend(page_jobs)
        # Reject duplicate-offset pages even before the caller classifies a run.
        job_ids = [job.get("id") for job in jobs]
        if (any(type(job_id) is not int or job_id < 1 for job_id in job_ids)
                or len(set(job_ids)) != len(job_ids)):
            raise InvalidEvidence("duplicate_job_id")
        if len(jobs) == count:
            return jobs
        if len(jobs) > count or not page_jobs:
            raise InvalidEvidence("incomplete_pagination")
    raise InvalidEvidence("page_limit_exceeded")


def _verified_main_deploy_run(item: dict, now: datetime) -> tuple[str, int]:
    """Canonical GitHub metadata, never a permission to promote to production."""
    if type(item) is not dict:
        raise InvalidEvidence("untrusted_deploy_run")
    if (item.get("name") != "zCloud VPS deploy"
            or item.get("head_branch") != "main"
            or item.get("event") != "workflow_run"
            or item.get("path") != CANONICAL_DEPLOY_WORKFLOW_PATH
            or type(item.get("workflow_id")) is not int
            or item["workflow_id"] != CANONICAL_DEPLOY_WORKFLOW_ID):
        raise InvalidEvidence("untrusted_deploy_run")
    for source in ("repository", "head_repository"):
        repo = item.get(source)
        if type(repo) is not dict or type(repo.get("id")) is not int:
            raise InvalidEvidence("untrusted_deploy_run")
        if repo["id"] != CANONICAL_REPOSITORY_ID or repo.get("full_name") != REPO:
            raise InvalidEvidence("untrusted_deploy_run")
    run_id, attempt, sha = item.get("id"), item.get("run_attempt"), item.get("head_sha")
    if (type(run_id) is not int or run_id < 1
            or type(attempt) is not int or attempt < 1
            or type(sha) is not str or re.fullmatch(r"[0-9a-f]{40}", sha) is None):
        raise InvalidEvidence("untrusted_deploy_run")
    stamp = item.get("created_at")
    _age(stamp, now)
    return (stamp, run_id)


def latest_main_deploy_receipts(token: str, count: int = 2) -> list[tuple[int, str, int]]:
    """Bind each newest main deploy to its immutable head SHA and run attempt."""
    _validated_token(token)
    if type(count) is not int or not 1 <= count <= 3:
        raise InvalidEvidence("invalid_run_selection")
    url = ("https://api.github.com/repos/Zennay/zCloud/actions/workflows/"
           "zcloud-vps-deploy.yml/runs?branch=main&per_page=10")
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}",
                      "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28",
                      "User-Agent": "zcloud-deployops-runner-route-evidence"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            if response.headers.get_content_type() != "application/json":
                raise InvalidEvidence("nonjson_api_reply")
            payload = strict_json(response.read(MAX_BYTES + 1))
    except urllib.error.HTTPError as exc:
        raise _github_api_http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise InvalidEvidence("github_api_unavailable") from exc
    runs = payload.get("workflow_runs")
    total = payload.get("total_count")
    if (type(runs) is not list or type(total) is not int or total < 0
            or len(runs) > 10 or len(runs) > total):
        raise InvalidEvidence("malformed_pagination")
    now = datetime.now(timezone.utc)
    keyed = [(_verified_main_deploy_run(item, now), item["id"]) for item in runs]
    ids = [run_id for _, run_id in sorted(keyed, reverse=True)]
    if len(ids) < count or len(set(ids)) != len(ids):
        raise InvalidEvidence("incomplete_recent_deploys")
    by_id = {item["id"]: item for item in runs}
    return [(run_id, by_id[run_id]["head_sha"], by_id[run_id]["run_attempt"])
            for run_id in ids[:count]]


def latest_main_deploy_ids(token: str, count: int = 2) -> list[int]:
    """Backward-compatible ID-only observation, never release authority."""
    return [run_id for run_id, _, _ in latest_main_deploy_receipts(token, count)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=[], action="append")
    parser.add_argument("--recent-deploy", action="store_true",
                        help="Also inspect the latest two main deployment runs")
    args = parser.parse_args(argv)
    if (not args.run_id and not args.recent_deploy or
            len(args.run_id) > 10 or len(set(args.run_id)) != len(args.run_id)):
        print('{"error":"invalid_run_selection","authorizes_mutation":false}')
        return 2
    token = os.environ.get("GH_TOKEN", "")
    try:
        ids = list(args.run_id)
        expected_deploy = {}
        if args.recent_deploy:
            receipts = latest_main_deploy_receipts(token)
            ids.extend([run_id for run_id, _, _ in receipts])
            expected_deploy = {run_id: (sha, attempt)
                               for run_id, sha, attempt in receipts}
        # Duplicates across requested/recent run IDs are the same observation.
        ids = list(dict.fromkeys(ids))
        if len(ids) > 10:
            raise InvalidEvidence("invalid_run_selection")
        data = []
        for run_id in ids:
            jobs = fetch_jobs(run_id, token)
            if run_id in expected_deploy:
                sha, attempt = expected_deploy[run_id]
                if not all(job["head_sha"] == sha and job["run_attempt"] == attempt
                           for job in jobs):
                    raise InvalidEvidence("deploy_job_metadata_mismatch")
            data.append(classify_run(run_id, jobs))
    except (InvalidEvidence, OSError) as exc:
        # Emit only a finite allowlisted diagnostic; never dump URL, token or logs.
        reason = str(exc)
        safe = reason if reason in {
            "oversized_payload","duplicate_json_key","nonfinite_json","invalid_json",
            "nonobject_json","invalid_job_timestamp","timezone_missing",
            "future_job_timestamp","invalid_job_identity","invalid_job_status",
            "invalid_job_labels","mixed_runner_lane","invalid_runner_identity",
            "skipped_job_claims_runner",
            "partial_runner_identity","invalid_conclusion","completed_without_conclusion",
            "nonterminal_with_conclusion","inconsistent_runner_state","missing_jobs",
            "duplicate_job_id","invalid_run_id","missing_actions_token",
            "invalid_actions_token",
            "unexpected_redirect","nonjson_api_reply","github_api_unavailable",
            "github_api_permission_or_limit","github_api_missing_or_inaccessible",
            "github_api_rate_limited",
            "malformed_pagination","unstable_pagination","incomplete_pagination",
            "page_limit_exceeded","incomplete_recent_deploys","invalid_run_selection",
            "invalid_step_evidence","untrusted_deploy_run","invalid_unicode",
            "json_structure_limit","untrusted_job_provenance",
            "cross_run_job_evidence","mixed_run_head_sha",
            "mixed_run_workflow_name","mixed_run_attempts",
            "deploy_job_metadata_mismatch"} else "unavailable"
        print(json.dumps({"schema": "zcloud-runner-route-evidence-v1",
                          "error": safe, "authorizes_mutation": False}))
        return 2
    print(json.dumps({"schema":"zcloud-runner-route-evidence-v1",
                      "authorizes_mutation":False, "runs":data}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

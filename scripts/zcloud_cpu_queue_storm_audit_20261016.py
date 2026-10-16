#!/usr/bin/env python3
"""Bounded read-only receipt for repeated queued zCloud VPS CPU diagnostics.

Never cancels, dispatches, approves, restarts or authorizes anything.
Not proof that a VPS process, listener or runner is healthy.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "Zennay/zCloud"
WORKFLOW = "vps-cpu-diagnostic.yml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW}"
WORKFLOW_NAME = "VPS CPU diagnostic snapshot"
BASE = f"https://api.github.com/repos/{REPO}"
REQUIRED = frozenset(("self-hosted", "zcloud", "vps"))
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_SAMPLE = 30
MAX_BYTES = 2 * 1024 * 1024
MIN_STALE_SECONDS = 15 * 60


class Unavailable(ValueError):
    """Ambiguous source: never convert into affirmative runner diagnosis."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable("redirect_denied")


def strict_json(data: bytes) -> dict:
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise Unavailable("invalid_body_size")

    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise Unavailable("duplicate_json_key")
            obj[key] = value
        return obj

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(Unavailable("nonfinite_json")))
    except Unavailable:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Unavailable("invalid_json") from exc
    if type(value) is not dict:
        raise Unavailable("nonobject_json")
    return value


def utc_time(raw: object) -> datetime:
    if type(raw) is not str or not 10 <= len(raw) <= 40:
        raise Unavailable("invalid_time")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Unavailable("invalid_time") from exc
    if parsed.tzinfo is None:
        raise Unavailable("timezone_missing")
    return parsed.astimezone(timezone.utc)


def check_positive_int(value: object, name: str) -> int:
    if type(value) is not int or not 0 < value < 2**63:
        raise Unavailable(name)
    return value


def canonical_run(raw: object, now: datetime) -> dict:
    if type(raw) is not dict:
        raise Unavailable("run_invalid")
    rid = check_positive_int(raw.get("id"), "run_id_invalid")
    repo = raw.get("repository")
    head_repo = raw.get("head_repository")
    if not (type(repo) is dict and type(head_repo) is dict
            and repo.get("full_name") == REPO and head_repo.get("full_name") == REPO):
        raise Unavailable("repo_provenance_invalid")
    raw_path = raw.get("path")
    if (type(raw_path) is not str
            or raw_path.split("@", 1)[0] != WORKFLOW_PATH
            or raw.get("name") != WORKFLOW_NAME):
        raise Unavailable("workflow_provenance_invalid")
    branch = raw.get("head_branch")
    head_sha = raw.get("head_sha")
    if (type(branch) is not str or not branch or len(branch) > 255
            or type(head_sha) is not str or SHA.fullmatch(head_sha) is None):
        raise Unavailable("head_provenance_invalid")
    attempt = check_positive_int(raw.get("run_attempt"), "attempt_invalid")
    if attempt > 100:
        raise Unavailable("attempt_unbounded")
    event = raw.get("event")
    if event not in ("pull_request", "workflow_dispatch"):
        raise Unavailable("event_untrusted")
    status = raw.get("status")
    if status not in ("queued", "waiting", "pending", "requested"):
        raise Unavailable("unexpected_run_status")
    if raw.get("conclusion") is not None:
        raise Unavailable("run_conclusion_unexpected")
    created = utc_time(raw.get("created_at"))
    age = (now - created).total_seconds()
    if age < -5:
        raise Unavailable("future_run")
    return {"id": rid, "sha": head_sha, "branch": branch, "event": event,
            "attempt": attempt, "status": status, "created": created.isoformat()}


def canonical_job(raw: object, run: dict, now: datetime) -> dict:
    if type(raw) is not dict:
        raise Unavailable("job_invalid")
    jid = check_positive_int(raw.get("id"), "job_id_invalid")
    if (raw.get("run_id") != run["id"] or raw.get("run_attempt") != run["attempt"]
            or raw.get("name") != "cpu-snapshot"
            or raw.get("workflow_name") != WORKFLOW_NAME
            or raw.get("head_sha") != run["sha"]
            or raw.get("head_branch") != run["branch"]):
        raise Unavailable("job_run_binding_invalid")
    if (raw.get("run_url") != f"{BASE}/actions/runs/{run['id']}"
            or raw.get("url") != f"{BASE}/actions/jobs/{jid}"
            or raw.get("html_url") != f"https://github.com/{REPO}/actions/runs/{run['id']}/job/{jid}"):
        raise Unavailable("job_url_binding_invalid")
    labels = raw.get("labels")
    if (type(labels) is not list or not all(type(label) is str for label in labels)
            or len(labels) != len(set(labels)) or not REQUIRED.issubset(set(labels))):
        raise Unavailable("job_runner_labels_invalid")
    if set(labels) - (REQUIRED | {"linux", "x64", "arm64"}):
        raise Unavailable("job_runner_labels_unexpected")
    runner_id = raw.get("runner_id")
    if runner_id is None:
        runner_id = 0
    if type(runner_id) is not int or runner_id < 0:
        raise Unavailable("job_runner_id_invalid")
    runner_name = raw.get("runner_name")
    if runner_id == 0 and runner_name not in (None, ""):
        raise Unavailable("job_runner_name_inconsistent")
    if runner_id > 0 and (type(runner_name) is not str or not runner_name):
        raise Unavailable("job_runner_name_missing")
    status = raw.get("status")
    if status not in ("queued", "waiting", "pending", "requested", "in_progress"):
        raise Unavailable("job_status_unexpected")
    if raw.get("conclusion") is not None:
        raise Unavailable("job_not_pending")
    created = utc_time(raw.get("created_at"))
    seconds = (now - created).total_seconds()
    if seconds < -5:
        raise Unavailable("future_job")
    if created < datetime.fromisoformat(run["created"]) and (
            datetime.fromisoformat(run["created"]) - created).total_seconds() > 5:
        raise Unavailable("job_before_run")
    return {"id": jid, "run_id": run["id"], "runner_id": runner_id,
            "status": status, "age_seconds": int(max(0, seconds))}


def read_sample(runs_payload: dict, jobs_by_run: dict, now: datetime, cap: int) -> dict:
    if type(runs_payload) is not dict or type(jobs_by_run) is not dict:
        raise Unavailable("inventory_invalid")
    count = runs_payload.get("total_count")
    runs = runs_payload.get("workflow_runs")
    if (type(count) is not int or count < 0 or type(runs) is not list
            or len(runs) != min(count, cap)):
        raise Unavailable("sample_incomplete")
    seen_runs, seen_jobs = set(), set()
    accepted = []
    for item in runs:
        run = canonical_run(item, now)
        if run["id"] in seen_runs:
            raise Unavailable("duplicate_run")
        seen_runs.add(run["id"])
        payload = jobs_by_run.get(run["id"])
        if type(payload) is not dict or payload.get("total_count") != 1:
            raise Unavailable("job_page_incomplete")
        jobs = payload.get("jobs")
        if type(jobs) is not list or len(jobs) != 1:
            raise Unavailable("job_cardinality")
        job = canonical_job(jobs[0], run, now)
        if job["id"] in seen_jobs:
            raise Unavailable("duplicate_job")
        seen_jobs.add(job["id"])
        accepted.append({"run": run, "job": job})
    if set(jobs_by_run) != seen_runs:
        raise Unavailable("job_inventory_mismatch")
    return {"total_count": count, "sampled": accepted, "complete": count <= cap}


def receipt(snapshot: dict, *, stable: bool) -> dict:
    groups = defaultdict(list)
    unassigned = 0
    stale = 0
    approval = 0
    assigned = 0
    for item in snapshot["sampled"]:
        run, job = item["run"], item["job"]
        if run["status"] != "queued":
            approval += 1
            continue
        if job["runner_id"] != 0 or job["status"] == "in_progress":
            assigned += 1
            continue
        if job["status"] != "queued":
            approval += 1
            continue
        unassigned += 1
        if job["age_seconds"] >= MIN_STALE_SECONDS:
            stale += 1
        groups[(run["branch"], run["sha"], run["event"])].append(item)
    duplicate = []
    for (_, sha, event), items in groups.items():
        if len(items) < 2:
            continue
        ids = sorted((part["run"]["id"] for part in items))
        duplicate.append({"head_sha_prefix": sha[:12], "event": event,
                          "sample_count": len(items), "run_ids": ids[:MAX_SAMPLE],
                          "oldest_job_minutes": max(x["job"]["age_seconds"] for x in items) // 60})
    duplicate.sort(key=lambda g: (-g["sample_count"], g["run_ids"][0]))
    return {"schema": "zcloud-cpu-queue-storm-readonly-v1",
            "status": "STABLE_READONLY_SAMPLE" if stable else "EVIDENCE_CHANGED",
            "complete": snapshot["complete"] and stable,
            "total_queued_workflow_runs": snapshot["total_count"],
            "sample_count": len(snapshot["sampled"]),
            "sampled_unassigned": unassigned,
            "sampled_unassigned_over_15m": stale,
            "sampled_approval_or_prestart": approval,
            "sampled_assigned": assigned,
            "repeated_sha_groups": duplicate if stable else [],
            "runner_offline_verified": False, "vps_health_verified": False,
            "cancel_authorized": False, "deploy_authorized": False,
            "mutation_performed": False,
            "recommendation": "review_with_existing_runner_owner_only"}


def get_json(url: str, token: str, opener) -> dict:
    if not url.startswith(BASE + "/actions/"):
        raise Unavailable("url_not_allowlisted")
    req = Request(url, headers={"Authorization": f"Bearer {token}",
                  "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28",
                  "User-Agent": "zcloud-cpu-queue-storm-readonly"})
    try:
        with opener.open(req, timeout=12) as response:
            if response.geturl() != url:
                raise Unavailable("untrusted_redirect")
            if response.headers.get_content_type() != "application/json":
                raise Unavailable("wrong_content_type")
            return strict_json(response.read(MAX_BYTES + 1))
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise Unavailable("github_observation_failed") from exc


def collect(token: str, cap: int = 20, *, getter=None, now=None) -> dict:
    if type(token) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,2048}", token) is None:
        raise Unavailable("invalid_token")
    if type(cap) is not int or not 1 <= cap <= MAX_SAMPLE:
        raise Unavailable("invalid_cap")
    if getter is None:
        opener = build_opener(NoRedirect())
        getter = lambda url: get_json(url, token, opener)
    if now is None:
        now = datetime.now(timezone.utc)
    if type(now) is not datetime or now.tzinfo is None:
        raise Unavailable("invalid_clock")
    url = f"{BASE}/actions/workflows/{WORKFLOW}/runs?status=queued&per_page={cap}&page=1"

    def single_pass():
        payload = getter(url)
        runs = payload.get("workflow_runs")
        if type(runs) is not list or len(runs) > cap:
            raise Unavailable("sample_size_invalid")
        jobs = {}
        for run in runs:
            rid = run.get("id") if type(run) is dict else None
            rid = check_positive_int(rid, "run_id_invalid")
            if rid in jobs:
                raise Unavailable("duplicate_run")
            jobs[rid] = getter(f"{BASE}/actions/runs/{rid}/jobs?per_page=100")
        return read_sample(payload, jobs, now, cap)

    first = single_pass()
    second = single_pass()
    stable = first == second
    return receipt(second, stable=stable)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=20)
    args = parser.parse_args()
    try:
        out = collect(os.environ.get("GH_TOKEN", ""), cap=args.sample)
        code = 0 if out["status"] == "STABLE_READONLY_SAMPLE" else 2
    except (Unavailable, TypeError, ValueError, OverflowError):
        out = {"schema": "zcloud-cpu-queue-storm-readonly-v1",
               "status": "EVIDENCE_UNAVAILABLE", "cancel_authorized": False,
               "deploy_authorized": False, "runner_offline_verified": False,
               "mutation_performed": False}
        code = 2
    print(json.dumps(out, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only zCloud production evidence cockpit; it never grants release permission.

Produces sanitized JSON and an offline HTML operator view. This is an advisory
snapshot, not a replacement for #580/PWQ-41, #1089 or runner/runtime approval.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Zennay/zCloud"
API = "https://api.github.com"
EXPECTED_RUNNER = "zcloud-vps-1"
EXPECTED_LABELS = {"self-hosted", "zcloud", "vps"}
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_BYTES = 2_000_000


class EvidenceError(Exception):
    """Invalid, incomplete, or unstable GitHub source evidence."""


def require_sha(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise EvidenceError("invalid_commit_sha")
    return value


def integer(value: object) -> int:
    if type(value) is not int or value < 1:
        raise EvidenceError("invalid_positive_integer")
    return value


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def nonfinite(_: str) -> object:
    raise EvidenceError("nonfinite_json_number")


class GitHubReadOnly:
    def __init__(self, token: str | None = None):
        self.token = token

    def get(self, path: str) -> object:
        if not path.startswith("/repos/Zennay/zCloud/"):
            raise EvidenceError("noncanonical_api_path")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "zcloud-read-only-release-evidence",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(API + path, headers=headers, method="GET")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as r:
                if r.status != 200:
                    raise EvidenceError("unexpected_http_status")
                raw = r.read(MAX_BYTES + 1)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise EvidenceError("github_transport_unavailable") from exc
        if len(raw) > MAX_BYTES:
            raise EvidenceError("oversized_api_response")
        try:
            return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs,
                              parse_constant=nonfinite)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise EvidenceError("invalid_api_json") from exc


def main_sha(api) -> str:
    item = api.get("/repos/Zennay/zCloud/git/ref/heads/main")
    if not isinstance(item, dict) or not isinstance(item.get("object"), dict):
        raise EvidenceError("missing_main_ref")
    if item["object"].get("type") != "commit":
        raise EvidenceError("main_ref_not_commit")
    return require_sha(item["object"].get("sha"))


def production_status(api, sha: str) -> dict:
    latest = None
    count = 0
    for page in range(1, 6):
        payload = api.get(
            f"/repos/Zennay/zCloud/commits/{sha}/statuses?per_page=100&page={page}"
        )
        if not isinstance(payload, list) or len(payload) > 100:
            raise EvidenceError("incomplete_production_status_page")
        for row in payload:
            if not isinstance(row, dict):
                raise EvidenceError("malformed_production_status")
            if row.get("context") != "zcloud/vps-production":
                continue
            state = row.get("state")
            if state not in ("pending", "success", "failure", "error"):
                raise EvidenceError("unknown_production_state")
            stamp = row.get("created_at")
            if not isinstance(stamp, str) or not re.fullmatch(
                r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp
            ):
                raise EvidenceError("invalid_production_timestamp")
            status_id = integer(row.get("id"))
            key = (stamp, status_id)
            if latest is None or key > latest[0]:
                latest = (key, state)
            count += 1
        if len(payload) < 100:
            break
    else:
        raise EvidenceError("production_status_pagination_cap")
    return {"state": latest[1] if latest else "missing", "matching_events": count}


def current_deploy(api, sha: str) -> dict:
    payload = api.get(
        "/repos/Zennay/zCloud/actions/workflows/zcloud-vps-deploy.yml/runs"
        "?branch=main&per_page=100&page=1"
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise EvidenceError("invalid_workflow_runs")
    runs = payload["workflow_runs"]
    if len(runs) > 100:
        raise EvidenceError("oversized_workflow_page")
    matching = []
    for row in runs:
        if not isinstance(row, dict):
            raise EvidenceError("invalid_workflow_run")
        if row.get("head_sha") != sha:
            continue
        if row.get("head_branch") != "main" or row.get("event") != "workflow_run":
            raise EvidenceError("unexpected_deploy_provenance")
        run_id = integer(row.get("id"))
        attempt = integer(row.get("run_attempt"))
        status = row.get("status")
        conclusion = row.get("conclusion")
        if status not in ("queued", "in_progress", "completed", "waiting", "pending", "requested"):
            raise EvidenceError("unrecognized_deploy_run_status")
        if conclusion is not None and conclusion not in (
            "success", "failure", "cancelled", "skipped", "timed_out",
            "action_required", "neutral", "stale", "startup_failure",
        ):
            raise EvidenceError("unrecognized_deploy_conclusion")
        matching.append((run_id, attempt, status, conclusion))
    if not matching:
        return {
            "run_id": None, "attempt": None, "status": "not_observed",
            "conclusion": None, "runner_route": "UNVERIFIED",
            "matching_deploys_in_first_page": 0,
        }
    run_id, attempt, status, conclusion = max(matching, key=lambda x: (x[0], x[1]))
    jobs_payload = api.get(
        f"/repos/Zennay/zCloud/actions/runs/{run_id}/jobs?per_page=100&page=1"
    )
    if not isinstance(jobs_payload, dict) or not isinstance(jobs_payload.get("jobs"), list):
        raise EvidenceError("invalid_deploy_jobs")
    total = jobs_payload.get("total_count")
    if type(total) is not int or total < 0 or total > 100:
        raise EvidenceError("incomplete_deploy_jobs")
    jobs = jobs_payload["jobs"]
    if len(jobs) != total:
        raise EvidenceError("deploy_job_count_mismatch")
    found = []
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("labels"), list):
            raise EvidenceError("malformed_job")
        labels = job["labels"]
        if not all(isinstance(label, str) for label in labels):
            raise EvidenceError("malformed_runner_labels")
        if not EXPECTED_LABELS.issubset(set(labels)):
            continue
        job_status = job.get("status")
        if job_status not in ("queued", "in_progress", "completed", "waiting", "pending"):
            raise EvidenceError("unknown_job_status")
        runner_id = job.get("runner_id")
        runner_name = job.get("runner_name")
        if runner_id is not None and (type(runner_id) is not int or runner_id < 0):
            raise EvidenceError("malformed_runner_id")
        if runner_name is not None and not isinstance(runner_name, str):
            raise EvidenceError("malformed_runner_name")
        if runner_id and runner_name == EXPECTED_RUNNER:
            found.append("ASSIGNED_TO_EXPECTED")
        elif runner_id or runner_name:
            found.append("UNEXPECTED_RUNNER")
        elif job_status == "queued":
            found.append("QUEUED_UNASSIGNED")
        else:
            found.append("UNVERIFIED")
    # Any unexpected route or unassigned queue prevents an optimistic summary.
    priority = ("UNEXPECTED_RUNNER", "QUEUED_UNASSIGNED",
                "UNVERIFIED", "ASSIGNED_TO_EXPECTED")
    runner_route = next((name for name in priority if name in found),
                        "NO_SELF_HOSTED_JOB")
    return {
        "run_id": run_id, "attempt": attempt, "status": status,
        "conclusion": conclusion, "runner_route": runner_route,
        "matching_deploys_in_first_page": len(matching),
    }


def collect(api) -> dict:
    sha = main_sha(api)
    prod = production_status(api, sha)
    deploy = current_deploy(api, sha)
    if main_sha(api) != sha:
        raise EvidenceError("main_moved_during_observation")
    healthy_production_observed = prod["state"] == "success"
    warnings = []
    if not healthy_production_observed:
        warnings.append("production_not_success")
    if deploy["runner_route"] != "ASSIGNED_TO_EXPECTED":
        warnings.append("runner_route_not_proven")
    if deploy["conclusion"] != "success":
        warnings.append("deploy_run_not_success")
    return {
        "schema": "zcloud-deploy-ops-evidence-v1",
        "repository": REPO, "main_sha": sha,
        "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "production": prod, "deploy": deploy, "warnings": warnings,
        "observation": "GREEN_STATUS_IS_NOT_RELEASE_PERMISSION",
        "release_authorized": False, "deployment_authorized": False,
        "mutations_performed": False,
    }


def render_html(snapshot: dict) -> str:
    def safe(value: object) -> str:
        return html.escape(str(value), quote=True)
    p, d = snapshot["production"], snapshot["deploy"]
    warnings = "".join(f"<li>{safe(w)}</li>" for w in snapshot["warnings"])
    return """<!doctype html><html lang="nl"><meta charset="utf-8">
<title>zCloud deploy-ops evidence</title>
<style>body{max-width:850px;margin:2rem auto;padding:1rem;font:16px system-ui,sans-serif;
line-height:1.6;background:#f8f8fa;color:#171c25}article{background:white;padding:1.5rem;
border:1px solid #d3d6dd;border-radius:12px}dt{font-weight:bold;margin-top:1rem}
dd{margin-left:0;overflow-wrap:anywhere}.warning{font-weight:700}</style>
<article><h1>zCloud · releasebewijs</h1>
<p class="warning">Alleen lezen. Geen toestemming voor merge, restart of productie-deploy.</p>
<dl>
<dt>Exact geobserveerde main</dt><dd>""" + safe(snapshot["main_sha"]) + """</dd>
<dt>Productie-status</dt><dd>""" + safe(p["state"]) + """</dd>
<dt>Laatste deploy (huidige SHA, beperkte eerste pagina)</dt><dd>run """ + safe(d["run_id"]) + """ · poging """ + safe(d["attempt"]) + """ · """ + safe(d["status"]) + """ · """ + safe(d["conclusion"]) + """</dd>
<dt>VPS-runnerbewijs</dt><dd>""" + safe(d["runner_route"]) + """</dd></dl>
<h2>Ontbrekend of afwijkend bewijs</h2><ul>""" + (warnings or "<li>Geen lokale waarschuwingen; onafhankelijke releasepoort blijft verplicht.</li>") + """</ul>
<p>Geen live healthcheck van Firefox, SQLite, workers of VPS verricht. 
Groene GitHub-status is geen bewijs van actuele runtimegezondheid.</p>
<p>Bevoegd tot deploy: <strong>NEE</strong></p></article></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="deploy-ops-evidence.json")
    parser.add_argument("--html", default="deploy-ops-evidence.html")
    args = parser.parse_args(argv)
    try:
        data = collect(GitHubReadOnly(os.getenv("GITHUB_TOKEN")))
    except EvidenceError as exc:
        # No exception details, tokens, URLs, headers, or arbitrary responses.
        data = {
            "schema": "zcloud-deploy-ops-evidence-v1", "repository": REPO,
            "main_sha": "UNVERIFIED", "observed_utc": dt.datetime.now(
                dt.timezone.utc).isoformat(),
            "production": {"state": "unknown"},
            "deploy": {"run_id": None, "attempt": None, "status": "not_observed",
                       "conclusion": None, "runner_route": "UNVERIFIED"},
            "warnings": [str(exc)], "observation": "INCOMPLETE_EVIDENCE",
            "release_authorized": False, "deployment_authorized": False,
            "mutations_performed": False,
        }
        status = 2
    else:
        status = 0 if not data["warnings"] else 1
    Path(args.json).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    Path(args.html).write_text(render_html(data), encoding="utf-8")
    print(f"zCloud deploy-ops evidence: {data['main_sha']} "
          f"warnings={len(data['warnings'])} authorized=false")
    return status


if __name__ == "__main__":
    sys.exit(main())

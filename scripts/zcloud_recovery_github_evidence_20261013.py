#!/usr/bin/env python3
"""Read-only, fail-closed GitHub provenance adapter for offline retry-policy logic.

Fetches *only* canonical GitHub REST GET endpoints over TLS. No API dispatch,
deployment, token echo, SQLite, VPS, browser or worker changes. This observer
never grants automatic recovery authority or proves a production deployment.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Import sibling pure classifier without requiring installed Python packages.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from zcloud_recovery_retry_policy_20261013 import (
    REPO, SCHEMA, SHA, WORKFLOWS, classify
)

BASE = "https://api.github.com/repos/Zennay/zCloud"
REPOSITORY_ID = 1389227570
MAX_PAGES = 10
PAGE_SIZE = 100
MAX_BYTES = 2 * 1024 * 1024
VALID_STATES = {"pending", "success", "failure", "error"}


class SourceDenied(ValueError):
    """Ambiguous GitHub evidence. Never translate to authorization."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise SourceDenied(reason)


def distinct_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise SourceDenied("duplicate_json_key")
        obj[key] = value
    return obj


class DenyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceDenied("redirect_denied")


def github_get(path: str):
    """Canonical same-repository GET. Never accepts an external response URL."""
    require(path.startswith("/") and not path.startswith("//"), "invalid_api_path")
    url = BASE + path
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.netloc == "api.github.com"
            and parsed.path.startswith("/repos/Zennay/zCloud/"), "invalid_origin")
    request = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zcloud-recovery-observer-readonly-20261013",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), DenyRedirects()
    )
    try:
        with opener.open(request, timeout=12) as response:
            require(response.status == 200 and response.geturl() == url,
                    "unexpected_api_response")
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            require(mime in {"application/json", "application/vnd.github+json"},
                    "unexpected_content_type")
            raw = response.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "response_too_large")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Never put exception text in an output: proxy and URL errors may leak.
        raise SourceDenied("github_transport_unavailable") from None
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=distinct_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(SourceDenied("nonfinite_json")))
    except SourceDenied:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise SourceDenied("invalid_json") from None


def positive_int(value) -> bool:
    return type(value) is int and value > 0


def read_main_sha(get=github_get) -> str:
    response = get("/commits/main")
    require(isinstance(response, dict) and isinstance(response.get("sha"), str)
            and SHA.fullmatch(response["sha"]), "main_sha_missing")
    return response["sha"]


def status_snapshot(sha: str, get=github_get) -> dict:
    payload = get(f"/commits/{sha}/status")
    require(isinstance(payload, dict) and payload.get("sha") == sha,
            "production_status_wrong_sha")
    statuses = payload.get("statuses")
    count = payload.get("total_count")
    require(type(count) is int and 0 <= count <= 100
            and isinstance(statuses, list) and len(statuses) == count,
            "production_status_incomplete")
    safe = []
    for status in statuses:
        require(isinstance(status, dict) and positive_int(status.get("id"))
                and isinstance(status.get("context"), str)
                and status.get("state") in VALID_STATES,
                "malformed_production_status")
        safe.append({"context": status["context"], "state": status["state"]})
    return {"complete": True, "sha": sha, "statuses": safe}


def runs_snapshot(kind: str, sha: str, get=github_get) -> dict:
    workflow = WORKFLOWS[kind]
    safe = []
    seen = set()
    total = None
    pages = None
    for page in range(1, MAX_PAGES + 1):
        path = (f"/actions/workflows/{workflow}/runs?head_sha={sha}"
                f"&branch=main&per_page={PAGE_SIZE}&page={page}")
        payload = get(path)
        require(isinstance(payload, dict), "missing_workflow_page")
        count, rows = payload.get("total_count"), payload.get("workflow_runs")
        require(type(count) is int and 0 <= count <= MAX_PAGES * PAGE_SIZE
                and isinstance(rows, list) and len(rows) <= PAGE_SIZE,
                "unbounded_or_missing_workflow_page")
        if total is None:
            total = count
            pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        require(count == total, "workflow_total_changed")
        require(len(rows) == min(PAGE_SIZE, total - len(safe)), "workflow_page_truncated")
        for row in rows:
            require(isinstance(row, dict), "malformed_workflow_run")
            require(row.get("path") == f".github/workflows/{workflow}",
                    "workflow_path_mismatch")
            repository, head = row.get("repository"), row.get("head_repository")
            require(isinstance(repository, dict) and isinstance(head, dict)
                    and repository.get("id") == REPOSITORY_ID
                    and head.get("id") == REPOSITORY_ID
                    and repository.get("full_name") == REPO
                    and head.get("full_name") == REPO,
                    "workflow_repository_identity")
            run_id, attempt = row.get("id"), row.get("run_attempt")
            require(positive_int(run_id) and positive_int(attempt)
                    and run_id not in seen, "workflow_duplicate_run_id")
            seen.add(run_id)
            require(row.get("head_sha") == sha and row.get("head_branch") == "main",
                    "workflow_run_wrong_head")
            safe.append({k: row.get(k) for k in (
                "id", "run_attempt", "head_sha", "head_branch", "event",
                "status", "conclusion")})
        if page == pages:
            break
    require(total is not None and len(safe) == total, "workflow_pagination_incomplete")
    return {"complete": True, "has_next_page": False, "sha": sha,
            "workflow": workflow, "runs": safe}


def collect(get=github_get):
    start = read_main_sha(get)
    status = status_snapshot(start, get)
    snap = {"schema": SCHEMA, "repository": REPO, "main_sha": start,
            "production_status": status}
    # Observed production failure already prohibits redispatch. Do not require
    # costly/possibly paginated workflow-history reads merely to say NOOP.
    if not any(item["context"] == "zcloud/vps-production"
               for item in status["statuses"]):
        snap["deploy"] = runs_snapshot("deploy", start, get)
        snap["regression"] = runs_snapshot("regression", start, get)
    require(read_main_sha(get) == start, "main_changed_during_snapshot")
    return snap


def observe(get=github_get):
    try:
        decision = classify(collect(get))
        # Never emit prompt, token, URL, live SQLite path or raw GitHub payload.
        return {"schema": "zcloud-recovery-github-observation-v1",
                "decision": decision["decision"],
                "reason": decision["reason"],
                "sha": decision["sha"],
                "authorized": False, "mutations_performed": False}
    except (SourceDenied, TypeError, KeyError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, SourceDenied) else "malformed_github_evidence"
        return {"schema": "zcloud-recovery-github-observation-v1",
                "decision": "DENY", "reason": reason, "sha": None,
                "authorized": False, "mutations_performed": False}


if __name__ == "__main__":
    result = observe()
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    # "READY" means a candidate for independent serialized review, not permission.
    raise SystemExit(0 if result["decision"] in {"NOOP", "WAIT"} else 2)

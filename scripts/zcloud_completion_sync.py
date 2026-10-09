#!/usr/bin/env python3
"""Worker Completion Controller sync.

Pulls this repo's own open pull requests + their check runs from the GitHub
API (using the token the caller already has — typically the workflow's own
GITHUB_TOKEN, which is correctly scoped to this repo and nothing else) and
reports them to the local zCloud dashboard, which classifies and persists
them (see completion_controller.py). Optionally merges the subset the
dashboard reports back as auto-merge-eligible, re-verifying each one
immediately before merging so a stale classification can never merge a PR
that regressed in the meantime.

Deliberately stdlib-only (urllib), matching the rest of this repo's
GitHub-Actions-step scripts, and deliberately does not import
completion_controller for classification: the dashboard is the single
source of truth for what "safe to merge" means, so a script bug here can
under-report but can never invent a merge the dashboard did not approve.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zcloud-completion-sync/1",
    }


def _request(method: str, url: str, token: str, payload: dict | None = None, timeout: int = 20):
    data = None
    headers = _headers(token)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _paged_get(path: str, token: str, params: str = "") -> list:
    items = []
    page = 1
    while True:
        sep = "&" if params else ""
        url = f"{API}{path}?per_page=100&page={page}{sep}{params}"
        batch = _request("GET", url, token)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 20:  # 2000 PRs is already far beyond this portfolio's scale
            break
    return items


def fetch_open_pulls(repo: str, token: str) -> list:
    return _paged_get(f"/repos/{repo}/pulls", token, "state=open")


def fetch_check_runs(repo: str, sha: str, token: str) -> list:
    try:
        data = _request("GET", f"{API}/repos/{repo}/commits/{sha}/check-runs?per_page=100", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            return []
        raise
    return data.get("check_runs", []) if isinstance(data, dict) else []


def _post_local(dashboard_url: str, payload: dict) -> dict:
    # The dashboard is a localhost-only service with no token on this path
    # (see server.py action_request_allowed: 127.0.0.1 is always trusted),
    # so this intentionally sends no Authorization header.
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        dashboard_url.rstrip("/") + "/api/completion",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "zcloud-completion-sync/1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def sync(repo: str, project_id: str, token: str, dashboard_url: str) -> dict:
    pulls = fetch_open_pulls(repo, token)
    batch = []
    for pr in pulls:
        sha = (pr.get("head") or {}).get("sha") or ""
        checks = fetch_check_runs(repo, sha, token) if sha else []
        batch.append({"pr": pr, "checks": checks})
        time.sleep(0.05)  # stay comfortably under secondary rate limits
    result = _post_local(
        dashboard_url,
        {"action": "sync-pr-state", "project_id": project_id, "repo": repo, "pulls": batch, "authoritative": True},
    )
    return {"open_pulls": len(pulls), "sync_result": result}


def apply_auto_merges(repo: str, project_id: str, token: str, dashboard_url: str, merge_method: str) -> dict:
    status = _get_local(dashboard_url)
    candidates = (status.get("projects", {}).get(project_id, {}) or {}).get("mergeable_ready", [])
    merged, skipped = [], []
    for row in candidates:
        number = int(row["pr_number"])
        # Re-verify immediately before merging: never trust a classification
        # that may be minutes old for an action this irreversible.
        fresh = _request("GET", f"{API}/repos/{repo}/pulls/{number}", token)
        if fresh.get("draft") or fresh.get("mergeable_state") != "clean":
            skipped.append({"pr_number": number, "reason": f"no longer clean: mergeable_state={fresh.get('mergeable_state')} draft={fresh.get('draft')}"})
            continue
        checks = fetch_check_runs(repo, (fresh.get("head") or {}).get("sha") or "", token)
        if any(str(c.get("conclusion") or "").lower() not in ("success", "neutral", "skipped") for c in checks) or not checks:
            skipped.append({"pr_number": number, "reason": "checks are no longer all-green at merge time"})
            continue
        try:
            merge_result = _request(
                "PUT", f"{API}/repos/{repo}/pulls/{number}/merge", token,
                {"merge_method": merge_method, "sha": fresh.get("head", {}).get("sha")},
            )
            merged.append({"pr_number": number, "result": merge_result})
            _post_local(dashboard_url, {"action": "progress-event", "project_id": project_id, "kind": "merge", "detail": f"PR #{number} auto-merged by completion controller", "source_url": fresh.get("html_url", "")})
            _post_local(dashboard_url, {"action": "task-completed", "project_id": project_id})
        except urllib.error.HTTPError as exc:
            # Branch protection or a last-second review requirement rejected
            # it server-side — that is the safety net working as intended,
            # not a bug in this script.
            skipped.append({"pr_number": number, "reason": f"GitHub rejected merge: {exc.code} {exc.read()[:300]}"})
    return {"merged": merged, "skipped": skipped}


def _get_local(dashboard_url: str) -> dict:
    req = urllib.request.Request(dashboard_url.rstrip("/") + "/api/completion", headers={"User-Agent": "zcloud-completion-sync/1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name, e.g. Zennay/zCloud")
    parser.add_argument("--project-id", required=True, help="zCloud project id, e.g. cloud")
    parser.add_argument("--token", required=True)
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8765")
    parser.add_argument("--apply-merge", action="store_true", help="also merge dashboard-approved mergeable PRs")
    parser.add_argument("--merge-method", default="squash", choices=["squash", "merge", "rebase"])
    args = parser.parse_args(argv)

    report = sync(args.repo, args.project_id, args.token, args.dashboard_url)
    if args.apply_merge:
        report["auto_merge"] = apply_auto_merges(args.repo, args.project_id, args.token, args.dashboard_url, args.merge_method)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

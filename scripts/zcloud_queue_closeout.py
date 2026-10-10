"""Fail-closed, read-only GitHub proof for queue DONE.

A PR existing or a generated test plan is not a delivered change. A code task
can be DONE only when the specified PR was actually merged to main and its
exact merged SHA has completed successful GitHub checks. Uses fixed github.com
paths only; no arbitrary evidence URL is fetched.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

PR = re.compile(r"https://github\.com/(Zennay)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)\b", re.I)
SHA = re.compile(r"\b[0-9a-f]{40}\b", re.I)


def _github_json(path):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "zcloud-finish-first/1"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def verified_merged_delivery(evidence, fetcher=None):
    """Return (allowed, reason), never raise for untrusted external evidence.

    This checks GitHub's current state rather than accepting claims from the
    worker. It does not perform a deploy and does not prove user acceptance.
    """
    raw = str(evidence or "").strip()
    if not raw or len(raw) > 4000:
        return False, "Missing or oversized closeout evidence"
    matches = list(PR.finditer(raw))
    if len(matches) != 1:
        return False, "DONE requires exactly one Zennay GitHub pull-request URL"
    owner, repo, number = matches[0].groups()
    repo = repo.strip()
    github = fetcher or _github_json
    try:
        pr = github("/repos/Zennay/" + repo + "/pulls/" + number)
        if not isinstance(pr, dict) or not pr.get("merged_at"):
            return False, "Referenced PR is not merged"
        if (pr.get("base") or {}).get("ref") != "main":
            return False, "Referenced PR was not merged to main"
        sha = str(pr.get("merge_commit_sha") or "")
        if not SHA.fullmatch(sha):
            return False, "Merged PR has no valid merge commit SHA"
        checks = github("/repos/Zennay/" + repo + "/commits/" + sha + "/check-runs?per_page=100")
        jobs = checks.get("check_runs") if isinstance(checks, dict) else None
        if not isinstance(jobs, list) or not jobs:
            return False, "No exact-merge-SHA CI checks found"
        if any(not isinstance(job, dict) or job.get("status") != "completed"
               or job.get("conclusion") not in ("success", "skipped", "neutral") for job in jobs):
            return False, "Exact merge commit has unfinished or failing checks"
        if not any(job.get("conclusion") == "success" for job in jobs):
            return False, "No successful exact-merge-SHA check"
        return True, "Merged PR and exact-merge-SHA checks verified"
    except (urllib.error.URLError, OSError, ValueError, TypeError, KeyError, TimeoutError):
        return False, "GitHub closeout proof unavailable; retain runnable queue state"

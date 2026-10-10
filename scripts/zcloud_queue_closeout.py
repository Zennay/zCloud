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
from datetime import datetime, timezone

PR = re.compile(r"https://github\.com/(Zennay)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)\b", re.I)
RUN = re.compile(r"https://github\.com/(Zennay)/([A-Za-z0-9_.-]+)/actions/runs/([1-9][0-9]*)\b", re.I)


def proof_reference(evidence):
    """One canonical repo-bound GitHub delivery artifact, or empty."""
    matches=list(PR.finditer(str(evidence or '')))+list(RUN.finditer(str(evidence or '')))
    return matches[0].group(0).lower() if len(matches)==1 else ''

SHA = re.compile(r"\b[0-9a-f]{40}\b", re.I)


def _github_json(path):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "zcloud-finish-first/1"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def verified_merged_delivery(evidence, fetcher=None, *, expected_repo=None, claimed_after=None):
    """Return (allowed, reason), never raise for untrusted external evidence.

    This checks GitHub's current state rather than accepting claims from the
    worker. It does not perform a deploy and does not prove user acceptance.
    """
    submitted = str(evidence or "")
    raw = submitted.strip()
    if not raw or len(submitted) > 4000:
        return False, "Missing or oversized closeout evidence"
    prs = list(PR.finditer(raw))
    runs = list(RUN.finditer(raw))
    if len(prs)+len(runs) != 1:
        return False, "DONE requires one project GitHub merged PR or successful Actions run with an artifact"
    owner, repo, number = (prs or runs)[0].groups()
    repo = repo.strip()
    if expected_repo and repo.casefold() != str(expected_repo).casefold():
        return False, "Closeout proof does not belong to this queue project's repository"
    github = fetcher or _github_json
    try:
        if runs:
            run = github("/repos/Zennay/" + repo + "/actions/runs/" + number)
            if not isinstance(run, dict) or run.get("status")!="completed" or run.get("conclusion")!="success":
                return False, "Actions run has no terminal success"
            if run.get("head_branch")!="main" or run.get("event") not in ("workflow_dispatch","schedule"):
                return False, "Actions run is not a trusted main-branch execution"
            if claimed_after:
                run_time = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
                claimed_time = datetime.fromisoformat(str(claimed_after).replace("Z", "+00:00")).astimezone(timezone.utc)
                if run_time < claimed_time:
                    return False, "Actions run predates this work assignment"
            artifacts = github("/repos/Zennay/" + repo + "/actions/runs/" + number + "/artifacts?per_page=100")
            assets = artifacts.get("artifacts") if isinstance(artifacts,dict) else None
            if not isinstance(assets,list) or not assets:
                return False, "Successful run has no durable result artifact"
            total = artifacts.get("total_count")
            if isinstance(total,int) and total>len(assets):
                return False, "Actions artifact list incomplete"
            if not any(isinstance(a,dict) and a.get("expired") is False
                       and isinstance(a.get("size_in_bytes"),int) and a["size_in_bytes"]>0 for a in assets):
                return False, "No nonempty unexpired run artifact"
            return True, "Successful post-claim main run and nonempty artifact verified"
        pr = github("/repos/Zennay/" + repo + "/pulls/" + number)
        if not isinstance(pr, dict) or not pr.get("merged_at"):
            return False, "Referenced PR is not merged"
        if claimed_after:
            merged_time = datetime.fromisoformat(str(pr["merged_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
            claimed_time = datetime.fromisoformat(str(claimed_after).replace("Z", "+00:00")).astimezone(timezone.utc)
            if merged_time < claimed_time:
                return False, "PR was merged before this work assignment began"
        if (pr.get("base") or {}).get("ref") != "main":
            return False, "Referenced PR was not merged to main"
        sha = str(pr.get("merge_commit_sha") or "")
        if not SHA.fullmatch(sha):
            return False, "Merged PR has no valid merge commit SHA"
        checks = github("/repos/Zennay/" + repo + "/commits/" + sha + "/check-runs?per_page=100")
        jobs = checks.get("check_runs") if isinstance(checks, dict) else None
        if not isinstance(jobs, list) or not jobs:
            return False, "No exact-merge-SHA CI checks found"
        reported=checks.get("total_count")
        if isinstance(reported, int) and reported > len(jobs):
            return False, "CI check list is incomplete; cannot declare terminal green"
        if any(not isinstance(job, dict) or job.get("status") != "completed"
               or job.get("conclusion") not in ("success", "skipped", "neutral") for job in jobs):
            return False, "Exact merge commit has unfinished or failing checks"
        if not any(job.get("conclusion") == "success" for job in jobs):
            return False, "No successful exact-merge-SHA check"
        return True, "Merged PR and exact-merge-SHA checks verified"
    except (urllib.error.URLError, OSError, ValueError, TypeError, KeyError, TimeoutError):
        return False, "GitHub closeout proof unavailable; retain runnable queue state"

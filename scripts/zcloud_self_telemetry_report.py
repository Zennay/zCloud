#!/usr/bin/env python3
"""Build a bounded, read-only zCloud self-telemetry report.

This report gives the control-plane an evidence-backed view of its own
scorecard and exact GitHub main state without touching runtime state.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = "zcloud-control-plane-telemetry-v1"
DEFAULT_REPO = "Zennay/zCloud"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_MILESTONES = 64
PR_PAGE_SIZE = 100
MAX_PR_PAGES = 5
MAX_PR_NUMBERS_EMITTED = 64
MAX_TEXT = 320
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CI = {"success", "failure", "in_progress", "not_configured", "unknown"}


def _bounded_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("projects path must not be a symlink")
    if not path.is_file():
        raise ValueError("projects file is missing")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("projects file exceeds bounded size")


def _text(value: object, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise ValueError(f"{label} must be text")
        return ""
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label} must be non-empty")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{label} exceeds bounded length")
    return value


def _progress(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("milestone progress must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0 or value > 100:
        raise ValueError("milestone progress must be between 0 and 100")
    return value


def _load_cloud_project(path: Path) -> dict:
    _bounded_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("projects file is not valid UTF-8 JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("projects catalog must be a list")
    matches = [row for row in payload if isinstance(row, dict) and row.get("id") == "cloud"]
    if len(matches) != 1:
        raise ValueError("projects catalog must contain exactly one cloud project")
    project = matches[0]
    if str(project.get("status") or "").strip().lower() == "archived":
        raise ValueError("cloud project is archived")
    if not PROJECT_RE.fullmatch(str(project.get("id") or "")):
        raise ValueError("cloud project id is not canonical")
    return project


def _scorecard(project: dict) -> dict:
    rows = project.get("milestones")
    if not isinstance(rows, list) or not rows:
        raise ValueError("cloud milestones must be a non-empty list")
    if len(rows) > MAX_MILESTONES:
        raise ValueError("cloud milestone count exceeds bound")

    milestones = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("cloud milestones must be objects")
        title = _text(row.get("title"), "milestone title", required=True)
        progress = _progress(row.get("progress"))
        done = row.get("done")
        if type(done) is not bool:
            raise ValueError("milestone done must be boolean")
        if done and progress != 100:
            raise ValueError("completed milestone must have 100 progress")
        milestones.append({"title": title, "progress": progress, "done": done})

    overall = project.get("progress_override")
    overall_value = _progress(overall) if overall is not None else round(
        sum(row["progress"] for row in milestones) / len(milestones), 1
    )
    return {
        "revision": _text(project.get("milestone_revision"), "milestone revision", required=True),
        "progress_basis": _text(project.get("progress_basis"), "progress basis", required=True),
        "overall_progress": overall_value,
        "milestones": milestones,
    }


def _github_get(url: str, token: str | None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zcloud-self-telemetry/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub read returned HTTP {response.status}")
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError("GitHub response exceeds bounded size")
    return json.loads(raw.decode("utf-8"))


def _repo_url(repo: str, suffix: str = "") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub repository name is invalid")
    return f"https://api.github.com/repos/{repo}{suffix}"


def _open_pr_inventory(repo: str, token: str | None, getter: Callable[[str, str | None], object]) -> dict:
    numbers = []
    for page in range(1, MAX_PR_PAGES + 1):
        payload = getter(
            _repo_url(repo, "/pulls?" + urllib.parse.urlencode({
                "state": "open",
                "base": "main",
                "per_page": str(PR_PAGE_SIZE),
                "page": str(page),
            })),
            token,
        )
        if not isinstance(payload, list):
            raise RuntimeError("GitHub pull request response is invalid")
        if len(payload) > PR_PAGE_SIZE:
            raise RuntimeError("GitHub pull request page exceeds bound")
        for row in payload:
            number = row.get("number") if isinstance(row, dict) else None
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise RuntimeError("GitHub pull request row is invalid")
            numbers.append(number)
        if len(payload) < PR_PAGE_SIZE:
            break
    else:
        raise RuntimeError("open pull request inventory exceeds bounded page limit")

    if len(set(numbers)) != len(numbers):
        raise RuntimeError("GitHub pull request inventory contains duplicate numbers")
    emitted = sorted(numbers, reverse=True)[:MAX_PR_NUMBERS_EMITTED]
    return {
        "open_pr_count": len(numbers),
        "open_pr_numbers": emitted,
        "open_pr_numbers_truncated": len(numbers) > len(emitted),
        "open_pr_number_limit": MAX_PR_NUMBERS_EMITTED,
    }


def _classify_ci(runs: list[dict], status_payload: dict) -> dict:
    exact = [row for row in runs if isinstance(row, dict)]
    contexts = status_payload.get("statuses") if isinstance(status_payload, dict) else None
    contexts = contexts if isinstance(contexts, list) else []

    if exact:
        statuses = {str(row.get("status") or "").lower() for row in exact}
        conclusions = {
            str(row.get("conclusion") or "").lower()
            for row in exact
            if str(row.get("status") or "").lower() == "completed"
        }
        if statuses & {"queued", "in_progress", "requested", "waiting", "pending"}:
            state = "in_progress"
        elif conclusions & {"failure", "timed_out", "action_required", "startup_failure", "cancelled", "stale"}:
            state = "failure"
        elif "success" in conclusions and conclusions <= {"success", "neutral", "skipped"}:
            state = "success"
        else:
            state = "unknown"
        return {
            "status": state,
            "source": "github_actions_exact_head",
            "run_count": len(exact),
            "status_context_count": len(contexts),
        }

    if contexts:
        state = str(status_payload.get("state") or "").strip().lower()
        mapped = {
            "success": "success",
            "failure": "failure",
            "error": "failure",
            "pending": "in_progress",
        }.get(state, "unknown")
        return {
            "status": mapped,
            "source": "github_commit_status_exact_head",
            "run_count": 0,
            "status_context_count": len(contexts),
        }

    return {
        "status": "not_configured",
        "source": "github_exact_head_no_ci_evidence",
        "run_count": 0,
        "status_context_count": 0,
    }


def build_report(
    projects_path: Path,
    *,
    repo: str = DEFAULT_REPO,
    token: str | None = None,
    now: datetime | None = None,
    getter: Callable[[str, str | None], object] = _github_get,
) -> dict:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("reference time must be timezone-aware")
    observed = observed.astimezone(timezone.utc)

    project = _load_cloud_project(projects_path)
    scorecard = _scorecard(project)

    commit_payload = getter(_repo_url(repo, "/commits/main"), token)
    if not isinstance(commit_payload, dict):
        raise RuntimeError("GitHub main commit response is invalid")
    main_sha = str(commit_payload.get("sha") or "").lower()
    if not SHA_RE.fullmatch(main_sha):
        raise RuntimeError("GitHub main commit SHA is invalid")

    pull_inventory = _open_pr_inventory(repo, token, getter)

    runs_payload = getter(
        _repo_url(repo, "/actions/runs?" + urllib.parse.urlencode({
            "branch": "main", "head_sha": main_sha, "per_page": "100"
        })),
        token,
    )
    if not isinstance(runs_payload, dict) or not isinstance(runs_payload.get("workflow_runs"), list):
        raise RuntimeError("GitHub workflow-runs response is invalid")
    workflow_runs = [
        row for row in runs_payload["workflow_runs"]
        if isinstance(row, dict) and str(row.get("head_sha") or "").lower() == main_sha
    ]

    status_payload = getter(_repo_url(repo, f"/commits/{main_sha}/status"), token)
    if not isinstance(status_payload, dict):
        raise RuntimeError("GitHub commit-status response is invalid")
    ci = _classify_ci(workflow_runs, status_payload)
    if ci["status"] not in ALLOWED_CI:
        raise RuntimeError("CI classifier returned an invalid state")

    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed.isoformat(),
        "project_id": "cloud",
        "phase": _text(project.get("phase"), "project phase", required=True),
        "next_step": _text(project.get("next_step"), "project next_step", required=True),
        "scorecard": scorecard,
        "github": {
            "repository": repo,
            "main_sha": main_sha,
            **pull_inventory,
            "ci": ci,
        },
        "evidence_contract": {
            "scorecard_source": "zcloud_projects_registry",
            "github_source": "github_read_only_exact_main",
            "ci_missing_is_never_green": True,
            "runtime_state_is_not_mutated": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--projects", type=Path, default=root / "projects.json")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    token = os.environ.get(args.token_env) or None
    payload = build_report(args.projects, repo=args.repo, token=token)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

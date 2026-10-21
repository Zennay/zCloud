#!/usr/bin/env python3
"""Read-only freshness audit for active worker branches against canonical main."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Callable
from urllib.parse import quote


STATUSES = ("current", "stale", "missing", "untracked", "unknown")
_REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class GitHubReadError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object, *, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} timestamp missing")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_regular_file(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} path must be a regular file")


def _project_repos(projects_path: Path) -> dict[str, str | None]:
    _require_regular_file(projects_path, label="projects")
    try:
        payload = json.loads(projects_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("projects JSON is unreadable") from exc
    if not isinstance(payload, list):
        raise ValueError("projects JSON must be a list")

    repos: dict[str, str | None] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("project entries must be objects")
        project_id = str(row.get("id") or "").strip()
        if not project_id:
            raise ValueError("project id must be non-empty")
        if project_id in repos:
            raise ValueError(f"duplicate project id {project_id!r}")
        repo_url = str(row.get("repo_url") or "").strip()
        match = _REPO_RE.fullmatch(repo_url) if repo_url else None
        repos[project_id] = match.group(1) if match else None
    return repos


def _readonly_connect(db_path: Path) -> sqlite3.Connection:
    _require_regular_file(db_path, label="history database")
    uri = "file:" + quote(str(db_path.resolve()), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def _active_targets(db_path: Path, projects: dict[str, str | None], observed_at: datetime) -> list[dict]:
    try:
        with _readonly_connect(db_path) as connection:
            rows = connection.execute(
                """SELECT project_id, worker_id, lease_until, metadata_json
                   FROM task_claims
                   ORDER BY project_id, worker_id, claim_key"""
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError("unable to read active task claims") from exc

    grouped: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    malformed: Counter[str] = Counter()
    for row in rows:
        project_id = str(row["project_id"] or "").strip()
        if not project_id:
            continue
        try:
            lease_until = _parse_time(row["lease_until"], label=f"{project_id} lease")
        except ValueError:
            malformed[project_id] += 1
            continue
        if lease_until <= observed_at:
            continue

        worker_id = str(row["worker_id"] or "").strip()
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            malformed[project_id] += 1
            continue
        if not isinstance(metadata, dict):
            malformed[project_id] += 1
            continue
        raw_branch = metadata.get("branch")
        branch = str(raw_branch).strip() if isinstance(raw_branch, str) else ""
        grouped[(project_id, branch or None)].add(worker_id)

    targets: list[dict] = []
    for (project_id, branch), workers in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        targets.append(
            {
                "project_id": project_id,
                "repo": projects.get(project_id),
                "branch": branch,
                "worker_count": len([worker for worker in workers if worker]) or len(workers),
            }
        )
    for project_id, count in sorted(malformed.items()):
        targets.append(
            {
                "project_id": project_id,
                "repo": projects.get(project_id),
                "branch": None,
                "worker_count": count,
                "malformed_claim_metadata": True,
            }
        )
    return targets


def _gh_json(path: str, *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
    completed = runner(
        ["gh", "api", path],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        stderr = str(completed.stderr or "")
        code = "not_found" if "HTTP 404" in stderr or "404 Not Found" in stderr else "unavailable"
        raise GitHubReadError(code)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubReadError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise GitHubReadError("invalid_payload")
    return payload


def github_compare(
    repo: str,
    branch: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    main = _gh_json(f"repos/{repo}/branches/main", runner=runner)
    main_sha = str(((main.get("commit") or {}).get("sha")) or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", main_sha):
        raise GitHubReadError("invalid_main")

    if branch == "main":
        return {"status": "current", "ahead_by": 0, "behind_by": 0}

    encoded_branch = quote(branch, safe="")
    try:
        remote_branch = _gh_json(f"repos/{repo}/branches/{encoded_branch}", runner=runner)
    except GitHubReadError as exc:
        if exc.code == "not_found":
            return {"status": "missing", "ahead_by": None, "behind_by": None}
        raise

    branch_sha = str(((remote_branch.get("commit") or {}).get("sha")) or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", branch_sha):
        raise GitHubReadError("invalid_branch")

    comparison = _gh_json(f"repos/{repo}/compare/{main_sha}...{branch_sha}", runner=runner)
    try:
        ahead_by = max(0, int(comparison.get("ahead_by")))
        behind_by = max(0, int(comparison.get("behind_by")))
    except (TypeError, ValueError) as exc:
        raise GitHubReadError("invalid_compare") from exc
    return {
        "status": "stale" if behind_by > 0 else "current",
        "ahead_by": ahead_by,
        "behind_by": behind_by,
    }


def build_report(
    db_path: Path,
    projects_path: Path,
    *,
    now: datetime | None = None,
    compare_reader: Callable[[str, str], dict] = github_compare,
) -> dict:
    observed_at = (now or _now()).astimezone(timezone.utc)
    projects = _project_repos(projects_path)
    targets = _active_targets(db_path, projects, observed_at)

    results: list[dict] = []
    for target in targets:
        project_id = target["project_id"]
        repo = target.get("repo")
        branch = target.get("branch")
        worker_count = int(target.get("worker_count") or 0)

        if target.get("malformed_claim_metadata"):
            status = "unknown"
            reason = "malformed_claim_metadata"
            ahead_by = behind_by = None
        elif not branch:
            status = "untracked"
            reason = "branch_not_declared"
            ahead_by = behind_by = None
        elif not repo:
            status = "unknown"
            reason = "canonical_repo_unavailable"
            ahead_by = behind_by = None
        else:
            try:
                comparison = compare_reader(repo, branch)
                status = str(comparison.get("status") or "unknown")
                if status not in {"current", "stale", "missing"}:
                    status = "unknown"
                ahead_by = comparison.get("ahead_by")
                behind_by = comparison.get("behind_by")
                reason = None if status in {"current", "stale", "missing"} else "invalid_compare_result"
            except GitHubReadError as exc:
                status = "unknown"
                reason = f"github_{exc.code}"
                ahead_by = behind_by = None
            except Exception:
                status = "unknown"
                reason = "github_unavailable"
                ahead_by = behind_by = None

        results.append(
            {
                "project_id": project_id,
                "repo": repo,
                "branch": branch,
                "worker_count": worker_count,
                "status": status,
                "warning": status in {"stale", "missing", "unknown"},
                "ahead_by": ahead_by,
                "behind_by": behind_by,
                "reason": reason,
            }
        )

    counts = {status: sum(1 for item in results if item["status"] == status) for status in STATUSES}
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "counts": counts,
        "warning_count": sum(1 for item in results if item["warning"]),
        "branches": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--now", help="Timezone-aware ISO timestamp for deterministic validation.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    observed_at = _parse_time(args.now, label="--now") if args.now else None
    payload = build_report(args.db, args.projects, now=observed_at)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

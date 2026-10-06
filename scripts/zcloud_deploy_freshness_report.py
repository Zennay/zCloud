#!/usr/bin/env python3
"""Emit bounded, read-only zCloud deployment freshness evidence.

The report combines the durable last-known-good recovery manifest with an
explicitly supplied green regression SHA and the current repository revision.
It never reads or mutates live SQLite, services, queues, browser state, or
secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_STATE_FILE_BYTES = 1024 * 1024
MAX_CHANGED_PATHS = 128

RUNTIME_PATHS = (
    ".github/workflows/zcloud-vps-deploy.yml",
    ".github/workflows/zcloud-regression-smoke.yml",
    "server.py",
    "enhancements.py",
    "project_runtime.py",
    "lane_generator.py",
    "project-contracts.json",
    "autonomy-policy.json",
    "vps-execution-policy.json",
    "portfolio_queue.seed.json",
    "firefox-extension/",
    "public/",
    "deploy/",
    "scripts/",
)


class DeployFreshnessError(RuntimeError):
    pass


def _regular_json(path: Path) -> dict:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise DeployFreshnessError(f"state file unavailable: {path.name}") from exc
    if path.is_symlink() or not path.is_file():
        raise DeployFreshnessError(
            f"state file must be a regular non-symlink: {path.name}"
        )
    if stat.st_size > MAX_STATE_FILE_BYTES:
        raise DeployFreshnessError(f"state file too large: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployFreshnessError(f"invalid JSON state file: {path.name}") from exc
    if not isinstance(value, dict):
        raise DeployFreshnessError(f"state JSON must be an object: {path.name}")
    return value


def _sha(value: object, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(normalized):
        raise DeployFreshnessError(
            f"{label} must be a full lowercase 40-character Git SHA"
        )
    return normalized


def _timestamp(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 80:
        raise DeployFreshnessError(f"{label} must be a bounded ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeployFreshnessError(f"{label} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DeployFreshnessError(f"{label} must include timezone information")
    return normalized


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployFreshnessError("Git inspection failed") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = detail[-1][:180] if detail else f"rc={result.returncode}"
        raise DeployFreshnessError(f"Git inspection failed: {suffix}")
    return result


def _git_head(repo: Path) -> str:
    return _sha(_git(repo, "rev-parse", "HEAD").stdout.strip(), "current SHA")


def _ensure_commit(repo: Path, sha: str, label: str) -> None:
    result = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False)
    if result.returncode:
        raise DeployFreshnessError(
            f"{label} is not available as a commit in this repository"
        )


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    result = _git(repo, "merge-base", "--is-ancestor", older, newer, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise DeployFreshnessError("unable to determine Git ancestry")


def _runtime_path(path: str) -> bool:
    for configured in RUNTIME_PATHS:
        if configured.endswith("/"):
            if path.startswith(configured):
                return True
        elif path == configured:
            return True
    return False


def _changed_runtime_paths(repo: Path, base: str, head: str) -> list[str]:
    result = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACDMRTUXB",
        base,
        head,
        "--",
    )
    paths: list[str] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value or not _runtime_path(value):
            continue
        if any(ord(char) < 32 for char in value):
            raise DeployFreshnessError("changed path contains control characters")
        paths.append(value)
        if len(paths) > MAX_CHANGED_PATHS:
            raise DeployFreshnessError(
                "too many runtime paths changed for bounded evidence"
            )
    return paths


def load_lkg(state: Path) -> dict:
    state = state.expanduser()
    pointer = _regular_json(state / "last-known-good.json")
    snapshot_id = str(pointer.get("snapshot_id") or "")
    if not SNAPSHOT_RE.fullmatch(snapshot_id):
        raise DeployFreshnessError("invalid last-known-good snapshot id")

    manifest_path = state / "snapshots" / snapshot_id / "manifest.json"
    manifest = _regular_json(manifest_path)
    if str(manifest.get("snapshot_id") or "") != snapshot_id:
        raise DeployFreshnessError("last-known-good manifest snapshot mismatch")
    try:
        format_version = int(manifest.get("format_version") or 0)
    except (TypeError, ValueError) as exc:
        raise DeployFreshnessError("invalid last-known-good manifest format") from exc
    if format_version != 1:
        raise DeployFreshnessError("unsupported last-known-good manifest format")

    git = manifest.get("git")
    if not isinstance(git, dict):
        raise DeployFreshnessError("last-known-good manifest lacks Git evidence")
    deployed_sha = _sha(git.get("head"), "deployed SHA")

    evidence = str(manifest.get("evidence") or "")
    if "POSTDEPLOY_GREEN" not in evidence:
        raise DeployFreshnessError(
            "last-known-good manifest lacks POSTDEPLOY_GREEN evidence"
        )

    updated_at = _timestamp(
        pointer.get("updated_at"),
        "last-known-good updated_at",
    )
    return {
        "snapshot_id": snapshot_id,
        "updated_at": updated_at,
        "deployed_sha": deployed_sha,
    }


def _ahead_count(repo: Path, older: str, newer: str) -> int | None:
    if not _is_ancestor(repo, older, newer):
        return None
    raw = _git(repo, "rev-list", "--count", f"{older}..{newer}").stdout.strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise DeployFreshnessError("invalid Git revision count") from exc
    return max(0, value)


def build_report(
    *,
    repo: Path,
    state: Path,
    green_sha: str,
    current_sha: str | None = None,
) -> dict:
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise DeployFreshnessError("repository path is not a Git worktree")

    current = _sha(current_sha, "current SHA") if current_sha else _git_head(repo)
    green = _sha(green_sha, "green regression SHA")
    lkg = load_lkg(state)

    for sha, label in (
        (current, "current SHA"),
        (green, "green regression SHA"),
        (lkg["deployed_sha"], "deployed SHA"),
    ):
        _ensure_commit(repo, sha, label)

    runtime_changes: list[str] = []
    if current == green:
        validation_status = "exact_green"
    elif _is_ancestor(repo, green, current):
        runtime_changes = _changed_runtime_paths(repo, green, current)
        validation_status = (
            "runtime_changes_unvalidated"
            if runtime_changes
            else "runtime_equivalent"
        )
    elif _is_ancestor(repo, current, green):
        validation_status = "current_behind_green"
    else:
        validation_status = "diverged_from_green"

    deployed_to_current = _ahead_count(repo, lkg["deployed_sha"], current)
    current_matches_deploy = current == lkg["deployed_sha"]
    deployed_is_ancestor = deployed_to_current is not None

    return {
        "schema_version": 1,
        "last_green_deploy": {
            "snapshot_id": lkg["snapshot_id"],
            "commit_sha": lkg["deployed_sha"],
            "updated_at": lkg["updated_at"],
            "postdeploy_green": True,
        },
        "source": {
            "current_sha": current,
            "matches_deployed_sha": current_matches_deploy,
            "deployed_is_ancestor": deployed_is_ancestor,
            "commits_ahead_of_deploy": deployed_to_current,
        },
        "validation": {
            "green_sha": green,
            "status": validation_status,
            "needs_validation": validation_status
            not in {"exact_green", "runtime_equivalent"},
            "runtime_changed_count": len(runtime_changes),
            "runtime_changed_paths": runtime_changes,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument(
        "--state",
        default=str(Path.home() / ".local/state/zcloud/recovery"),
    )
    parser.add_argument("--green-sha", required=True)
    parser.add_argument("--current-sha")
    parser.add_argument("--require-validated", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(
            repo=Path(args.repo),
            state=Path(args.state),
            green_sha=args.green_sha,
            current_sha=args.current_sha,
        )
    except DeployFreshnessError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "invalid",
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"ZCLOUD_DEPLOY_FRESHNESS_INVALID: {exc}")
        return 2

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        deployment = report["last_green_deploy"]
        validation = report["validation"]
        print(
            "ZCLOUD_DEPLOY_FRESHNESS "
            f"deployed={deployment['commit_sha']} "
            f"current={report['source']['current_sha']} "
            f"green={validation['green_sha']} "
            f"status={validation['status']} "
            f"runtime_changes={validation['runtime_changed_count']}"
        )

    if args.require_validated and report["validation"]["needs_validation"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

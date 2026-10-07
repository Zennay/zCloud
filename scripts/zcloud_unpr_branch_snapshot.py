#!/usr/bin/env python3
"""Build a bounded, fail-closed snapshot of origin branches without an open PR."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from scripts.zcloud_unpr_branch_overlap_audit import (
        MAX_BRANCHES,
        MAX_FILES_PER_BRANCH,
        _safe_branch,
        _safe_repo_path,
    )
except ModuleNotFoundError:
    from zcloud_unpr_branch_overlap_audit import (
        MAX_BRANCHES,
        MAX_FILES_PER_BRANCH,
        _safe_branch,
        _safe_repo_path,
    )

MAX_OPEN_PRS = 500
MAX_OPEN_PRS_BYTES = 512 * 1024
GIT_TIMEOUT_SECONDS = 30


class SnapshotError(ValueError):
    pass


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError(f"git_failed:{args[0]}:{exc.__class__.__name__}") from exc
    if proc.returncode != 0:
        raise SnapshotError(f"git_failed:{args[0]}:exit_{proc.returncode}")
    return proc.stdout


def _load_open_pr_heads(path: Path) -> set[str]:
    try:
        st = path.lstat()
    except OSError as exc:
        raise SnapshotError(f"open_pr_snapshot_unreadable:{exc.__class__.__name__}") from exc
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise SnapshotError("open_pr_snapshot_not_regular")
    if st.st_size > MAX_OPEN_PRS_BYTES:
        raise SnapshotError("open_pr_snapshot_too_large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"open_pr_snapshot_invalid:{exc.__class__.__name__}") from exc
    if not isinstance(payload, list) or len(payload) > MAX_OPEN_PRS:
        raise SnapshotError("open_pr_snapshot_incomplete_or_oversized")

    heads: set[str] = set()
    for row in payload:
        if not isinstance(row, dict) or set(row) != {"headRefName", "isCrossRepository"}:
            raise SnapshotError("open_pr_row_invalid")
        cross_repo = row["isCrossRepository"]
        if type(cross_repo) is not bool:
            raise SnapshotError("open_pr_cross_repo_invalid")
        head = _safe_branch(row["headRefName"])
        if not cross_repo:
            heads.add(head)
    return heads


def _origin_branches(repo_root: Path, remote: str) -> list[str]:
    if remote != "origin":
        raise SnapshotError("remote_invalid")
    base_ref = f"refs/remotes/{remote}/main"
    base_sha = _run_git(repo_root, "rev-parse", "--verify", base_ref).strip()
    if len(base_sha) != 40 or any(ch not in "0123456789abcdef" for ch in base_sha.lower()):
        raise SnapshotError("base_branch_missing")
    raw = _run_git(
        repo_root,
        "for-each-ref",
        f"--no-merged={base_ref}",
        "--format=%(refname:strip=3)",
        f"refs/remotes/{remote}",
    )
    branches: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        if not line or line == "HEAD" or line == "main":
            continue
        name = _safe_branch(line)
        if name in seen:
            raise SnapshotError("remote_branch_duplicate")
        seen.add(name)
        branches.append(name)
    branches.sort()
    if len(branches) > MAX_BRANCHES:
        raise SnapshotError("remote_branch_inventory_too_large")
    return branches


def _merge_base(repo_root: Path, base_ref: str, branch_ref: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "merge-base", base_ref, branch_ref],
            cwd=repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError(f"git_failed:merge-base:{exc.__class__.__name__}") from exc
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise SnapshotError(f"git_failed:merge-base:exit_{proc.returncode}")
    value = proc.stdout.strip()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise SnapshotError("merge_base_invalid")
    return value


def _branch_changed_files(repo_root: Path, remote: str, base: str, branch: str) -> list[str]:
    base_ref = f"refs/remotes/{remote}/{base}"
    branch_ref = f"refs/remotes/{remote}/{branch}"
    merge_base = _merge_base(repo_root, base_ref, branch_ref)
    if merge_base is None:
        raw = _run_git(
            repo_root,
            "ls-tree",
            "-r",
            "--name-only",
            branch_ref,
        )
    else:
        raw = _run_git(
            repo_root,
            "diff",
            "--name-only",
            "--no-renames",
            merge_base,
            branch_ref,
            "--",
        )
    paths: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        path = _safe_repo_path(line)
        if path in seen:
            raise SnapshotError("changed_file_duplicate")
        seen.add(path)
        paths.append(path)
    paths.sort()
    if len(paths) > MAX_FILES_PER_BRANCH:
        raise SnapshotError("changed_files_too_many")
    return paths


def build_snapshot(
    repo_root: Path,
    open_pr_heads: set[str],
    *,
    remote: str = "origin",
    base: str = "main",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if base != "main":
        raise SnapshotError("base_branch_invalid")
    branches = _origin_branches(repo_root, remote)
    rows: list[dict[str, Any]] = []
    for branch in branches:
        if branch == base or branch in open_pr_heads:
            continue
        rows.append(
            {
                "name": branch,
                "has_open_pr": False,
                "changed_files_complete": True,
                "changed_files": _branch_changed_files(repo_root, remote, base, branch),
            }
        )
    captured_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return {
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "inventory_complete": True,
        "base_branch": base,
        "branches": rows,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        try:
            st = path.lstat()
        except OSError as exc:
            raise SnapshotError(f"output_unreadable:{exc.__class__.__name__}") from exc
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise SnapshotError("output_not_regular")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--open-prs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        heads = _load_open_pr_heads(args.open_prs)
        snapshot = build_snapshot(args.repo_root, heads)
        _write_json(args.output, snapshot)
    except (SnapshotError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema": "zcloud-unpr-branch-snapshot-v1",
                    "status": "incomplete",
                    "reason": str(exc),
                    "mutation_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "schema": "zcloud-unpr-branch-snapshot-v1",
                "status": "complete",
                "branch_count": len(snapshot["branches"]),
                "mutation_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

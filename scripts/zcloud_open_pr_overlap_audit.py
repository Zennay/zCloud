#!/usr/bin/env python3
"""Detect overlapping file ownership across open pull requests.

Input is the JSON emitted by:
  gh pr list --state open --limit 500 --json number,headRefName,isDraft,updatedAt,files

The report intentionally excludes PR titles, bodies, authors, labels, comments and
other free text. It is safe to use as a coordination primitive before claiming a
zCloud work slice.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "open-pr-overlap-audit-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_PRS = 500
MAX_FILES_PER_PR = 500
MAX_PATH_LENGTH = 512
MAX_HEAD_REF_LENGTH = 160


class AuditError(ValueError):
    pass


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_ref: str
    draft: bool
    updated_at: str
    files: tuple[str, ...]


def _bounded_string(value: Any, field: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise AuditError(f"{field} must be a string")
    if not allow_empty and not value:
        raise AuditError(f"{field} must not be empty")
    if len(value) > limit:
        raise AuditError(f"{field} exceeds {limit} characters")
    if any(ord(ch) < 32 for ch in value):
        raise AuditError(f"{field} contains control characters")
    return value


def _normalize_repo_path(value: Any) -> str:
    path = _bounded_string(value, "file.path", MAX_PATH_LENGTH)
    if path.startswith("/") or "\\" in path:
        raise AuditError("file.path must be repository-relative POSIX syntax")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AuditError("file.path contains an unsafe path segment")
    return path


def _parse_files(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AuditError("files must be a list")
    if len(value) > MAX_FILES_PER_PR:
        raise AuditError(f"PR exceeds {MAX_FILES_PER_PR} files")

    paths: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, str):
            raw_path = entry
        elif isinstance(entry, dict) and set(entry).issuperset({"path"}):
            raw_path = entry["path"]
        else:
            raise AuditError("each files entry must be a path or an object containing path")
        path = _normalize_repo_path(raw_path)
        if path in seen:
            raise AuditError(f"duplicate file path inside PR: {path}")
        seen.add(path)
        paths.append(path)
    return tuple(sorted(paths))


def parse_snapshot(payload: Any) -> tuple[PullRequest, ...]:
    if not isinstance(payload, list):
        raise AuditError("snapshot root must be a list")
    if len(payload) > MAX_PRS:
        raise AuditError(f"snapshot exceeds {MAX_PRS} open PRs")

    prs: list[PullRequest] = []
    seen_numbers: set[int] = set()
    for row in payload:
        if not isinstance(row, dict):
            raise AuditError("each PR entry must be an object")
        number = row.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise AuditError("PR number must be a positive integer")
        if number in seen_numbers:
            raise AuditError(f"duplicate PR number: {number}")
        seen_numbers.add(number)

        draft = row.get("isDraft", False)
        if not isinstance(draft, bool):
            raise AuditError("isDraft must be boolean")

        head_ref = _bounded_string(row.get("headRefName", ""), "headRefName", MAX_HEAD_REF_LENGTH)
        updated_at = _bounded_string(row.get("updatedAt", ""), "updatedAt", 64)
        changed_files = row.get("changedFiles")
        if isinstance(changed_files, bool) or not isinstance(changed_files, int) or changed_files < 0:
            raise AuditError("changedFiles must be a non-negative integer")
        files = _parse_files(row.get("files"))
        if changed_files != len(files):
            raise AuditError(
                f"PR #{number} file evidence incomplete: "
                f"changedFiles={changed_files}, received={len(files)}"
            )
        prs.append(PullRequest(number, head_ref, draft, updated_at, files))

    return tuple(sorted(prs, key=lambda pr: pr.number))


def load_snapshot(path: Path) -> tuple[PullRequest, ...]:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise AuditError(f"snapshot not found: {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AuditError("snapshot path must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise AuditError("snapshot path must be a regular file")
    if info.st_size > MAX_INPUT_BYTES:
        raise AuditError(f"snapshot exceeds {MAX_INPUT_BYTES} bytes")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"unable to read valid JSON snapshot: {exc}") from exc
    return parse_snapshot(payload)


def overlap_report(prs: Iterable[PullRequest], current_pr: int | None = None) -> dict[str, Any]:
    pr_list = tuple(prs)
    by_number = {pr.number: pr for pr in pr_list}
    if current_pr is not None and current_pr not in by_number:
        raise AuditError(f"current PR #{current_pr} is absent from snapshot")

    owners: dict[str, list[int]] = {}
    for pr in pr_list:
        for path in pr.files:
            owners.setdefault(path, []).append(pr.number)

    conflicts: list[dict[str, Any]] = []
    for path, numbers in sorted(owners.items()):
        unique = sorted(set(numbers))
        if len(unique) < 2:
            continue
        if current_pr is not None and current_pr not in unique:
            continue
        peers = [number for number in unique if number != current_pr] if current_pr is not None else unique
        conflicts.append({"path": path, "pr_numbers": peers})

    current = by_number.get(current_pr) if current_pr is not None else None
    peer_numbers = sorted({n for item in conflicts for n in item["pr_numbers"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "current_pr" if current_pr is not None else "all_open_prs",
        "current_pr": current_pr,
        "current_head_ref": current.head_ref if current is not None else None,
        "open_pr_count": len(pr_list),
        "current_file_count": len(current.files) if current is not None else None,
        "conflict_count": len(conflicts),
        "conflicting_pr_numbers": peer_numbers,
        "conflicts": conflicts,
        "clear": not conflicts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="gh pr list JSON snapshot")
    parser.add_argument("--current-pr", type=int, help="limit report to overlaps involving this PR")
    parser.add_argument(
        "--require-clear",
        action="store_true",
        help="exit 2 when overlap is present; malformed evidence exits 1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prs = load_snapshot(args.input)
        report = overlap_report(prs, args.current_pr)
    except AuditError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.require_clear and not report["clear"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

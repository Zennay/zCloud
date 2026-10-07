#!/usr/bin/env python3
"""Fail-closed audit for candidate path overlap with branches that have no open PR."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import stat
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-unpr-branch-overlap-audit-v1"
MAX_BYTES = 512 * 1024
MAX_BRANCHES = 500
MAX_FILES_PER_BRANCH = 500
MAX_CANDIDATE_PATHS = 100
MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 60


class AuditError(ValueError):
    pass


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        st = path.lstat()
    except OSError as exc:
        raise AuditError(f"snapshot_unreadable:{exc.__class__.__name__}") from exc
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise AuditError("snapshot_not_regular")
    if st.st_size > MAX_BYTES:
        raise AuditError("snapshot_too_large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"snapshot_invalid:{exc.__class__.__name__}") from exc
    if not isinstance(payload, dict):
        raise AuditError("snapshot_not_object")
    return payload


def _parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise AuditError("captured_at_invalid")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditError("captured_at_invalid") from exc
    if parsed.tzinfo is None:
        raise AuditError("captured_at_naive")
    return parsed.astimezone(dt.timezone.utc)


def _safe_repo_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 300:
        raise AuditError("path_invalid")
    if value.startswith(("/", "\\")) or "\x00" in value:
        raise AuditError("path_invalid")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AuditError("path_invalid")
    return value


def _safe_branch(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise AuditError("branch_invalid")
    if value == "main":
        return value
    if value.startswith(("/", "-")) or value.endswith(("/", ".")):
        raise AuditError("branch_invalid")
    if any(token in value for token in ("..", "@{", "\\", " ", "~", "^", ":", "?", "*", "[")):
        raise AuditError("branch_invalid")
    return value


def _validate_snapshot(payload: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    allowed = {"captured_at", "inventory_complete", "base_branch", "branches"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AuditError("snapshot_unknown_fields")

    if payload.get("inventory_complete") is not True:
        raise AuditError("inventory_incomplete")
    if payload.get("base_branch") != "main":
        raise AuditError("base_branch_invalid")

    captured_at = _parse_utc(payload.get("captured_at"))
    age = (now - captured_at).total_seconds()
    if age > MAX_AGE_SECONDS:
        raise AuditError("snapshot_stale")
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise AuditError("snapshot_from_future")

    branches = payload.get("branches")
    if not isinstance(branches, list):
        raise AuditError("branches_invalid")
    if len(branches) > MAX_BRANCHES:
        raise AuditError("branches_too_many")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in branches:
        if not isinstance(row, dict):
            raise AuditError("branch_row_invalid")
        row_allowed = {"name", "has_open_pr", "changed_files_complete", "changed_files"}
        if set(row) - row_allowed:
            raise AuditError("branch_row_unknown_fields")
        name = _safe_branch(row.get("name"))
        if name in seen:
            raise AuditError("branch_duplicate")
        seen.add(name)
        if type(row.get("has_open_pr")) is not bool:
            raise AuditError("has_open_pr_invalid")
        if row.get("changed_files_complete") is not True:
            raise AuditError("changed_files_incomplete")
        files = row.get("changed_files")
        if not isinstance(files, list) or len(files) > MAX_FILES_PER_BRANCH:
            raise AuditError("changed_files_invalid")
        normalized_files: list[str] = []
        file_seen: set[str] = set()
        for item in files:
            path = _safe_repo_path(item)
            if path in file_seen:
                raise AuditError("changed_file_duplicate")
            file_seen.add(path)
            normalized_files.append(path)
        normalized.append(
            {
                "name": name,
                "has_open_pr": row["has_open_pr"],
                "changed_files": normalized_files,
            }
        )
    return normalized


def audit(payload: dict[str, Any], candidate_paths: list[str], now: dt.datetime) -> dict[str, Any]:
    if not candidate_paths or len(candidate_paths) > MAX_CANDIDATE_PATHS:
        raise AuditError("candidate_paths_invalid")
    normalized_candidates: list[str] = []
    seen_candidates: set[str] = set()
    for raw in candidate_paths:
        path = _safe_repo_path(raw)
        if path in seen_candidates:
            raise AuditError("candidate_path_duplicate")
        seen_candidates.add(path)
        normalized_candidates.append(path)

    branches = _validate_snapshot(payload, now)
    candidate_set = set(normalized_candidates)
    overlaps: list[dict[str, Any]] = []
    unpr_count = 0
    for row in branches:
        if row["name"] == "main" or row["has_open_pr"]:
            continue
        unpr_count += 1
        shared = sorted(candidate_set.intersection(row["changed_files"]))
        if shared:
            overlaps.append({"branch": row["name"], "paths": shared})

    return {
        "schema": SCHEMA,
        "status": "overlap" if overlaps else "clear",
        "candidate_path_count": len(normalized_candidates),
        "unpr_branch_count": unpr_count,
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--candidate-path", action="append", dest="candidate_paths", required=True)
    parser.add_argument("--require-clear", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = _load_snapshot(Path(args.snapshot))
        result = audit(payload, args.candidate_paths, dt.datetime.now(dt.timezone.utc))
    except AuditError as exc:
        result = {
            "schema": SCHEMA,
            "status": "incomplete",
            "reason": str(exc),
            "mutation_performed": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 1

    print(json.dumps(result, sort_keys=True))
    if args.require_clear and result["status"] != "clear":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

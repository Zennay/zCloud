#!/usr/bin/env python3
"""Audit recent un-PR'd branch ownership against a candidate change-set."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import stat
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA = "recent-branch-ownership-snapshot-v1"
REPORT_SCHEMA = "recent-branch-ownership-audit-v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_BRANCHES = 500
MAX_PATHS = 500
SHA_LENGTH = 40


class AuditError(ValueError):
    pass


def parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AuditError("timestamp_invalid")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AuditError("timestamp_invalid") from exc
    return parsed.astimezone(dt.timezone.utc)


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise AuditError("path_invalid")
    if value.startswith("/") or "\\" in value or any(ord(ch) < 32 for ch in value):
        raise AuditError("path_invalid")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AuditError("path_invalid")
    return value


def _sha(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != SHA_LENGTH
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise AuditError("sha_invalid")
    return value


def _read_json_file(path: Path) -> Any:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise AuditError("input_not_found") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AuditError("input_symlink_rejected")
    if not stat.S_ISREG(info.st_mode):
        raise AuditError("input_not_regular_file")
    if info.st_size > MAX_INPUT_BYTES:
        raise AuditError("input_too_large")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError("input_json_invalid") from exc


def parse_current_pr(payload: Any, expected_head: str) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise AuditError("current_pr_invalid")
    head = _sha(payload.get("headRefOid"))
    if head != _sha(expected_head):
        raise AuditError("current_pr_head_mismatch")
    changed = payload.get("changedFiles")
    files = payload.get("files")
    if isinstance(changed, bool) or not isinstance(changed, int) or changed < 0:
        raise AuditError("current_pr_changed_files_invalid")
    if not isinstance(files, list):
        raise AuditError("current_pr_files_invalid")
    if changed != len(files):
        raise AuditError("current_pr_file_evidence_incomplete")
    if changed > MAX_PATHS:
        raise AuditError("current_pr_path_bound_exceeded")

    paths: list[str] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or "path" not in item:
            raise AuditError("current_pr_file_invalid")
        path = _safe_path(item["path"])
        if path in seen:
            raise AuditError("current_pr_duplicate_path")
        seen.add(path)
        paths.append(path)
    return tuple(sorted(paths))


def parse_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SNAPSHOT_SCHEMA:
        raise AuditError("snapshot_schema_invalid")
    repository = payload.get("repository")
    if not isinstance(repository, str) or repository.count("/") != 1 or len(repository) > 200:
        raise AuditError("snapshot_repository_invalid")

    main_sha = _sha(payload.get("main_sha"))
    as_of = parse_utc(payload.get("as_of"))
    recent_hours = payload.get("recent_hours")
    if isinstance(recent_hours, bool) or not isinstance(recent_hours, int) or not 1 <= recent_hours <= 168:
        raise AuditError("snapshot_recent_hours_invalid")
    branches = payload.get("branches")
    if not isinstance(branches, list):
        raise AuditError("snapshot_branches_invalid")
    if len(branches) > MAX_BRANCHES:
        raise AuditError("snapshot_branch_bound_exceeded")
    if payload.get("branch_count") != len(branches):
        raise AuditError("snapshot_branch_count_mismatch")

    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for row in branches:
        if not isinstance(row, dict):
            raise AuditError("snapshot_branch_invalid")
        name = row.get("name")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 160
            or any(ord(ch) < 32 for ch in name)
        ):
            raise AuditError("snapshot_branch_name_invalid")
        if name in names:
            raise AuditError("snapshot_duplicate_branch")
        names.add(name)
        sha = _sha(row.get("sha"))
        committed_at_text = row.get("committed_at")
        committed_at = parse_utc(committed_at_text)
        has_open_pr = row.get("has_open_pr")
        if not isinstance(has_open_pr, bool):
            raise AuditError("snapshot_open_pr_flag_invalid")

        compare = row.get("compare")
        normalized_compare = None
        if compare is not None:
            if not isinstance(compare, dict):
                raise AuditError("snapshot_compare_invalid")
            ahead = compare.get("ahead_by")
            behind = compare.get("behind_by")
            if (
                isinstance(ahead, bool) or not isinstance(ahead, int) or ahead < 0
                or isinstance(behind, bool) or not isinstance(behind, int) or behind < 0
            ):
                raise AuditError("snapshot_compare_count_invalid")
            merge_base = _sha(compare.get("merge_base_sha"))
            complete = compare.get("file_list_complete")
            if not isinstance(complete, bool):
                raise AuditError("snapshot_compare_completeness_invalid")
            files = compare.get("files")
            if not isinstance(files, list) or len(files) > 300:
                raise AuditError("snapshot_compare_files_invalid")
            safe_files: list[str] = []
            seen_files: set[str] = set()
            for item in files:
                path = _safe_path(item)
                if path in seen_files:
                    raise AuditError("snapshot_compare_duplicate_path")
                seen_files.add(path)
                safe_files.append(path)
            normalized_compare = {
                "ahead_by": ahead,
                "behind_by": behind,
                "merge_base_sha": merge_base,
                "file_list_complete": complete,
                "files": tuple(sorted(safe_files)),
            }

        normalized.append(
            {
                "name": name,
                "sha": sha,
                "committed_at_text": committed_at_text,
                "committed_at": committed_at,
                "has_open_pr": has_open_pr,
                "compare": normalized_compare,
            }
        )

    return {
        "repository": repository,
        "main_sha": main_sha,
        "as_of": as_of,
        "as_of_text": payload["as_of"],
        "recent_hours": recent_hours,
        "branches": tuple(normalized),
    }


def build_report(snapshot: dict[str, Any], current_paths: tuple[str, ...] | None) -> dict[str, Any]:
    cutoff = snapshot["as_of"] - dt.timedelta(hours=snapshot["recent_hours"])
    future_limit = snapshot["as_of"] + dt.timedelta(minutes=5)
    owner_rows: list[dict[str, Any]] = []
    stale_count = 0
    represented_count = 0
    not_ahead_count = 0

    for row in snapshot["branches"]:
        name = row["name"]
        committed_at = row["committed_at"]
        compare = row["compare"]

        if committed_at > future_limit:
            raise AuditError("future_branch_timestamp")
        if name == "main":
            if compare is not None:
                raise AuditError("main_branch_compare_unexpected")
            continue
        if row["has_open_pr"]:
            represented_count += 1
            if compare is not None:
                raise AuditError("represented_branch_compare_unexpected")
            continue
        if committed_at < cutoff:
            stale_count += 1
            if compare is not None:
                raise AuditError("stale_branch_compare_unexpected")
            continue
        if compare is None:
            raise AuditError("recent_branch_compare_missing")
        if not compare["file_list_complete"]:
            raise AuditError("recent_branch_file_evidence_incomplete")
        if compare["ahead_by"] == 0:
            not_ahead_count += 1
            continue

        owner_rows.append(
            {
                "name": name,
                "sha": row["sha"],
                "committed_at": row["committed_at_text"],
                "ahead_by": compare["ahead_by"],
                "behind_by": compare["behind_by"],
                "file_count": len(compare["files"]),
                "files": compare["files"],
            }
        )

    conflicts: list[dict[str, Any]] = []
    current_set = set(current_paths or ())
    if current_paths is not None:
        by_path: dict[str, list[dict[str, str]]] = {}
        for owner in owner_rows:
            for path in owner["files"]:
                if path not in current_set:
                    continue
                by_path.setdefault(path, []).append(
                    {"name": owner["name"], "sha": owner["sha"]}
                )
        for path in sorted(by_path):
            conflicts.append(
                {
                    "path": path,
                    "branches": sorted(by_path[path], key=lambda item: item["name"]),
                }
            )

    public_owners = [
        {
            "name": owner["name"],
            "sha": owner["sha"],
            "committed_at": owner["committed_at"],
            "ahead_by": owner["ahead_by"],
            "behind_by": owner["behind_by"],
            "file_count": owner["file_count"],
        }
        for owner in sorted(owner_rows, key=lambda item: item["name"])
    ]
    return {
        "schema_version": REPORT_SCHEMA,
        "repository": snapshot["repository"],
        "main_sha": snapshot["main_sha"],
        "as_of": snapshot["as_of_text"],
        "recent_hours": snapshot["recent_hours"],
        "branch_count": len(snapshot["branches"]),
        "recent_unpr_owner_count": len(public_owners),
        "represented_by_open_pr_count": represented_count,
        "stale_branch_count": stale_count,
        "recent_not_ahead_count": not_ahead_count,
        "current_path_count": len(current_paths) if current_paths is not None else None,
        "conflict_count": len(conflicts),
        "owners": public_owners,
        "conflicts": conflicts,
        "clear": not conflicts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--current-pr-json", type=Path)
    parser.add_argument("--expected-current-head")
    parser.add_argument("--require-clear", action="store_true")
    args = parser.parse_args(argv)

    try:
        snapshot = parse_snapshot(_read_json_file(args.snapshot))
        current_paths = None
        if args.current_pr_json:
            if not args.expected_current_head:
                raise AuditError("current_pr_expected_head_required")
            current_paths = parse_current_pr(
                _read_json_file(args.current_pr_json),
                args.expected_current_head,
            )
        elif args.expected_current_head:
            raise AuditError("expected_head_without_current_pr")
        if args.require_clear and current_paths is None:
            raise AuditError("require_clear_needs_current_pr")
        report = build_report(snapshot, current_paths)
    except AuditError as exc:
        print(json.dumps({"schema_version": REPORT_SCHEMA, "error": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.require_clear and not report["clear"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

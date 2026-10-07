#!/usr/bin/env python3
"""Collect bounded recent branch ownership evidence from GitHub.

The collector is intentionally read-only. It emits branch names, SHAs, commit
timestamps and changed repository paths only. Commit messages, PR titles/bodies,
authors and comments are never requested.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from typing import Any, Callable

SCHEMA_VERSION = "recent-branch-ownership-snapshot-v1"
MAX_BRANCHES = 500
MAX_OPEN_PRS = 500
MAX_COMPARE_FILES = 300
MAX_RECENT_COMPARE_BRANCHES = 100
MAX_RECENT_HOURS = 168
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class CollectorError(ValueError):
    pass


def parse_utc(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CollectorError("timestamp_invalid")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CollectorError("timestamp_invalid") from exc
    return parsed.astimezone(dt.timezone.utc)


def _run_json(args: list[str]) -> Any:
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CollectorError("github_command_failed") from exc
    if proc.returncode != 0:
        raise CollectorError("github_command_failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CollectorError("github_json_invalid") from exc


def _validate_sha(value: Any) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise CollectorError("sha_invalid")
    return value


def _validate_branch_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise CollectorError("branch_name_invalid")
    if any(ord(ch) < 32 for ch in value):
        raise CollectorError("branch_name_invalid")
    return value


def _graphql_page(
    owner: str,
    name: str,
    cursor: str | None,
    runner: Callable[[list[str]], Any],
) -> dict[str, Any]:
    query = """
query($owner:String!, $name:String!, $endCursor:String) {
  repository(owner:$owner, name:$name) {
    refs(refPrefix:"refs/heads/", first:100, after:$endCursor) {
      nodes {
        name
        target {
          ... on Commit {
            oid
            committedDate
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()
    args = [
        "gh", "api", "graphql",
        "-f", f"owner={owner}",
        "-f", f"name={name}",
        "-f", f"query={query}",
    ]
    if cursor:
        args.extend(["-f", f"endCursor={cursor}"])
    payload = runner(args)
    try:
        refs = payload["data"]["repository"]["refs"]
        if not isinstance(refs["nodes"], list) or not isinstance(refs["pageInfo"], dict):
            raise TypeError
    except (KeyError, TypeError) as exc:
        raise CollectorError("branch_page_invalid") from exc
    return refs


def _open_pr_heads(
    repo: str,
    runner: Callable[[list[str]], Any],
) -> frozenset[str]:
    rows = runner([
        "gh", "pr", "list",
        "--repo", repo,
        "--state", "open",
        "--limit", str(MAX_OPEN_PRS + 1),
        "--json", "headRefName,isCrossRepository",
    ])
    if not isinstance(rows, list):
        raise CollectorError("open_pr_snapshot_invalid")
    if len(rows) > MAX_OPEN_PRS:
        raise CollectorError("open_pr_bound_exceeded")

    heads: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CollectorError("open_pr_snapshot_invalid")
        branch = _validate_branch_name(row.get("headRefName"))
        cross = row.get("isCrossRepository")
        if not isinstance(cross, bool):
            raise CollectorError("open_pr_snapshot_invalid")
        if not cross:
            heads.add(branch)
    return frozenset(heads)


def _branch_refs(
    owner: str,
    name: str,
    runner: Callable[[list[str]], Any],
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    cursor: str | None = None
    while True:
        refs = _graphql_page(owner, name, cursor, runner)
        for node in refs["nodes"]:
            if not isinstance(node, dict):
                raise CollectorError("branch_node_invalid")
            branch_name = _validate_branch_name(node.get("name"))
            if branch_name in seen:
                raise CollectorError("duplicate_branch_ref")
            seen.add(branch_name)
            target = node.get("target")
            if not isinstance(target, dict):
                raise CollectorError("branch_target_invalid")
            sha = _validate_sha(target.get("oid"))
            committed_at = target.get("committedDate")
            parse_utc(committed_at)
            rows.append({
                "name": branch_name,
                "sha": sha,
                "committed_at": committed_at,
            })
            if len(rows) > MAX_BRANCHES:
                raise CollectorError("branch_bound_exceeded")

        page = refs["pageInfo"]
        has_next = page.get("hasNextPage")
        next_cursor = page.get("endCursor")
        if not isinstance(has_next, bool):
            raise CollectorError("branch_page_invalid")
        if not has_next:
            break
        if len(rows) >= MAX_BRANCHES:
            raise CollectorError("branch_bound_exceeded")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise CollectorError("branch_page_invalid")
        cursor = next_cursor

    return tuple(sorted(rows, key=lambda item: item["name"]))


def collect_snapshot(
    repo: str,
    as_of: dt.datetime,
    recent_hours: int,
    runner: Callable[[list[str]], Any] = _run_json,
) -> dict[str, Any]:
    if repo.count("/") != 1:
        raise CollectorError("repo_invalid")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise CollectorError("repo_invalid")
    if not 1 <= recent_hours <= MAX_RECENT_HOURS:
        raise CollectorError("recent_hours_invalid")
    if as_of.tzinfo is None:
        raise CollectorError("as_of_timezone_required")
    as_of = as_of.astimezone(dt.timezone.utc)

    ref = runner(["gh", "api", f"repos/{repo}/git/ref/heads/main"])
    try:
        main_sha = _validate_sha(ref["object"]["sha"])
    except (KeyError, TypeError) as exc:
        raise CollectorError("main_ref_invalid") from exc

    open_heads = _open_pr_heads(repo, runner)
    ref_rows = _branch_refs(owner, name, runner)
    main_rows = [row for row in ref_rows if row["name"] == "main"]
    if len(main_rows) != 1 or main_rows[0]["sha"] != main_sha:
        raise CollectorError("main_ref_snapshot_mismatch")

    branches: list[dict[str, Any]] = [
        {
            "name": row["name"],
            "sha": row["sha"],
            "committed_at": row["committed_at"],
            "has_open_pr": row["name"] in open_heads,
            "compare": None,
        }
        for row in ref_rows
    ]

    cutoff = as_of - dt.timedelta(hours=recent_hours)
    candidates: list[dict[str, Any]] = []
    for row in branches:
        committed_dt = parse_utc(row["committed_at"])
        if row["name"] == "main" or row["has_open_pr"] or committed_dt < cutoff:
            continue
        if committed_dt > as_of + dt.timedelta(minutes=5):
            raise CollectorError("future_branch_timestamp")
        candidates.append(row)
    if len(candidates) > MAX_RECENT_COMPARE_BRANCHES:
        raise CollectorError("recent_branch_compare_bound_exceeded")

    for row in candidates:
        compare = runner([
            "gh", "api",
            f"repos/{repo}/compare/{main_sha}...{row['sha']}",
            "--jq",
            (
                "{ahead_by:.ahead_by,behind_by:.behind_by,"
                "merge_base_commit:{sha:.merge_base_commit.sha},"
                "files:[.files[]?|{filename:.filename}]}"
            ),
        ])
        try:
            ahead_by = compare["ahead_by"]
            behind_by = compare["behind_by"]
            merge_base_sha = _validate_sha(compare["merge_base_commit"]["sha"])
            files_raw = compare.get("files", [])
        except (KeyError, TypeError) as exc:
            raise CollectorError("compare_payload_invalid") from exc
        if (
            isinstance(ahead_by, bool) or not isinstance(ahead_by, int) or ahead_by < 0
            or isinstance(behind_by, bool) or not isinstance(behind_by, int) or behind_by < 0
            or not isinstance(files_raw, list)
        ):
            raise CollectorError("compare_payload_invalid")

        files: list[str] = []
        for item in files_raw:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                raise CollectorError("compare_file_invalid")
            path = item["filename"]
            if not path or len(path) > 512 or path.startswith("/") or "\\" in path:
                raise CollectorError("compare_file_invalid")
            parts = path.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise CollectorError("compare_file_invalid")
            files.append(path)

        row["compare"] = {
            "ahead_by": ahead_by,
            "behind_by": behind_by,
            "merge_base_sha": merge_base_sha,
            "files": sorted(set(files)),
            # GitHub compare returns at most 300 files. Exactly 300 is treated
            # as potentially truncated rather than guessed complete.
            "file_list_complete": len(files_raw) < MAX_COMPARE_FILES,
        }

    open_heads_after = _open_pr_heads(repo, runner)
    if open_heads_after != open_heads:
        raise CollectorError("open_pr_snapshot_changed")
    ref_rows_after = _branch_refs(owner, name, runner)
    if ref_rows_after != ref_rows:
        raise CollectorError("branch_snapshot_changed")
    ref_after = runner(["gh", "api", f"repos/{repo}/git/ref/heads/main"])
    try:
        main_sha_after = _validate_sha(ref_after["object"]["sha"])
    except (KeyError, TypeError) as exc:
        raise CollectorError("main_ref_invalid") from exc
    if main_sha_after != main_sha:
        raise CollectorError("main_ref_snapshot_changed")

    branches.sort(key=lambda item: item["name"])
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repo,
        "main_sha": main_sha,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "recent_hours": recent_hours,
        "branch_count": len(branches),
        "open_pr_head_count": len(open_heads),
        "branches": branches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--recent-hours", type=int, default=72)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    try:
        as_of = parse_utc(args.as_of)
        payload = collect_snapshot(args.repo, as_of, args.recent_hours)
    except CollectorError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": str(exc)}, sort_keys=True))
        return 1

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if args.output:
        path = os.path.abspath(args.output)
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

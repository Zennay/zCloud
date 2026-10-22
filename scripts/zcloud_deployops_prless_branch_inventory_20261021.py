#!/usr/bin/env python3
"""Collect a bounded, read-only inventory of local branches without open PRs.

A PR-less branch is not necessarily abandoned, unowned, safe to delete, or
eligible to merge. This collector never changes refs or authorizes release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from scripts.zcloud_deployops_pr_owner_collision_20261015 import (
    InvalidInventory, MAX_PRS, SHA,
)
from scripts.zcloud_deployops_pr_owner_snapshot_20261015 import (
    REPO, _main_sha, github_reader,
)

MAX_BRANCH_PAGES = 51
MAX_BRANCHES = 5000
MAX_PR_PAGES = 10
MAX_OUTPUT_BYTES = 1_800_000
MAX_PRLESS_BRANCHES_FOR_CURRENT_SLICE = 200
MAX_COMPARE_FILES = 300
MAX_PATH_PREVIEW = 20
MAX_BRANCH_PREVIEW = 40


def _bounded_text(value, limit=255):
    return (type(value) is str and 1 <= len(value) <= limit
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


def _branches(read):
    """Read every branch page; refuse truncation, duplicates, and weak identities."""
    rows = []
    seen = set()
    complete = False
    for page in range(1, MAX_BRANCH_PAGES + 1):
        batch = read(f"/branches?per_page=100&page={page}")
        if type(batch) is not list or len(batch) > 100:
            raise InvalidInventory("invalid_branch_page")
        for entry in batch:
            if type(entry) is not dict:
                raise InvalidInventory("invalid_branch_page_item")
            name = entry.get("name")
            commit = entry.get("commit")
            sha = commit.get("sha") if type(commit) is dict else None
            protected = entry.get("protected")
            if (not _bounded_text(name) or name in seen
                    or type(sha) is not str or SHA.fullmatch(sha) is None
                    or type(protected) is not bool):
                raise InvalidInventory("invalid_or_duplicate_branch_identity")
            seen.add(name)
            rows.append({"name": name, "head_sha": sha, "protected": protected})
            if len(rows) > MAX_BRANCHES:
                raise InvalidInventory("branch_inventory_limit_exceeded")
        if len(batch) < 100:
            complete = True
            break
    if not complete:
        raise InvalidInventory("incomplete_branch_pagination")
    return rows


def _open_pr_heads(read):
    """Capture full open-PR identity needed to classify same-repository heads."""
    rows = []
    seen = set()
    complete = False
    for page in range(1, MAX_PR_PAGES + 1):
        batch = read(f"/pulls?state=open&per_page=100&page={page}")
        if type(batch) is not list or len(batch) > 100:
            raise InvalidInventory("invalid_open_pr_page")
        for entry in batch:
            if type(entry) is not dict:
                raise InvalidInventory("invalid_open_pr_item")
            number = entry.get("number")
            head, base = entry.get("head"), entry.get("base")
            head_repo = head.get("repo") if type(head) is dict else None
            head_repo_name = (head_repo.get("full_name")
                              if type(head_repo) is dict else None)
            head_ref = head.get("ref") if type(head) is dict else None
            head_sha = head.get("sha") if type(head) is dict else None
            base_sha = base.get("sha") if type(base) is dict else None
            if (type(number) is not int or not 1 <= number <= 10_000_000
                    or number in seen or entry.get("state") != "open"
                    or not _bounded_text(head_repo_name, 200)
                    or not _bounded_text(head_ref)
                    or type(head_sha) is not str or SHA.fullmatch(head_sha) is None
                    or type(base_sha) is not str or SHA.fullmatch(base_sha) is None):
                raise InvalidInventory("incomplete_open_pr_identity")
            seen.add(number)
            rows.append({
                "number": number, "head_repo": head_repo_name,
                "head_ref": head_ref, "head_sha": head_sha,
                "base_sha": base_sha,
            })
            if len(rows) > MAX_PRS:
                raise InvalidInventory("open_pr_inventory_limit_exceeded")
        if len(batch) < 100:
            complete = True
            break
    if not complete:
        raise InvalidInventory("incomplete_open_pr_pagination")
    return rows


def _safe_git_path(value):
    if (not _bounded_text(value, 500) or value.startswith("/")
            or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _commit_time(read, sha):
    payload = read(f"/commits/{sha}")
    if type(payload) is not dict or payload.get("sha") != sha:
        raise InvalidInventory("branch_commit_sha_mismatch")
    commit = payload.get("commit")
    committer = commit.get("committer") if type(commit) is dict else None
    value = committer.get("date") if type(committer) is dict else None
    if not _bounded_text(value, 64):
        raise InvalidInventory("branch_commit_time_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise InvalidInventory("branch_commit_time_invalid") from None
    if parsed.tzinfo is None:
        raise InvalidInventory("branch_commit_time_missing_timezone")
    return parsed.astimezone(timezone.utc)


def _compare_branch(read, main_sha, head_sha):
    comparison = read(f"/compare/{main_sha}...{head_sha}")
    if type(comparison) is not dict:
        raise InvalidInventory("invalid_branch_comparison")
    status = comparison.get("status")
    ahead_by = comparison.get("ahead_by")
    behind_by = comparison.get("behind_by")
    if (status not in {"ahead", "behind", "identical", "diverged"}
            or type(ahead_by) is not int or ahead_by < 0
            or type(behind_by) is not int or behind_by < 0):
        raise InvalidInventory("invalid_branch_comparison_identity")
    if (status == "ahead" and (ahead_by < 1 or behind_by != 0)
            or status == "behind" and (ahead_by != 0 or behind_by < 1)
            or status == "identical" and (ahead_by != 0 or behind_by != 0)
            or status == "diverged" and (ahead_by < 1 or behind_by < 1)):
        raise InvalidInventory("inconsistent_branch_comparison_counts")
    merge_base = comparison.get("merge_base_commit")
    merge_base_sha = merge_base.get("sha") if type(merge_base) is dict else None
    if type(merge_base_sha) is not str or SHA.fullmatch(merge_base_sha) is None:
        raise InvalidInventory("invalid_branch_merge_base")
    files = comparison.get("files")
    if type(files) is not list:
        raise InvalidInventory("missing_changed_file_inventory")
    if len(files) >= MAX_COMPARE_FILES:
        raise InvalidInventory("changed_file_inventory_truncated")
    paths = set()
    for entry in files:
        if type(entry) is not dict:
            raise InvalidInventory("invalid_changed_file_item")
        filename = entry.get("filename")
        if not _safe_git_path(filename):
            raise InvalidInventory("unsafe_changed_file_path")
        paths.add(filename)
        previous = entry.get("previous_filename")
        if previous is not None:
            if not _safe_git_path(previous):
                raise InvalidInventory("unsafe_previous_file_path")
            paths.add(previous)
    return {
        "status": status,
        "ahead_by": ahead_by,
        "behind_by": behind_by,
        "merge_base_sha": merge_base_sha,
        "changed_file_count": len(files),
        "changed_paths": sorted(paths),
    }


def _path_overlap(current_path, owner_path):
    if current_path == owner_path:
        return "exact_path"
    if current_path.startswith(owner_path + "/"):
        return "current_nested_under_owner_path"
    if owner_path.startswith(current_path + "/"):
        return "owner_nested_under_current_path"
    return None


def _current_slice(read, main_sha, branches, current_branch, recent_days, started_at):
    if not _bounded_text(current_branch):
        raise InvalidInventory("invalid_current_branch")
    if type(recent_days) is not int or not 1 <= recent_days <= 30:
        raise InvalidInventory("invalid_recent_window")
    prless = [row for row in branches if row["classification"] == "prless"]
    if len(prless) > MAX_PRLESS_BRANCHES_FOR_CURRENT_SLICE:
        raise InvalidInventory("prless_candidate_limit_exceeded")
    by_name = {row["name"]: row for row in branches}
    current = by_name.get(current_branch)
    if current is None:
        raise InvalidInventory("current_branch_not_in_snapshot")
    if current_branch == "main" or current["classification"] != "prless":
        raise InvalidInventory("current_branch_must_be_prless_non_main")

    current_time = _commit_time(read, current["head_sha"])
    current_age = (started_at - current_time).total_seconds()
    if current_age < -120:
        raise InvalidInventory("current_branch_commit_time_in_future")
    current_age = max(0, int(current_age))
    current_diff = _compare_branch(read, main_sha, current["head_sha"])
    if current_diff["ahead_by"] < 1:
        raise InvalidInventory("current_branch_not_ahead_of_main")
    current_paths = current_diff["changed_paths"]

    recent_prless_count = 0
    older_prless_count = 0
    recent_not_ahead_count = 0
    owners = []
    overlaps = []
    overlap_count = 0
    older_preview = []

    for row in prless:
        if row["name"] == current_branch:
            continue
        commit_time = _commit_time(read, row["head_sha"])
        age_seconds = (started_at - commit_time).total_seconds()
        if age_seconds < -120:
            raise InvalidInventory("branch_commit_time_in_future")
        age_seconds = max(0, int(age_seconds))
        if age_seconds > recent_days * 86400:
            older_prless_count += 1
            if len(older_preview) < MAX_BRANCH_PREVIEW:
                older_preview.append({
                    "name": row["name"], "head_sha": row["head_sha"],
                    "age_seconds": age_seconds,
                })
            continue

        recent_prless_count += 1
        comparison = _compare_branch(read, main_sha, row["head_sha"])
        if comparison["ahead_by"] == 0:
            recent_not_ahead_count += 1
            continue

        owner = {
            "name": row["name"],
            "head_sha": row["head_sha"],
            "age_seconds": age_seconds,
            "status": comparison["status"],
            "ahead_by": comparison["ahead_by"],
            "behind_by": comparison["behind_by"],
            "merge_base_sha": comparison["merge_base_sha"],
            "changed_file_count": comparison["changed_file_count"],
            "changed_path_count": len(comparison["changed_paths"]),
            "changed_paths_preview": comparison["changed_paths"][:MAX_PATH_PREVIEW],
            "changed_paths_preview_omitted": max(
                0, len(comparison["changed_paths"]) - MAX_PATH_PREVIEW
            ),
        }
        owners.append(owner)
        for current_path in current_paths:
            for owner_path in comparison["changed_paths"]:
                relation = _path_overlap(current_path, owner_path)
                if relation is None:
                    continue
                overlap_count += 1
                if len(overlaps) < MAX_BRANCH_PREVIEW:
                    overlaps.append({
                        "current_branch": current_branch,
                        "current_path": current_path,
                        "owner_branch": row["name"],
                        "owner_head_sha": row["head_sha"],
                        "owner_path": owner_path,
                        "relation": relation,
                    })

    return {
        "state": "conflict" if overlap_count else "clear",
        "current_branch": current_branch,
        "current_head_sha": current["head_sha"],
        "current_branch_age_seconds": current_age,
        "main_sha": main_sha,
        "recent_window_days": recent_days,
        "prless_candidate_count": len(prless),
        "recent_prless_branch_count": recent_prless_count,
        "recent_prless_ahead_owner_count": len(owners),
        "recent_prless_not_ahead_count": recent_not_ahead_count,
        "older_prless_branch_count": older_prless_count,
        "older_prless_preview": older_preview,
        "current_status": current_diff["status"],
        "current_ahead_by": current_diff["ahead_by"],
        "current_behind_by": current_diff["behind_by"],
        "current_merge_base_sha": current_diff["merge_base_sha"],
        "current_changed_file_count": current_diff["changed_file_count"],
        "current_changed_path_count": len(current_paths),
        "current_changed_paths_preview": current_paths[:MAX_PATH_PREVIEW],
        "current_changed_paths_preview_omitted": max(
            0, len(current_paths) - MAX_PATH_PREVIEW
        ),
        "recent_owner_branches": owners[:MAX_BRANCH_PREVIEW],
        "recent_owner_branches_omitted": max(0, len(owners) - MAX_BRANCH_PREVIEW),
        "overlap_count": overlap_count,
        "overlaps": overlaps,
        "overlaps_omitted": max(0, overlap_count - len(overlaps)),
        "mutation_performed": False,
        "release_authorized": False,
        "deployment_authorized": False,
    }


def collect(read, *, current_branch=None, recent_days=7):
    """Require complete, repeatable inventories; optionally check a current work slice."""
    if type(recent_days) is not int or not 1 <= recent_days <= 30:
        raise InvalidInventory("invalid_recent_window")
    if current_branch is not None and not _bounded_text(current_branch):
        raise InvalidInventory("invalid_current_branch")

    started_at = datetime.now(timezone.utc)
    main_before = _main_sha(read)
    branches_before = _branches(read)
    prs_before = _open_pr_heads(read)

    main_rows = [row for row in branches_before if row["name"] == "main"]
    if len(main_rows) != 1 or main_rows[0]["head_sha"] != main_before:
        raise InvalidInventory("main_branch_mismatch")

    open_by_ref = {}
    for pr in prs_before:
        if pr["head_repo"] == REPO:
            open_by_ref.setdefault(pr["head_ref"], []).append(pr["number"])

    annotated = []
    prless = []
    for row in sorted(branches_before, key=lambda item: item["name"]):
        open_numbers = sorted(open_by_ref.get(row["name"], []))
        item = {
            **row,
            "open_pr_numbers": open_numbers,
            "classification": ("main" if row["name"] == "main"
                               else "has_open_pr" if open_numbers
                               else "prless"),
        }
        annotated.append(item)
        if item["classification"] == "prless":
            prless.append({
                "name": item["name"], "head_sha": item["head_sha"],
                "protected": item["protected"],
            })

    current_slice = None
    if current_branch is not None:
        current_slice = _current_slice(
            read, main_before, annotated, current_branch, recent_days, started_at
        )

    # Re-read the full inventories after commit/compare evidence so a branch,
    # PR, or main movement during the current-slice analysis refuses the report.
    branches_after = _branches(read)
    prs_after = _open_pr_heads(read)
    main_after = _main_sha(read)
    completed_at = datetime.now(timezone.utc)
    if completed_at < started_at:
        raise InvalidInventory("snapshot_clock_moved_backwards")
    if branches_before != branches_after:
        raise InvalidInventory("branch_inventory_changed_during_snapshot")
    if prs_before != prs_after:
        raise InvalidInventory("open_pr_inventory_changed_during_snapshot")
    if main_before != main_after:
        raise InvalidInventory("main_changed_during_snapshot")

    result = {
        "policy": "zcloud-deployops-prless-branch-inventory-v1",
        "repo": REPO,
        "main_sha": main_before,
        "snapshot_started_at_utc": started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "snapshot_completed_at_utc": completed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "snapshot_duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "branch_inventory_complete": True,
        "open_pr_inventory_complete": True,
        "branches_scanned": len(annotated),
        "open_prs_scanned": len(prs_before),
        "prless_branch_count": len(prless),
        "branches": annotated,
        "prless_branches": prless,
        "mutation_performed": False,
        "release_authorized": False,
        "deployment_authorized": False,
    }
    if current_slice is not None:
        result["current_slice"] = current_slice

    # Preserve a stable inventory-state fingerprint across identical snapshots.
    # Observation clocks and elapsed ages describe freshness, not branch identity.
    fingerprint_view = json.loads(json.dumps(result))
    for key in ("snapshot_started_at_utc", "snapshot_completed_at_utc",
                "snapshot_duration_seconds"):
        fingerprint_view.pop(key, None)
    if type(fingerprint_view.get("current_slice")) is dict:
        slice_view = fingerprint_view["current_slice"]
        slice_view.pop("current_branch_age_seconds", None)
        for owner in slice_view.get("recent_owner_branches", []):
            if type(owner) is dict:
                owner.pop("age_seconds", None)
        for old in slice_view.get("older_prless_preview", []):
            if type(old) is dict:
                old.pop("age_seconds", None)
    canonical = json.dumps(
        fingerprint_view, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["snapshot_sha256_unverified"] = hashlib.sha256(canonical).hexdigest()

    if current_slice is not None:
        # The current-slice report is compact, but its fingerprint covers the
        # complete, rechecked inventory and all bounded overlap evidence above.
        result = {
            "policy": "zcloud-deployops-prless-current-slice-v1",
            "repo": REPO,
            "main_sha": main_before,
            "snapshot_started_at_utc": result["snapshot_started_at_utc"],
            "snapshot_completed_at_utc": result["snapshot_completed_at_utc"],
            "snapshot_duration_seconds": result["snapshot_duration_seconds"],
            "branch_inventory_complete": True,
            "open_pr_inventory_complete": True,
            "branches_scanned": len(annotated),
            "open_prs_scanned": len(prs_before),
            "prless_branch_count": len(prless),
            "current_slice": current_slice,
            "snapshot_sha256_unverified": hashlib.sha256(canonical).hexdigest(),
            "mutation_performed": False,
            "release_authorized": False,
            "deployment_authorized": False,
        }

    rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(rendered.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise InvalidInventory("branch_inventory_output_limit_exceeded")
    return rendered


def main():
    parser = argparse.ArgumentParser(
        description="Read-only PR-less branch inventory and optional current-slice conflict guard"
    )
    parser.add_argument(
        "--current-branch",
        help="Check this un-PR'd branch against recent un-PR'd owners; exact branch ref required",
    )
    parser.add_argument(
        "--recent-days", type=int, default=7,
        help="Bounded recency window for owner candidates (1-30 days; default 7)",
    )
    args = parser.parse_args()

    try:
        rendered = collect(
            github_reader(os.environ.get("GITHUB_TOKEN", "")),
            current_branch=args.current_branch,
            recent_days=args.recent_days,
        )
        print(rendered)
        if args.current_branch is not None:
            report = json.loads(rendered)
            return 1 if report.get("current_slice", {}).get("state") == "conflict" else 0
    except (InvalidInventory, UnicodeError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, InvalidInventory) else "invalid_github_response"
        print(json.dumps({
            "policy": ("zcloud-deployops-prless-current-slice-v1"
                       if args.current_branch is not None
                       else "zcloud-deployops-prless-branch-inventory-v1"),
            "state": "refused", "reason": reason,
            "mutation_performed": False, "release_authorized": False,
            "deployment_authorized": False,
        }, sort_keys=True, separators=(",", ":")))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

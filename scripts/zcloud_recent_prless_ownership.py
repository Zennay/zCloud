#!/usr/bin/env python3
"""Bounded, read-only recent PR-less branch ownership evidence (#870).

Uses the canonical complete branch/PR inventory from #1164. No owner/lease,
merge, deploy or permission is inferred from a branch, PR or green check.
Never executes checked-out code, nor writes GitHub, SQLite or runtime state.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
import time
import urllib.error
import urllib.request

from zcloud_deploy_prless_inventory_20261013_w1 import (
    API, OWNER, REPO, SHA, InventoryDenied, _DenyRedirect, _path,
    _path_conflict, _sha, collect, github_fetcher,
)

MAX_SLICE = 12
MAX_PATHS = 80
MAX_DIFF_FILES = 299
RECENT_DAYS = 7
MAX_RUNTIME = 75
MAX_COMMIT_BYTES = 96 * 1024
MAX_PREFIX = 100
MAX_RENDERED_PATHS = 20


def commit_time(sha, token, deadline):
    """Read only the exact commit's committer timestamp, never free text."""
    _sha(sha)
    if time.monotonic() >= deadline:
        raise InventoryDenied("time_budget_exhausted")
    url = f"{API}/repos/{OWNER}/{REPO}/git/commits/{sha}"
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "zsentinel-recent-prless-ownership"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.build_opener(_DenyRedirect()).open(
                request, timeout=min(8, max(1, deadline - time.monotonic()))) as response:
            if response.status != 200:
                raise InventoryDenied("commit_source_unavailable")
            raw = response.read(MAX_COMMIT_BYTES + 1)
            if len(raw) > MAX_COMMIT_BYTES:
                raise InventoryDenied("commit_metadata_too_large")
        payload = json.loads(raw)
    except (OSError, urllib.error.HTTPError, TimeoutError, ValueError):
        raise InventoryDenied("commit_metadata_unavailable") from None
    if not isinstance(payload, dict) or payload.get("sha") != sha:
        raise InventoryDenied("commit_identity_mismatch")
    actor = payload.get("committer")
    stamp = actor.get("date") if isinstance(actor, dict) else None
    if not isinstance(stamp, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", stamp):
        raise InventoryDenied("commit_timestamp_missing")
    try:
        result = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise InventoryDenied("commit_timestamp_invalid") from None
    if result.utcoffset() is None:
        raise InventoryDenied("commit_timestamp_without_timezone")
    return result.astimezone(timezone.utc)


def branch_paths(fetch, base_sha, branch_sha):
    """Immutable base/head compare with conservative truncation rejection."""
    _sha(base_sha)
    _sha(branch_sha)
    payload, next_page = fetch(f"compare:{base_sha}...{branch_sha}", 0)
    if next_page is not None or not isinstance(payload, dict):
        raise InventoryDenied("incomplete_branch_compare")
    for key, expected in (("base_commit", base_sha), ("head_commit", branch_sha)):
        value = payload.get(key)
        if not isinstance(value, dict) or value.get("sha") != expected:
            raise InventoryDenied("branch_compare_identity_mismatch")
    commits = payload.get("total_commits")
    ahead = payload.get("ahead_by")
    history = payload.get("commits")
    if (type(commits) is not int or commits < 0 or commits > 250
            or type(ahead) is not int or ahead < 0 or ahead > 250
            or not isinstance(history, list) or len(history) != commits):
        raise InventoryDenied("branch_compare_history_incomplete")
    if any(not isinstance(r, dict) or not SHA.fullmatch(str(r.get("sha") or ""))
           for r in history):
        raise InventoryDenied("branch_compare_history_invalid")
    if len({r["sha"] for r in history}) != commits:
        raise InventoryDenied("duplicate_compare_commit")
    if history and history[-1]["sha"] != branch_sha:
        raise InventoryDenied("branch_compare_history_mismatch")
    if payload.get("status") not in ("ahead", "behind", "diverged", "identical"):
        raise InventoryDenied("branch_compare_status_invalid")
    files = payload.get("files")
    if not isinstance(files, list) or len(files) > MAX_DIFF_FILES:
        raise InventoryDenied("branch_diff_truncated")
    paths = set()
    for item in files:
        if not isinstance(item, dict) or item.get("status") not in (
                "added", "removed", "modified", "renamed", "copied",
                "changed", "unchanged"):
            raise InventoryDenied("branch_diff_untrusted")
        paths.add(_path(item.get("filename")))
        if item["status"] == "renamed":
            paths.add(_path(item.get("previous_filename")))
    # No need to assign ownership to a branch with no unmerged commits.
    if ahead == 0 and paths:
        raise InventoryDenied("branch_diff_without_ahead_commit")
    return ahead, sorted(paths)


def inventory(main_sha, *, offset=0, limit=MAX_SLICE, owned_paths=(),
              branch_prefix=None, token="", now=None):
    """Return one exact-index slice, never falsely report global coverage."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_SLICE:
        raise InventoryDenied("invalid_slice")
    if len(owned_paths) > MAX_PATHS:
        raise InventoryDenied("owned_paths_limit")
    owned = sorted({_path(x) for x in owned_paths})
    if branch_prefix is not None:
        if (not isinstance(branch_prefix, str) or not 1 <= len(branch_prefix) <= MAX_PREFIX
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch_prefix)
                or ".." in branch_prefix):
            raise InventoryDenied("invalid_branch_prefix")
    _sha(main_sha)
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise InventoryDenied("untrusted_clock")
    now = now.astimezone(timezone.utc)
    deadline = time.monotonic() + MAX_RUNTIME
    fetch = github_fetcher(token)
    snapshot = collect(fetch, main_sha)
    all_candidates = snapshot["pr_less"]
    candidates = ([r for r in all_candidates if r["branch"].startswith(branch_prefix)]
                  if branch_prefix else all_candidates)
    if offset >= len(candidates) and candidates:
        raise InventoryDenied("slice_outside_inventory")
    selected = candidates[offset:offset + limit]
    branch_evidence = []
    recent_cutoff = now - timedelta(days=RECENT_DAYS)
    for candidate in selected:
        if time.monotonic() >= deadline:
            raise InventoryDenied("time_budget_exhausted")
        sha = _sha(candidate["commit_sha"])
        stamp = commit_time(sha, token, deadline)
        if stamp > now + timedelta(minutes=5):
            raise InventoryDenied("future_commit_timestamp")
        # Old branches are historical evidence, not an automatic active claim.
        if stamp < recent_cutoff:
            branch_evidence.append({"ref": candidate["branch"], "sha": sha,
                                    "last_commit_at": stamp.isoformat(),
                                    "classification": "historical", "owner_proven": False})
            continue
        ahead, paths = branch_paths(fetch, main_sha, sha)
        conflicts = sorted({p for p in paths for owner in owned if _path_conflict(p, owner)})
        branch_evidence.append({
            "ref": candidate["branch"], "sha": sha,
            "last_commit_at": stamp.isoformat(),
            "classification": "recent_unmerged" if ahead else "not_ahead",
            "ahead_commits": ahead,
            "changed_paths": paths,
            "overlapping_paths": conflicts,
            "owner_proven": False,
        })
    # A stable branch/PR inventory (not just a stale initial page) is required.
    again = collect(fetch, main_sha)
    if (again["pr_less"] != all_candidates or again["branch_count"] != snapshot["branch_count"]
            or again["open_pr_head_count"] != snapshot["open_pr_head_count"]):
        raise InventoryDenied("post_scan_inventory_drift")
    # Prefix filtering cannot prove absence of overlap outside the namespace.
    coverage = (branch_prefix is None and offset == 0
                and len(selected) == len(all_candidates))
    return {
        "schema": "zsentinel-recent-prless-ownership-v1",
        "source": "github_readonly",
        "repository": f"{OWNER}/{REPO}",
        "main_sha": main_sha,
        "observed_at": now.isoformat(),
        "lookback_days": RECENT_DAYS,
        "snapshot_complete": True,
        "branch_inventory_count": snapshot["branch_count"],
        "prless_count": len(all_candidates),
        "matching_prefix_count": len(candidates),
        "branch_prefix": branch_prefix,
        "slice_offset": offset,
        "slice_count": len(selected),
        "coverage_complete": coverage,
        "may_admit_work": False,
        "ownership_granted": False,
        "release_authorized": False,
        "overlap_in_slice": any(r.get("overlapping_paths") for r in branch_evidence),
        "branches": branch_evidence,
    }



def markdown_report(result):
    """Bounded human-readable evidence, not authorization to claim work."""
    def safe(value):
        return (str(value).replace("\\", "\\\\").replace("|", "\\|")
                .replace("`", "\\`").replace("<", "&lt;").replace(">", "&gt;")
                .replace("[", "\\[").replace("]", "\\]"))

    lines = [
        "# zSentinel PR-less branch handoff", "",
        f"- Main SHA: `{result['main_sha']}`",
        f"- Observed (UTC): {result['observed_at']}",
        f"- GitHub PR-less branches: {result['prless_count']}",
        f"- Matching namespace: {result['matching_prefix_count']}",
        f"- Namespace: {safe(result.get('branch_prefix') or '(none)')}",
        f"- Slice: offset {result['slice_offset']}, {result['slice_count']} entries",
        f"- Global coverage: {'complete' if result['coverage_complete'] else 'PARTIAL'}",
        "- **No ownership, claim, merge or deployment authorization**", "",
        "| Branch | Last commit (UTC) | Status | Ahead | Overlaps |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in result["branches"]:
        conflicts = row.get("overlapping_paths") or []
        lines.append("| " + " | ".join((
            safe(row["ref"]), safe(row["last_commit_at"]),
            safe(row["classification"]), str(row.get("ahead_commits", "-")),
            str(len(conflicts)),
        )) + " |")
        if conflicts:
            lines.append(f"\nConflicts for `{safe(row['ref'])}`:")
            lines.extend(f"- `{safe(p)}`" for p in conflicts[:MAX_RENDERED_PATHS])
            if len(conflicts) > MAX_RENDERED_PATHS:
                lines.append(f"- … {len(conflicts)-MAX_RENDERED_PATHS} more paths omitted")
    lines.extend(("", "Advisory evidence only. Recheck Notion active claims, all",
                  "open PRs and all unscanned branches before editing. Partial",
                  "results **never** establish exclusive ownership."))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("main_sha", help="exact current canonical main SHA")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=MAX_SLICE)
    parser.add_argument("--owned-path", action="append", default=[])
    parser.add_argument("--branch-prefix", default=None,
                        help="select namespace; global coverage stays partial")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--deny-overlap", action="store_true",
                        help="fail on overlapping paths; incomplete inventory never clears admission")
    args = parser.parse_args()
    try:
        if args.deny_overlap and not args.owned_path:
            raise InventoryDenied("deny_overlap_requires_owned_path")
        result = inventory(args.main_sha, offset=args.offset, limit=args.limit,
                           owned_paths=args.owned_path, branch_prefix=args.branch_prefix,
                           token=os.environ.get("GITHUB_TOKEN", ""))
        if args.deny_overlap:
            # A slice is useful warning evidence, not an automatic global clear.
            result["admission_result"] = (
                "overlap_detected" if result["overlap_in_slice"] else
                "partial_inventory" if not result["coverage_complete"] else
                "no_overlap_observed_not_authorized"
            )
        if args.format == "markdown":
            print(markdown_report(result))
        else:
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        if args.deny_overlap and result["overlap_in_slice"]:
            return 3
        if args.deny_overlap and not result["coverage_complete"]:
            return 4
        return 0
    except InventoryDenied as exc:
        print(json.dumps({"schema": "zsentinel-recent-prless-ownership-v1",
                          "snapshot_complete": False, "reason": str(exc),
                          "ownership_granted": False, "may_admit_work": False,
                          "release_authorized": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

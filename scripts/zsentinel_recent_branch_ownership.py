#!/usr/bin/env python3
"""zSentinel UI adapter for the ALREADY INTEGRATED PR-less ownership source.

Do not implement a second GitHub branch/PR crawler here. Delegate inventory,
double-read consistency, immutable compares and rename handling to canonical
scripts/zcloud_recent_prless_ownership.py (zCloud PR #1554 / issue #870).
Neither that source nor this adapter grants actual ownership or runs on VPS.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import ROOT, canonical_projects, bounded_get
from zcloud_recent_prless_ownership import inventory as canonical_inventory
from zcloud_deploy_prless_inventory_20261013_w1 import InventoryDenied

SHA = re.compile(r"^[a-f0-9]{40}$")
UTC = timezone.utc


def _main_sha(token):
    """Fetch pinned main revision; all subsequent comparisons use this SHA."""
    ref = bounded_get("https://api.github.com/repos/Zennay/zCloud/git/ref/heads/main", token)
    ref_obj = ref.get("object") if isinstance(ref, dict) else None
    sha = ref_obj.get("sha") if isinstance(ref_obj, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise InventoryDenied("canonical_main_ref_unavailable")
    return sha


def inventory(repo, token=None, prefix=None, current_paths=None, now=None):
    """Read and normalize canonical PR-less evidence for the offline cockpit.

    The canonical collector has stronger completeness and stability checks
    than a second ad-hoc branch listing, including post-scan double reads.
    """
    if repo != "Zennay/zCloud":
        raise InventoryDenied("canonical_owner_source_only_zcloud")
    current_paths = [] if current_paths is None else current_paths
    if not isinstance(current_paths, list) or len(current_paths) > 25:
        raise InventoryDenied("invalid_current_path_inventory")
    observed = now or datetime.now(UTC)
    if not isinstance(observed, datetime) or observed.utcoffset() is None:
        raise InventoryDenied("invalid_observation_time")
    observed = observed.astimezone(UTC)
    pinned = _main_sha(token)
    evidence = canonical_inventory(
        pinned, owned_paths=current_paths, branch_prefix=prefix,
        token=token or "", now=observed)
    if (evidence.get("schema") != "zsentinel-recent-prless-ownership-v1"
            or evidence.get("repository") != repo
            or evidence.get("main_sha") != pinned
            or evidence.get("snapshot_complete") is not True
            or evidence.get("may_admit_work") is not False
            or evidence.get("ownership_granted") is not False
            or evidence.get("release_authorized") is not False):
        raise InventoryDenied("invalid_canonical_branch_evidence")
    if _main_sha(token) != pinned:
        raise InventoryDenied("main_moved_during_branch_inventory")
    rows = []
    historical = 0
    for item in evidence.get("branches", []):
        if not isinstance(item, dict):
            raise InventoryDenied("invalid_canonical_branch_row")
        kind = item.get("classification")
        if kind == "historical":
            historical += 1
            continue
        if kind != "recent_unmerged":
            continue
        sha = item.get("sha")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise InventoryDenied("invalid_exact_branch_sha")
        timestamp = datetime.fromisoformat(item["last_commit_at"].replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise InventoryDenied("branch_clock_without_timezone")
        age = max(0, int((observed-timestamp.astimezone(UTC)).total_seconds() // 3600))
        paths = item.get("changed_paths")
        conflicts = item.get("overlapping_paths")
        if (not isinstance(paths, list) or not isinstance(conflicts, list)
                or len(paths) > 299 or len(conflicts) > 299):
            raise InventoryDenied("unbounded_canonical_file_paths")
        rows.append({
            "branch": item["ref"], "head_sha": sha, "main_sha": pinned,
            "commit_age_hours": age, "ahead_by": item["ahead_commits"],
            "paths": paths, "path_count": len(paths),
            "overlapping_paths": conflicts,
            "potential_overlap": bool(conflicts),
            "owner_verified": False, "claim_authorized": False,
        })
    return {
        "schema": "zsentinel-recent-unpr-branches-v1",
        "source": "canonical_zcloud_recent_prless_ownership_v1",
        "repository": repo, "main_sha": pinned,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "recent_window_hours": 24 * evidence["lookback_days"],
        "coverage": ("bounded_complete" if evidence["coverage_complete"]
                     else "incomplete_owner_review"),
        "sampled_unpr_branches": evidence["slice_count"],
        "historical_in_sample": historical,
        "current_paths_known": bool(current_paths),
        "current_slice_safe_to_claim": False,
        "recent_branch_slice_no_overlap_observed": (
            evidence["coverage_complete"] and bool(current_paths)
            and not evidence["overlap_in_slice"]),
        "recent_candidate_count": len(rows),
        "recent_candidates": rows,
        "potential_overlap_observed": bool(evidence["overlap_in_slice"]),
        "branches_deleted": False, "worker_claims_changed": False,
        "workflows_retried": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Canonical read-only un-PR owner evidence")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", required=True)
    parser.add_argument("--branch-prefix")
    parser.add_argument("--current-path", action="append", default=[])
    parser.add_argument("--require-clear-slice", action="store_true")
    args = parser.parse_args()
    try:
        projects = canonical_projects(args.registry)
        if args.project not in projects:
            raise InventoryDenied("unknown_canonical_project")
        source = inventory(projects[args.project], os.environ.get("GITHUB_TOKEN"),
                           args.branch_prefix, args.current_path)
        if args.require_clear_slice and not source["recent_branch_slice_no_overlap_observed"]:
            raise InventoryDenied("recent_branch_slice_incomplete_or_overlap")
        print(json.dumps(source, sort_keys=True, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, "zSentinel canonical branch source unavailable: " +
                    str(exc)[:120] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

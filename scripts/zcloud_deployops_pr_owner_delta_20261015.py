#!/usr/bin/env python3
"""Compare two unverified, complete PR owner snapshots without changing GitHub.

This is an advisory *delta*, not a merge, release, deployment, branch-ownership
or production admission decision. Both inputs MUST describe the same main SHA.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from scripts.zcloud_deployops_pr_owner_collision_20261015 import (
    InvalidInventory, assess, read_snapshot, unique_json,
)

REPORT_LIMIT = 50


def _report(values):
    """Bound all operator-visible lists while retaining exact counts."""
    values = sorted(values)
    return {"count": len(values), "items": values[:REPORT_LIMIT],
            "truncated": len(values) > REPORT_LIMIT}


def _owners(snapshot):
    result = defaultdict(set)
    for pr in snapshot["pulls"]:
        for item in pr["files"]:
            result[item["filename"]].add(pr["number"])
            if item["status"] == "renamed":
                result[item["previous_filename"]].add(pr["number"])
    return {path: frozenset(ids) for path, ids in result.items()}


def _hierarchy(owners):
    """Exact parent COMPONENT boundaries, not misleading string prefixes."""
    result = {}
    for path, descendants in owners.items():
        for position, char in enumerate(path):
            if char != "/":
                continue
            ancestor = path[:position]
            ancestors = owners.get(ancestor)
            if ancestors is None:
                continue
            group = ancestors | descendants
            if len(group) > 1:
                result[(ancestor, path)] = group
    return result


def compare_snapshots(previous, current):
    """Require two independently valid full inventories on the *same* main."""
    before = assess(previous)
    after = assess(current)
    if before["main_sha"] != after["main_sha"]:
        raise InvalidInventory("different_main_revisions_no_safe_comparison")

    before_prs = {pr["number"]: pr for pr in previous["pulls"]}
    after_prs = {pr["number"]: pr for pr in current["pulls"]}
    old_ids, new_ids = set(before_prs), set(after_prs)
    changed_ids = {n for n in old_ids & new_ids
                   if before_prs[n] != after_prs[n]}

    old_owners, new_owners = _owners(previous), _owners(current)
    paths_changed = {path for path in old_owners.keys() | new_owners.keys()
                     if old_owners.get(path) != new_owners.get(path)}
    old_exact = {path for path, ids in old_owners.items() if len(ids) > 1}
    new_exact = {path for path, ids in new_owners.items() if len(ids) > 1}

    old_hierarchy, new_hierarchy = _hierarchy(old_owners), _hierarchy(new_owners)
    # A hierarchy edge is identified by both paths and its full owner set:
    # an unchanged collision count must not mask a different conflicting PR.
    changed_hierarchy_edges = {
        edge for edge in old_hierarchy.keys() | new_hierarchy.keys()
        if old_hierarchy.get(edge) != new_hierarchy.get(edge)
    }
    # Component keys are already validated Git paths and cannot contain " -> ".
    edge_names = lambda edges: [ancestor + " -> " + child
                                for ancestor, child in edges]

    # An evidence change that leaves the semantic path-owner graph intact
    # (for example API PR ordering) is still a changed UNVERIFIED snapshot.
    # Never label nonidentical digest evidence as a stable inventory.
    modified = bool(
        before["snapshot_sha256"] != after["snapshot_sha256"]
        or old_ids != new_ids or changed_ids or paths_changed
        or changed_hierarchy_edges
    )
    return {
        "policy": "zcloud-deployops-pr-owner-snapshot-delta-v1",
        "repo": after["repo"],
        "main_sha": after["main_sha"],
        "previous_snapshot_sha256": before["snapshot_sha256"],
        "current_snapshot_sha256": after["snapshot_sha256"],
        "previous_pr_count": before["prs_scanned"],
        "current_pr_count": after["prs_scanned"],
        "added_prs": _report(new_ids - old_ids),
        "removed_prs": _report(old_ids - new_ids),
        "changed_prs": _report(changed_ids),
        "changed_path_ownership": _report(paths_changed),
        "new_exact_collision_paths": _report(new_exact - old_exact),
        "resolved_exact_collision_paths": _report(old_exact - new_exact),
        "changed_hierarchy_conflicts": _report(
            edge_names(changed_hierarchy_edges)),
        "previous_exact_collision_count": before["collision_count"],
        "current_exact_collision_count": after["collision_count"],
        "previous_hierarchy_collision_count": before["hierarchy_collision_count"],
        "current_hierarchy_collision_count": after["hierarchy_collision_count"],
        "inventory_changed": modified,
        "status": "changed" if modified else "unchanged",
        "input_provenance": "two_unverified_snapshots",
        "includes_pr_less_branches": False,
        "operator_review_required": True,
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        before = unique_json(read_snapshot(args.baseline))
        after = unique_json(read_snapshot(args.snapshot))
        result = compare_snapshots(before, after)
    except (InvalidInventory, OSError, UnicodeError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, InvalidInventory) else "invalid_snapshot"
        print(json.dumps({
            "status": "refused", "reason": reason,
            "operator_review_required": True,
            "release_authorized": False, "deployment_authorized": False,
            "mutation_performed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1 if result["inventory_changed"] else 0


if __name__ == "__main__":
    sys.exit(main())

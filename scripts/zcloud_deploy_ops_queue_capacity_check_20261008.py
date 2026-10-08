#!/usr/bin/env python3
"""Offline, non-authorizing deploy-ops inventory consistency check.

Input must be a caller-collected JSON snapshot. This never queries GitHub or
authorizes a merge/deploy; missing or partial inventories always STOP.
"""
import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"^[a-f0-9]{40}$")


def evaluate(snapshot):
    stop = []
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "main_sha", "candidate", "open_prs", "pr_less_branches",
        "open_prs_complete", "branches_complete", "serialized_gate_released"
    }:
        return {"decision": "STOP", "reasons": ["invalid_schema"], "merge_authorized": False, "deploy_authorized": False}
    candidate = snapshot["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != {"head_sha", "paths", "owner", "checks_green_for_head"}:
        stop.append("invalid_candidate")
        candidate = {}
    if not isinstance(snapshot["main_sha"], str) or not SHA.fullmatch(snapshot["main_sha"]):
        stop.append("invalid_main_sha")
    if not isinstance(candidate.get("head_sha"), str) or not SHA.fullmatch(candidate["head_sha"]):
        stop.append("invalid_head_sha")
    if not isinstance(candidate.get("owner"), str) or not candidate["owner"].strip():
        stop.append("missing_owner")
    paths = candidate.get("paths")
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths) or len(paths) != len(set(paths)):
        stop.append("invalid_paths")
        paths = []
    if candidate.get("checks_green_for_head") is not True:
        stop.append("missing_exact_head_checks")
    if snapshot["open_prs_complete"] is not True or snapshot["branches_complete"] is not True:
        stop.append("partial_inventory")
    if snapshot["serialized_gate_released"] is not True:
        stop.append("serialized_gate_held")
    for category in ("open_prs", "pr_less_branches"):
        items = snapshot[category]
        if not isinstance(items, list):
            stop.append("invalid_" + category)
            continue
        for item in items:
            if not isinstance(item, dict) or set(item) != {"owner", "paths"} or not isinstance(item["owner"], str) or not item["owner"].strip() or not isinstance(item["paths"], list) or any(not isinstance(p, str) for p in item["paths"]):
                stop.append("invalid_inventory_item")
                continue
            if set(paths).intersection(item["paths"]):
                stop.append("path_overlap")
    return {"decision": "STOP" if stop else "REVIEW_ONLY", "reasons": sorted(set(stop)), "merge_authorized": False, "deploy_authorized": False}


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage")
        path = Path(sys.argv[1])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError("unsafe_file")
        with path.open(encoding="utf-8") as stream:
            snapshot = json.load(stream, object_pairs_hook=_reject_duplicates)
        result = evaluate(snapshot)
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        result = {"decision": "STOP", "reasons": ["invalid_input"], "merge_authorized": False, "deploy_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["decision"] == "REVIEW_ONLY" else 1


def _reject_duplicates(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError("duplicate key")
        obj[k] = v
    return obj


if __name__ == "__main__":
    raise SystemExit(main())

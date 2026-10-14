#!/usr/bin/env python3
"""Read-only, fail-closed deploy-ops gate check for a bounded canonical snapshot.

This checker never fetches GitHub or changes repository/runtime state.
The caller must collect a complete fresh snapshot from canonical sources.
"""
import argparse
import datetime as dt
import json
import sys

REQUIRED = frozenset(("#580", "#1089"))
MAX_AGE_SECONDS = 300

def assess(snapshot, *, now, expected_main_sha=None):
    if not isinstance(snapshot, dict):
        return "incomplete", "snapshot_not_object"
    if snapshot.get("repository") != "Zennay/zCloud":
        return "incomplete", "repository_mismatch"
    sha = snapshot.get("main_sha")
    if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        return "incomplete", "invalid_main_sha"
    if expected_main_sha is not None and sha != expected_main_sha:
        return "incomplete", "main_moved_since_collection"
    if snapshot.get("inventory_complete") is not True:
        return "incomplete", "inventory_not_complete"
    try:
        collected = dt.datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00"))
        if collected.tzinfo is None:
            raise ValueError("naive timestamp")
        age = (now - collected).total_seconds()
    except (ValueError, TypeError, KeyError, AttributeError):
        return "incomplete", "invalid_collected_at"
    if not 0 <= age <= MAX_AGE_SECONDS:
        return "incomplete", "snapshot_stale_or_future"
    entries = snapshot.get("gates")
    if not isinstance(entries, list) or len(entries) != len(REQUIRED):
        return "incomplete", "gate_inventory_cardinality"
    by_id = {}
    for gate in entries:
        if not isinstance(gate, dict) or gate.get("id") not in REQUIRED or gate["id"] in by_id:
            return "incomplete", "missing_unknown_or_duplicate_gate"
        if gate.get("status") not in ("active", "released"):
            return "incomplete", "unrecognized_gate_status"
        if gate.get("verified_main_sha") != sha:
            return "incomplete", "gate_main_sha_mismatch"
        by_id[gate["id"]] = gate
    if set(by_id) != REQUIRED:
        return "incomplete", "required_gate_missing"
    if any(g["status"] == "active" for g in by_id.values()):
        return "blocked", "serialized_owner_active"
    if snapshot.get("other_serialized_owners_clear") is not True:
        return "incomplete", "other_owner_inventory_not_clear"
    return "clear", "all_serialized_owners_released"

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", help="Local JSON snapshot; no network access")
    parser.add_argument("--now", help="UTC timestamp for reproducible tests")
    parser.add_argument("--expected-main-sha", required=True, help="Separately verified canonical main revision")
    args = parser.parse_args(argv)
    try:
        with open(args.snapshot, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        now = dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else dt.datetime.now(dt.timezone.utc)
        status, reason = assess(snapshot, now=now, expected_main_sha=args.expected_main_sha)
    except (OSError, ValueError, TypeError) as exc:
        status, reason = "incomplete", "unreadable_or_invalid_snapshot"
    print(json.dumps({"status": status, "reason": reason}, sort_keys=True))
    return 0 if status == "clear" else 2

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Offline, fail-closed verification of a captured serialized deploy gate.

This tool never contacts GitHub and cannot authorize a deploy. Supply a JSON
snapshot captured by a trusted operator; then independently verify freshness.
"""
import argparse
import datetime as dt
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
GATES = (580, 1089)


def assess(data, now, max_age_seconds=300):
    reasons = []
    if not isinstance(data, dict):
        return {"eligible_for_review": False, "reasons": ["invalid_snapshot"]}
    if data.get("repository") != "Zennay/zCloud":
        reasons.append("repository_mismatch")
    head = data.get("main_sha")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        reasons.append("invalid_main_sha")
    try:
        stamped = dt.datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
        if stamped.tzinfo is None:
            raise ValueError("naive timestamp")
        age = (now - stamped).total_seconds()
        if age < 0 or age > max_age_seconds:
            reasons.append("snapshot_not_fresh")
    except (KeyError, AttributeError, TypeError, ValueError):
        reasons.append("invalid_capture_time")
    gates = data.get("gates")
    if not isinstance(gates, list) or len(gates) != 2:
        reasons.append("incomplete_gate_inventory")
    else:
        ids = [x.get("number") for x in gates if isinstance(x, dict)]
        if sorted(ids, key=str) != list(GATES):
            reasons.append("unexpected_gate_inventory")
        for gate in gates:
            if not isinstance(gate, dict):
                reasons.append("invalid_gate_entry")
                continue
            if gate.get("state") not in ("closed", "merged"):
                reasons.append("gate_still_open_or_unknown")
            if gate.get("release_confirmed") is not True:
                reasons.append("release_not_confirmed")
    # Gate closure alone is never merge/deploy authorization.
    return {
        "eligible_for_review": not reasons,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
        "reasons": sorted(set(reasons)),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", help="Local JSON gate snapshot")
    p.add_argument("--max-age-seconds", type=int, default=300)
    args = p.parse_args()
    if args.max_age_seconds < 1 or args.max_age_seconds > 3600:
        p.error("max-age-seconds must be between 1 and 3600")
    try:
        with open(args.snapshot, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"eligible_for_review": False, "error": type(exc).__name__}))
        return 2
    result = assess(data, dt.datetime.now(dt.timezone.utc), args.max_age_seconds)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["eligible_for_review"] else 1


if __name__ == "__main__":
    sys.exit(main())

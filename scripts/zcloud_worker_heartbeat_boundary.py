#!/usr/bin/env python3
"""Offline, deny-only check of purported worker heartbeat evidence.

No network, SQLite, browser, or service access.  Not an authorization gate.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

MAX_AGE_SECONDS = 180

def validate(record, now):
    reasons = []
    if not isinstance(record, dict):
        return {"trusted": False, "reasons": ["not_an_object"]}
    if record.get("source") != "vps_sqlite_observed":
        reasons.append("untrusted_source")
    if record.get("kind") != "worker_generation_heartbeat":
        reasons.append("wrong_kind")
    for key in ("worker_id", "assignment_id", "generation_id"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            reasons.append("invalid_" + key)
    stamp = record.get("observed_at")
    try:
        if not isinstance(stamp, str) or not stamp.endswith("Z"):
            raise ValueError("UTC Z required")
        observed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if observed.utcoffset() != dt.timedelta(0):
            raise ValueError("UTC required")
        age = (now - observed).total_seconds()
        if age < 0:
            reasons.append("future_observation")
        elif age > MAX_AGE_SECONDS:
            reasons.append("stale_observation")
    except (ValueError, TypeError, OverflowError):
        reasons.append("invalid_observed_at")
    if record.get("generation_started") is not True:
        reasons.append("generation_not_proven")
    return {"trusted": not reasons, "reasons": reasons, "mutation_performed": False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        result = validate(record, now)
    except (OSError, json.JSONDecodeError, UnicodeError):
        result = {"trusted": False, "reasons": ["invalid_input"], "mutation_performed": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["trusted"] else 1

if __name__ == "__main__":
    raise SystemExit(main())

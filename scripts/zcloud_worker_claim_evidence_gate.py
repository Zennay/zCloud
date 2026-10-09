#!/usr/bin/env python3
"""Fail-closed evidence gate for claims that a portfolio worker is actually running.

Consumes a JSON snapshot supplied by a trusted collector. No network, SQLite,
browser, queue or runtime mutations are performed.
"""
import argparse
import datetime as dt
import json
import sys

UTC = dt.timezone.utc


def parse_time(value):
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp required")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(UTC)


def assess(snapshot, *, now, max_age_seconds=300):
    """A Notion claim alone must never be reported as live generation."""
    if not isinstance(snapshot, dict) or max_age_seconds <= 0:
        raise ValueError("invalid input")
    required = ("assignment_id", "worker_id", "generation_started_at",
                "heartbeat_at", "source")
    if not all(isinstance(snapshot.get(k), str) and snapshot[k].strip()
               for k in required):
        return {"state": "unverified", "reason": "missing_runtime_evidence", "mutation_performed": False}
    if snapshot["source"] != "trusted_runtime":
        return {"state": "unverified", "reason": "untrusted_source", "mutation_performed": False}
    try:
        started = parse_time(snapshot["generation_started_at"])
        heartbeat = parse_time(snapshot["heartbeat_at"])
    except (ValueError, TypeError, OverflowError):
        return {"state": "unverified", "reason": "invalid_timestamp", "mutation_performed": False}
    if started > heartbeat or heartbeat > now or started > now:
        return {"state": "unverified", "reason": "inconsistent_timeline", "mutation_performed": False}
    age = (now - heartbeat).total_seconds()
    if age > max_age_seconds:
        return {"state": "stale", "reason": "heartbeat_expired", "mutation_performed": False}
    return {"state": "generation_observed", "reason": "fresh_runtime_heartbeat",
            "mutation_performed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file with trusted runtime evidence")
    parser.add_argument("--now", help="ISO-8601 timestamp; defaults to UTC clock")
    parser.add_argument("--max-age-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        with open(args.snapshot, encoding="utf-8") as handle:
            data = json.load(handle)
        current = parse_time(args.now) if args.now else dt.datetime.now(UTC)
        result = assess(data, now=current, max_age_seconds=args.max_age_seconds)
    except (OSError, ValueError, TypeError) as error:
        result = {"state": "unverified", "reason": "invalid_input", "mutation_performed": False}
        print(f"evidence error: {error}", file=sys.stderr)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "generation_observed" else 1


if __name__ == "__main__":
    sys.exit(main())

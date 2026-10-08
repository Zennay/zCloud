#!/usr/bin/env python3
"""Offline advisory clock-skew check for deploy evidence timestamps.

Never authorizes deployments: even a passing check is only a temporal
consistency signal, not provenance, ownership or production admission.
"""
import argparse
import datetime as dt
import json
import sys

UTC = dt.timezone.utc

def parse_utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("UTC_Z_REQUIRED")
    parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != UTC:
        raise ValueError("UTC_Z_REQUIRED")
    return parsed

def evaluate(receipt, *, now, max_age_seconds=900, future_skew_seconds=60):
    output = {"clock_consistent": False, "reason": "INVALID_RECEIPT",
              "deploy_authorized": False, "merge_authorized": False,
              "mutation_performed": False}
    if type(receipt) is not dict or set(receipt) != {"observed_at"}:
        return output
    if not 0 <= max_age_seconds <= 86400 or not 0 <= future_skew_seconds <= 300:
        return output
    try:
        timestamp = parse_utc(receipt["observed_at"])
        if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
            return output
        age = (now - timestamp).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return output
    if age < -future_skew_seconds:
        output["reason"] = "FUTURE_TIMESTAMP"
    elif age > max_age_seconds:
        output["reason"] = "STALE_TIMESTAMP"
    else:
        output["clock_consistent"] = True
        output["reason"] = "CLOCK_CONSISTENT_ONLY"
    return output

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", help="Local JSON file, never fetched remotely")
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument("--future-skew-seconds", type=int, default=60)
    args = parser.parse_args()
    try:
        with open(args.receipt, encoding="utf-8") as stream:
            data = json.load(stream)
        result = evaluate(data, now=dt.datetime.now(UTC),
                          max_age_seconds=args.max_age_seconds,
                          future_skew_seconds=args.future_skew_seconds)
    except (OSError, ValueError, UnicodeError):
        result = evaluate(None, now=dt.datetime.now(UTC))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["clock_consistent"] else 1

if __name__ == "__main__":
    sys.exit(main())

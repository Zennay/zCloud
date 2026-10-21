#!/usr/bin/env python3
"""Read-only, fail-closed comparison of two locally supplied deploy evidence receipts.

This is a drift diagnostic, never deployment/merge authorization.
"""
import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
FIELDS = ("candidate_sha", "deployed_sha", "main_sha", "workflow_run_id", "runner_name")

def compare(previous, current):
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ValueError("receipts must be JSON objects")
    for name, receipt in (("previous", previous), ("current", current)):
        for field in FIELDS:
            if field not in receipt or isinstance(receipt[field], (dict, list, bool)) or not str(receipt[field]).strip():
                raise ValueError(f"{name}: missing or malformed {field}")
        for field in ("candidate_sha", "deployed_sha", "main_sha"):
            if not isinstance(receipt[field], str) or not SHA.fullmatch(receipt[field]):
                raise ValueError(f"{name}: invalid {field}")
        if not isinstance(receipt["workflow_run_id"], int) or receipt["workflow_run_id"] <= 0:
            raise ValueError(f"{name}: invalid workflow_run_id")
        if not isinstance(receipt["runner_name"], str) or not receipt["runner_name"].strip():
            raise ValueError(f"{name}: invalid runner_name")
    changed = [field for field in FIELDS if previous[field] != current[field]]
    return {
        "evidence_drift_detected": bool(changed),
        "changed_fields": changed,
        "release_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", help="previous receipt JSON file")
    parser.add_argument("current", help="current receipt JSON file")
    args = parser.parse_args(argv)
    try:
        with open(args.previous, encoding="utf-8") as file:
            previous = json.load(file)
        with open(args.current, encoding="utf-8") as file:
            current = json.load(file)
        result = compare(previous, current)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["evidence_drift_detected"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error), "evidence_drift_detected": True, "release_authorized": False, "merge_authorized": False, "deploy_authorized": False, "mutation_performed": False}), file=sys.stdout)
        return 2

if __name__ == "__main__":
    sys.exit(main())

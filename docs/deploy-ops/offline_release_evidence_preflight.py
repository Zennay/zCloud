#!/usr/bin/env python3
"""Offline, fail-closed zCloud release-evidence consistency check.

This is an advisory preflight only: it cannot establish live GitHub provenance,
current main freshness, production authorization, or worker activation.
"""
import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = (
    "repository", "main_sha", "run_head_sha", "workflow_run_id",
    "run_status", "run_conclusion", "run_event", "expected_event",
    "trusted_origin", "owner_confirmed", "environment",
)

def validate(item):
    if not isinstance(item, dict):
        return ["evidence must be an object"]
    errors = [f"missing {key}" for key in REQUIRED if key not in item]
    if item.get("repository") != "Zennay/zCloud":
        errors.append("repository mismatch")
    for key in ("main_sha", "run_head_sha"):
        if not isinstance(item.get(key), str) or not SHA.fullmatch(item[key]):
            errors.append(f"invalid {key}")
    if item.get("main_sha") != item.get("run_head_sha"):
        errors.append("stale workflow head")
    if type(item.get("workflow_run_id")) is not int or item["workflow_run_id"] <= 0:
        errors.append("invalid workflow_run_id")
    if item.get("run_status") != "completed" or item.get("run_conclusion") != "success":
        errors.append("workflow not terminal-success")
    if not item.get("expected_event") or item.get("run_event") != item.get("expected_event"):
        errors.append("event mismatch")
    if item.get("trusted_origin") is not True:
        errors.append("untrusted origin")
    if item.get("owner_confirmed") is not True:
        errors.append("serialized owner not confirmed")
    if item.get("environment") != "production":
        errors.append("environment not production")
    # A document-only check must never pronounce live deployment approved.
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_json", help="Local JSON file; no network access")
    args = parser.parse_args()
    try:
        with open(args.evidence_json, encoding="utf-8") as fp:
            evidence = json.load(fp)
    except (OSError, ValueError) as exc:
        print(f"REJECT: cannot read evidence: {type(exc).__name__}", file=sys.stderr)
        return 2
    errors = validate(evidence)
    if errors:
        for err in errors:
            print(f"REJECT: {err}", file=sys.stderr)
        return 1
    print("CONSISTENT ONLY: independently verify live main, provenance, owner and receipts; NOT deploy approval")
    return 0

if __name__ == "__main__":
    sys.exit(main())

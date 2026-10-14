#!/usr/bin/env python3
"""Pure, fail-closed validation of a deploy-ops evidence envelope.

This tool cannot dispatch workflows, merge branches, or authorize deployment.
It accepts machine-generated JSON with exact revision/evidence identifiers.
"""
import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = ("main_sha", "candidate_base_sha", "candidate_head_sha",
            "validated_head_sha", "post_ci_main_sha")
CHECKS = ("regression", "cpu", "dashboard", "planner", "batch_preflight", "immutable_intent")


def assess(payload):
    if not isinstance(payload, dict):
        return ["invalid_payload"]
    failures = []
    for field in REQUIRED:
        if not isinstance(payload.get(field), str) or not SHA.fullmatch(payload[field]):
            failures.append("invalid_" + field)
    if failures:
        return failures
    if payload["candidate_base_sha"] != payload["main_sha"]:
        failures.append("candidate_base_mismatch")
    if payload["post_ci_main_sha"] != payload["main_sha"]:
        failures.append("main_drift")
    if payload["validated_head_sha"] != payload["candidate_head_sha"]:
        failures.append("head_drift")
    evidence = payload.get("checks")
    if not isinstance(evidence, dict):
        return failures + ["missing_checks"]
    for check in CHECKS:
        entry = evidence.get(check)
        if (not isinstance(entry, dict)
                or entry.get("conclusion") != "success"
                or entry.get("head_sha") != payload["candidate_head_sha"]
                or type(entry.get("run_id")) is not int
                or entry["run_id"] <= 0):
            failures.append("invalid_check_" + check)
    # The reader should never treat a passing evidence assessment as release authority.
    if payload.get("deploy_authorized") is not False:
        failures.append("non_authorizing_envelope_required")
    if payload.get("merge_authorized") is not False:
        failures.append("merge_not_authorized")
    if payload.get("mutation_performed") is not False:
        failures.append("mutation_flag_invalid")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", help="Path to bounded JSON evidence envelope")
    args = parser.parse_args()
    try:
        with open(args.evidence, encoding="utf-8") as handle:
            payload = json.load(handle)
        failures = assess(payload)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "reject", "reasons": ["unreadable_evidence"]}))
        return 2
    print(json.dumps({"status": "evidence_consistent" if not failures else "reject",
                      "release_authorized": False, "mutation_performed": False,
                      "reasons": failures}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

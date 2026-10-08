#!/usr/bin/env python3
"""Offline, fail-closed deploy-recovery evidence verifier.

Reads a JSON evidence bundle; never invokes shell, GitHub, network or VPS operations.
This is an *advisory* eligibility check, not deploy/recovery authorization.
"""
import argparse
import json
import re
import sys

REQUIRED_TRUE = (
    "inventory_complete",
    "exact_main_sha",
    "canonical_repository",
    "trusted_actor",
    "permanent_runner_verified",
    "pr_trigger_zero_mutation",
    "external_health_probed",
    "dashboard_unhealthy_confirmed",
    "independent_outage_corroborated",
    "gate_580_released",
    "gate_1089_released",
    "no_competing_writer",
    "rollback_ready",
    "bounded_recovery_plan",
    "receipt_plan_ready",
)
REQUIRED_STRINGS = ("main_sha", "candidate_sha", "evidence_timestamp_utc", "reviewer")


def evaluate(data):
    if not isinstance(data, dict):
        return ["bundle_not_object"]
    reasons = []
    for key in REQUIRED_TRUE:
        if data.get(key) is not True:
            reasons.append(f"missing_or_false:{key}")
    for key in REQUIRED_STRINGS:
        if not isinstance(data.get(key), str) or not data[key].strip():
            reasons.append(f"missing:{key}")
    if data.get("trigger") != "workflow_dispatch":
        reasons.append("trigger_not_manual_dispatch")
    if data.get("ref") != "refs/heads/main":
        reasons.append("not_main_ref")
    for key in ("main_sha", "candidate_sha"):
        if not isinstance(data.get(key), str) or re.fullmatch(r"[0-9a-fA-F]{40}", data[key]) is None:
            reasons.append(f"invalid_git_sha:{key}")
    if data.get("main_sha") != data.get("candidate_sha"):
        reasons.append("stale_candidate_sha")
    if data.get("dashboard_healthy") is not False:
        reasons.append("healthy_or_unknown_dashboard")
    return reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", help="offline JSON evidence bundle")
    args = parser.parse_args(argv)
    try:
        with open(args.evidence, encoding="utf-8") as f:
            bundle = json.load(f)
        reasons = evaluate(bundle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"eligible": False, "reason": "invalid_evidence_file"}))
        return 2
    print(json.dumps({"eligible": not bool(reasons), "reasons": reasons}, sort_keys=True))
    return 0 if not reasons else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Classify GitHub Actions dashboard recovery job outcomes without deployment authority.

Input is an offline JSON object: {"jobs": [{"name": "...", "conclusion": "..."}]}.
No GitHub API calls, privileged repair, or secret-bearing log parsing.
"""
import argparse
import json
import sys

OUTCOMES = {"success", "failure", "cancelled", "skipped", "timed_out", "neutral", "action_required"}

def classify(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("jobs must be an array")
    observations = {}
    for job in payload["jobs"]:
        if not isinstance(job, dict):
            raise ValueError("job entries must be objects")
        name, conclusion = job.get("name"), job.get("conclusion")
        if name not in ("recover", "external_verify"):
            continue
        if not isinstance(conclusion, str) or conclusion not in OUTCOMES:
            raise ValueError("missing or invalid conclusion for monitored job")
        if name in observations:
            raise ValueError("duplicate monitored job")
        observations[name] = conclusion
    recovery = observations.get("recover")
    external = observations.get("external_verify")
    if recovery is None or external is None:
        status = "incomplete_evidence"
    elif recovery == "failure" and external == "success":
        status = "recovery_failed_external_healthy"
    elif recovery == "success" and external == "success":
        status = "both_checks_succeeded_not_deploy_authority"
    elif external == "failure":
        status = "external_check_failed_not_proof_of_outage"
    else:
        status = "indeterminate"
    return {
        "schema_version": 1,
        "status": status,
        "observed_jobs": observations,
        "deploy_authorized": False,
        "recovery_authorized": False,
        "mutation_performed": False,
    }

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_snapshot", help="Offline GitHub job summary JSON file")
    args = parser.parse_args(argv)
    try:
        with open(args.job_snapshot, encoding="utf-8") as handle:
            result = classify(json.load(handle))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "deploy_authorized": False,
                          "recovery_authorized": False, "mutation_performed": False}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "incomplete_evidence" else 2

if __name__ == "__main__":
    raise SystemExit(main())

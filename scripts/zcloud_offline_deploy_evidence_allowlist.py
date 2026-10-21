#!/usr/bin/env python3
"""Fail-closed publisher for caller-supplied *offline* deployment check results.

No network calls, production access, or authorization decisions. Only explicit
literal allowlisted values leave this module. Never echo failed input values.
"""
import json
import re
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID = re.compile(r"[1-9][0-9]{0,17}\Z")
CHECK = frozenset({"offline_deploy_evidence_privacy", "postdeploy_receipt", "deploy_receipt_drift"})
STATUS = frozenset({"pass", "needs_review", "fail"})
REASON = frozenset({"NONE", "UNREVIEWED_OUTPUT_FIELD", "INVALID_EVIDENCE", "IDENTITY_MISMATCH", "MISSING_PROOF"})

def sanitize(value):
    if not isinstance(value, dict):
        raise ValueError("invalid evidence")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("invalid evidence")
    if value.get("check") not in CHECK or value.get("status") not in STATUS or value.get("reason_code") not in REASON:
        raise ValueError("invalid evidence")
    # Forbid any unknown fields rather than silently forwarding potentially
    # sensitive fields through future refactors.
    allowed = {"schema_version", "check", "status", "reason_code", "candidate_sha", "deployed_sha", "run_id", "changed_field_count"}
    if set(value) - allowed:
        raise ValueError("unreviewed evidence fields")
    result = {key: value[key] for key in ("schema_version", "check", "status", "reason_code")}
    for key in ("candidate_sha", "deployed_sha"):
        if key in value:
            if not isinstance(value[key], str) or not SHA.fullmatch(value[key]):
                raise ValueError("invalid evidence")
            result[key] = value[key]
    if "run_id" in value:
        if type(value["run_id"]) is not int or not RUN_ID.fullmatch(str(value["run_id"])):
            raise ValueError("invalid evidence")
        result["run_id"] = value["run_id"]
    if "changed_field_count" in value:
        n = value["changed_field_count"]
        if type(n) is not int or not 0 <= n <= 10000:
            raise ValueError("invalid evidence")
        result["changed_field_count"] = n
    result.update(release_authorized=False, merge_authorized=False, deploy_authorized=False, mutation_performed=False)
    return result

def main():
    try:
        obj = json.load(sys.stdin)
        safe = sanitize(obj)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
        safe = {"schema_version": 1, "check": "offline_deploy_evidence_privacy", "status": "fail",
                "reason_code": "INVALID_EVIDENCE", "release_authorized": False,
                "merge_authorized": False, "deploy_authorized": False, "mutation_performed": False}
        print(json.dumps(safe, sort_keys=True))
        return 1
    print(json.dumps(safe, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

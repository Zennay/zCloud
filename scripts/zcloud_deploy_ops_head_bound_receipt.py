#!/usr/bin/env python3
"""Read-only, fail-closed admission-evidence evaluator. Never grants release authority."""
import argparse
import json
from pathlib import Path


def evaluate(data):
    reasons = []
    required = ("candidate_pr", "owner", "main_sha", "head_sha", "changed_paths",
                "required_checks", "serialized_owners", "open_pr_overlap",
                "prless_branch_overlap", "ahead", "behind")
    for key in required:
        if key not in data:
            reasons.append("missing:" + key)
    if reasons:
        return {"decision": "defer", "reasons": reasons, "merge_authorized": False,
                "deploy_authorized": False, "mutation_performed": False}
    def sha(v):
        return isinstance(v, str) and len(v) == 40 and all(c in "0123456789abcdef" for c in v)
    if not sha(data["main_sha"]) or not sha(data["head_sha"]):
        reasons.append("invalid_sha")
    if type(data["ahead"]) is not int or data["ahead"] < 1 or type(data["behind"]) is not int or data["behind"] != 0:
        reasons.append("stale_or_invalid_compare")
    if not isinstance(data["changed_paths"], list) or not data["changed_paths"] or not all(isinstance(p, str) and p for p in data["changed_paths"]):
        reasons.append("incomplete_paths")
    if not isinstance(data["candidate_pr"], int) or isinstance(data["candidate_pr"], bool) or data["candidate_pr"] < 1 or not isinstance(data["owner"], str) or not data["owner"].strip():
        reasons.append("invalid_candidate_or_owner")
    checks = data["required_checks"]
    if not isinstance(checks, list) or not checks:
        reasons.append("missing_checks")
    else:
        for check in checks:
            if not isinstance(check, dict) or not all(k in check for k in ("name", "run_id", "head_sha", "conclusion")):
                reasons.append("incomplete_check")
            elif check["conclusion"] != "success" or check["head_sha"] != data["head_sha"] or not check["name"] or not check["run_id"]:
                reasons.append("not_exact_head_green")
    owners = data["serialized_owners"]
    if not isinstance(owners, dict) or any(owners.get(k) != "released" for k in ("580", "1089")):
        reasons.append("serialized_gate_not_released")
    for key in ("open_pr_overlap", "prless_branch_overlap"):
        if data[key] is not False:
            reasons.append("overlap_or_incomplete:" + key)
    # Even valid evidence cannot authorize a merge or deploy. Separate owner sign-off is required.
    return {"decision": "evidence_complete_non_authorizing" if not reasons else "defer",
            "reasons": reasons, "merge_authorized": False,
            "deploy_authorized": False, "mutation_performed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.receipt.read_text(encoding="utf-8"))
        result = evaluate(data) if isinstance(data, dict) else {"decision": "defer", "reasons": ["invalid_receipt"], "merge_authorized": False, "deploy_authorized": False, "mutation_performed": False}
    except (OSError, ValueError) as exc:
        result = {"decision": "defer", "reasons": ["unreadable_receipt:" + type(exc).__name__], "merge_authorized": False, "deploy_authorized": False, "mutation_performed": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["decision"] == "evidence_complete_non_authorizing" else 1


if __name__ == "__main__":
    raise SystemExit(main())

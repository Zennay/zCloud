#!/usr/bin/env python3
"""Offline, fail-closed check for a post-deploy receipt; never performs a deploy.

Only validates evidence supplied by the caller. A passing receipt is not
release authorization and cannot substitute for live gate/ownership checks.
"""
import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED_TRUE = ("serialized_gate_released", "runner_identity_verified",
                 "regression_green", "prewrite_guard_passed",
                 "external_health_verified")
REQUIRED_FALSE = ("rollback_required", "mutation_authorized")

def validate(receipt: object) -> list[str]:
    if not isinstance(receipt, dict):
        return ["receipt_must_be_object"]
    problems = []
    main = receipt.get("main_sha")
    candidate = receipt.get("candidate_sha")
    deployed = receipt.get("deployed_sha")
    for name, value in (("main_sha", main), ("candidate_sha", candidate),
                        ("deployed_sha", deployed)):
        if not isinstance(value, str) or not SHA.fullmatch(value):
            problems.append(f"{name}_invalid")
    if isinstance(candidate, str) and isinstance(deployed, str) and candidate != deployed:
        problems.append("deployed_sha_mismatch")
    for name in REQUIRED_TRUE:
        if receipt.get(name) is not True:
            problems.append(f"{name}_not_proven")
    for name in REQUIRED_FALSE:
        if receipt.get(name) is not False:
            problems.append(f"{name}_unsafe_or_unknown")
    for field in ("runner_proof_url", "regression_url", "health_receipt_url"):
        value = receipt.get(field)
        if not isinstance(value, str) or not value.startswith("https://"):
            problems.append(f"{field}_missing_https")
    return problems

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("receipt", type=Path)
    args = p.parse_args()
    try:
        errors = validate(json.loads(args.receipt.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError) as exc:
        errors = [f"receipt_unreadable_or_invalid_json:{type(exc).__name__}"]
    print(json.dumps({"valid": not errors, "problems": errors,
                      "release_authorized": False, "mutation_performed": False},
                     sort_keys=True))
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())

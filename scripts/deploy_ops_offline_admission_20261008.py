#!/usr/bin/env python3
"""Offline, fail-closed zCloud deploy admission evidence check.

No network, filesystem mutations, GitHub Actions, or production operations.
Consumes a bounded JSON evidence bundle and emits a non-authorizing verdict.
"""
import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = ("regression", "permanent_vps", "production_receipt", "dashboard_external")

def assess(bundle):
    if not isinstance(bundle, dict):
        return {"admissible": False, "reason": "invalid_bundle"}
    main = bundle.get("main_sha")
    candidate = bundle.get("candidate_base_sha")
    head = bundle.get("candidate_head_sha")
    if any(not isinstance(value, str) or not SHA.fullmatch(value) for value in (main, candidate, head)):
        return {"admissible": False, "reason": "missing_or_invalid_sha"}
    if main != candidate:
        return {"admissible": False, "reason": "stale_main"}
    if bundle.get("canonical_repository") != "Zennay/zCloud":
        return {"admissible": False, "reason": "noncanonical_repository"}
    gates = bundle.get("serialized_gates")
    if not isinstance(gates, dict) or set(gates) != {"580", "1089"} or any(gates[key] != "released" for key in ("580", "1089")):
        return {"admissible": False, "reason": "serialized_gate_closed_or_unknown"}
    if bundle.get("ownership_inventory_complete") is not True or bundle.get("owner_overlap") is not False:
        return {"admissible": False, "reason": "ownership_unverified"}
    if bundle.get("dashboard_recovery_pr_mutation") is not False:
        return {"admissible": False, "reason": "unsafe_pr_recovery"}
    checks = bundle.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED):
        return {"admissible": False, "reason": "incomplete_checks"}
    for name in REQUIRED:
        check = checks[name]
        if (not isinstance(check, dict) or check.get("status") != "success"
                or check.get("head_sha") != head or check.get("main_sha") != main):
            return {"admissible": False, "reason": "failed_or_stale_check:" + name}
    return {"admissible": True, "reason": "offline_evidence_consistent"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", help="Local JSON evidence bundle")
    args = parser.parse_args()
    try:
        with open(args.evidence, encoding="utf-8") as source:
            data = json.load(source)
        verdict = assess(data)
    except (OSError, ValueError):
        verdict = {"admissible": False, "reason": "unreadable_evidence"}
    verdict.update({"release_authorized": False, "mutation_performed": False})
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["admissible"] else 1

if __name__ == "__main__":
    sys.exit(main())

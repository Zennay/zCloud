#!/usr/bin/env python3
"""Offline, non-authorizing structural lint for a deploy-gate witness JSON.

Never contacts GitHub, Notion or the VPS. A passing exit code is not release
approval or proof of evidence authenticity/freshness.
"""
import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = (
    "witness_type", "observed_at_utc", "main_sha", "candidate_sha",
    "candidate_base_sha", "changed_paths", "ownership_conflicts",
    "exact_head_checks", "vps_identity_evidence", "gate_580", "gate_1089",
    "production_status", "main_unchanged_on_recheck",
    "release_authorized", "merge_authorized", "deploy_authorized",
    "mutation_performed", "next_owner",
)
DENIAL = ("release_authorized", "merge_authorized", "deploy_authorized", "mutation_performed")


def validate_witness(payload):
    if not isinstance(payload, dict):
        return ["witness must be an object"]
    errors = []
    for field in REQUIRED:
        if field not in payload:
            errors.append("missing:" + field)
    for field in ("main_sha", "candidate_sha", "candidate_base_sha"):
        if field in payload and (not isinstance(payload[field], str) or not SHA.fullmatch(payload[field])):
            errors.append("invalid:" + field)
    if payload.get("witness_type") != "read_only_non_authorizing":
        errors.append("invalid:witness_type")
    for field in DENIAL:
        if payload.get(field) is not False:
            errors.append("must_be_false:" + field)
    # A static witness must never promote uncertain state to an authorization.
    if payload.get("main_unchanged_on_recheck") not in (True, False, "unknown"):
        errors.append("invalid:main_unchanged_on_recheck")
    for field in ("changed_paths", "exact_head_checks"):
        if field in payload and not isinstance(payload[field], list):
            errors.append("invalid:" + field)
    return sorted(set(errors))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: zcloud_deploy_gate_witness_lint.py WITNESS.json", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        print("invalid:unreadable_json")
        return 2
    errors = validate_witness(payload)
    # Never print caller-supplied data: witness material may contain sensitive content.
    print(json.dumps({"valid_structure_only": not errors, "errors": errors,
                      "release_authorized": False, "merge_authorized": False,
                      "deploy_authorized": False, "mutation_performed": False},
                     sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

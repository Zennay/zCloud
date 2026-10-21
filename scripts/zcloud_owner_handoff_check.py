#!/usr/bin/env python3
"""Offline, non-authorizing check for serialized deploy-owner handoff evidence."""
import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
FIELDS = ("repository", "gate", "operation_class", "target_environment",
          "outgoing_owner", "incoming_owner", "main_sha", "candidate_head_sha",
          "rollback_owner", "verification_runs", "production_receipt")
FLAGS = ("release_authorized", "merge_authorized", "deploy_authorized",
         "mutation_performed")
CHECKS = ("outgoing_freeze_confirmed", "incoming_explicit_acceptance",
          "permanent_vps_runner_verified", "live_gate_owner_rechecked")


def evaluate(record):
    errors = []
    if not isinstance(record, dict):
        return {"handoff_consistent": False, "reasons": ["invalid_record"],
                **{key: False for key in FLAGS}}
    for key in FIELDS:
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append("missing_" + key)
    for key in ("main_sha", "candidate_head_sha"):
        if not isinstance(record.get(key), str) or not SHA.fullmatch(record[key]):
            errors.append("invalid_" + key)
    if record.get("operation_class") not in ("metadata", "merge", "deploy", "rollback"):
        errors.append("invalid_operation_class")
    if record.get("outgoing_owner") == record.get("incoming_owner"):
        errors.append("owners_not_distinct")
    for key in FLAGS:
        if record.get(key) is not False:
            errors.append("unsafe_" + key)
    for key in CHECKS:
        if record.get(key) is not True:
            errors.append("unconfirmed_" + key)
    return {"handoff_consistent": not errors, "reasons": sorted(set(errors)),
            **{key: False for key in FLAGS}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", help="Local JSON handoff record; never contacts production")
    args = parser.parse_args(argv)
    try:
        with open(args.record, encoding="utf-8") as handle:
            result = evaluate(json.load(handle))
    except (OSError, ValueError):
        result = evaluate(None)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["handoff_consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())

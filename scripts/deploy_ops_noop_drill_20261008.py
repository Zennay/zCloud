#!/usr/bin/env python3
"""Offline, non-authorizing deploy-ops tabletop STOP checker.

Usage: python3 scripts/deploy_ops_noop_drill_20261008.py scenario.json
This checker does not contact GitHub or a VPS and cannot grant permissions.
"""
import json
import sys
from pathlib import Path

FIELDS = frozenset({"scenario", "expected_stop", "observed_stop", "evidence_refs"})
SCENARIOS = frozenset({"stale_head_ci", "missing_external_health", "serialized_owner_active"})


def check(record):
    if not isinstance(record, dict) or set(record) != FIELDS:
        return False
    if record["scenario"] not in SCENARIOS:
        return False
    if record["expected_stop"] is not True or record["observed_stop"] is not True:
        return False
    refs = record["evidence_refs"]
    return (
        isinstance(refs, list)
        and 1 <= len(refs) <= 10
        and all(isinstance(ref, str) and ref.startswith("https://github.com/Zennay/zCloud/") and len(ref) < 250 for ref in refs)
    )


def result(ok):
    return {
        "drill_complete": bool(ok),
        "release_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "rollback_authorized": False,
        "mutation_performed": False,
    }


def main(argv):
    try:
        if len(argv) != 2:
            raise ValueError("expected one local JSON path")
        path = Path(argv[1])
        if path.stat().st_size > 16384:
            raise ValueError("oversized input")
        data = json.loads(path.read_text(encoding="utf-8"))
        valid = isinstance(data, list) and len(data) == 3
        if valid:
            valid = {r.get("scenario") for r in data if isinstance(r, dict)} == SCENARIOS and all(check(r) for r in data)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        valid = False
    print(json.dumps(result(valid), sort_keys=True))
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

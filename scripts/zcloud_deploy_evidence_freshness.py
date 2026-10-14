#!/usr/bin/env python3
"""Fail-closed, offline deploy evidence freshness check.

This helper does not access GitHub, dispatch workflows, merge, or deploy.
Input is an operator-supplied snapshot; freshness is *not* inferred from
workflow success alone.
"""
import argparse
import json
import re
import sys

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED = ("regression", "cpu", "dashboard")


def inspect(snapshot):
    errors = []
    if not isinstance(snapshot, dict):
        return ["snapshot must be an object"]
    main = snapshot.get("main_sha")
    base = snapshot.get("candidate_base_sha")
    head = snapshot.get("candidate_head_sha")
    for name, sha in (("main_sha", main), ("candidate_base_sha", base), ("candidate_head_sha", head)):
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            errors.append(f"{name}: invalid full lowercase SHA")
    if main != base:
        errors.append("candidate base differs from current main")
    if type(snapshot.get("behind")) is not int or snapshot["behind"] != 0:
        errors.append("candidate is behind main or behind count missing")
    if type(snapshot.get("ahead")) is not int or snapshot["ahead"] < 1:
        errors.append("candidate ahead count missing or not positive")
    if main == head:
        errors.append("candidate head must differ from current main")
    if snapshot.get("serialized_gate_released") is not True:
        errors.append("serialized deploy gate not explicitly released")
    checks = snapshot.get("checks")
    if not isinstance(checks, dict):
        return errors + ["checks must be an object"]
    for key in _REQUIRED:
        check = checks.get(key)
        if not isinstance(check, dict):
            errors.append(f"{key}: missing check")
            continue
        if check.get("conclusion") != "success":
            errors.append(f"{key}: check not successful")
        if check.get("head_sha") != head:
            errors.append(f"{key}: check not bound to candidate head")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON snapshot path (not an authorization token)")
    args = parser.parse_args(argv)
    try:
        with open(args.snapshot, encoding="utf-8") as stream:
            data = json.load(stream)
        errors = inspect(data)
    except (OSError, ValueError) as exc:
        errors = [f"cannot read snapshot: {type(exc).__name__}"]
    print(json.dumps({"admissible": not errors, "errors": errors,
                      "deploy_authorized": False, "mutation_performed": False},
                     sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())

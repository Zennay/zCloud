#!/usr/bin/env python3
"""Offline, non-authorizing dashboard-recovery admission fixture validator.

This is a specification model, NOT a production authorization engine. It reads
fixture JSON only and performs no network, runner, subprocess or VPS operations.
"""
import json
import pathlib
import sys

FIXTURES = pathlib.Path(__file__).with_name("dashboard-recovery-admission-cases-20261008.json")
REQUIRED = {"pr-healthy", "pr-unhealthy", "main-healthy", "untrusted-actor",
            "stale-main", "writer-active", "gate-unknown", "approval-missing",
            "trusted-bounded"}


def model_allows(case):
    """Fail closed: every safety prerequisite must be explicitly true."""
    return all((
        case.get("event") == "workflow_dispatch",
        case.get("dashboard") == "unhealthy",
        case.get("trusted") is True,
        case.get("head_matches_main") is True,
        case.get("gates") == "released",
        case.get("approved") is True,
        case.get("runner_verified") is True,
        case.get("exclusive_window") is True,
        case.get("rollback_ready") is True,
    ))


def validate(data):
    assert data.get("schema_version") == 1
    assert data.get("kind") == "zcloud-dashboard-recovery-admission-fixtures"
    cases = data.get("cases")
    assert isinstance(cases, list)
    assert {case["id"] for case in cases} == REQUIRED
    assert len(cases) == len(REQUIRED), "Duplicate case IDs"
    for case in cases:
        assert isinstance(case.get("allow_recovery"), bool), case["id"]
        assert model_allows(case) is case["allow_recovery"], case["id"]
    # Positive case must fail closed after removal or corruption of ANY prerequisite.
    positive = next(case for case in cases if case["id"] == "trusted-bounded")
    for key in ("event", "dashboard", "trusted", "head_matches_main", "gates",
                "approved", "runner_verified", "exclusive_window", "rollback_ready"):
        missing = dict(positive)
        missing.pop(key, None)
        assert not model_allows(missing), ("missing", key)
        bad = dict(positive)
        bad[key] = "UNKNOWN"
        assert not model_allows(bad), ("unknown", key)
    return len(cases)


def main():
    try:
        count = validate(json.loads(FIXTURES.read_text(encoding="utf-8")))
    except (AssertionError, ValueError, KeyError, OSError) as exc:
        print(f"FAIL: admission fixture contract: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {count} dashboard admission fixtures; all prerequisites fail closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed admission policy for cosmetic refactors during P0/P1 reliability debt.

Pure policy only. Runtime integration must derive change kind and open reliability
issues from canonical evidence; this module never mutates GitHub, SQLite, Notion,
files, queues or services.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCHEMA_VERSION = "cosmetic-refactor-admission-v1"
MAX_INPUT_BYTES = 32 * 1024
MAX_ISSUES = 100
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:#-]{0,79}$")
CHANGE_KINDS = {
    "cosmetic_refactor",
    "functional",
    "reliability_fix",
    "security_fix",
    "docs_only",
    "test_only",
}
PRIORITIES = {"P0", "P1", "P2", "P3", "P4"}


def _canonical_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a canonical machine id")
    return value


def _change(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"id", "kind"}:
        raise ValueError("change fields must exactly match contract")
    change_id = _canonical_id(raw["id"], "change id")
    kind = raw["kind"]
    if not isinstance(kind, str) or kind not in CHANGE_KINDS:
        raise ValueError("change kind is unsupported")
    return {"id": change_id, "kind": kind}


def _issue(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"id", "priority"}:
        raise ValueError("issue fields must exactly match contract")
    issue_id = _canonical_id(raw["id"], "issue id")
    priority = raw["priority"]
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise ValueError("issue priority is unsupported")
    return {"id": issue_id, "priority": priority}


def decide(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "change",
        "open_reliability_issues",
    }:
        raise ValueError(
            "top-level fields must be schema_version, change and open_reliability_issues"
        )
    version = payload["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("schema_version must equal integer 1")

    change = _change(payload["change"])
    raw_issues = payload["open_reliability_issues"]
    if not isinstance(raw_issues, list):
        raise ValueError("open_reliability_issues must be a list")
    if len(raw_issues) > MAX_ISSUES:
        raise ValueError(f"open reliability issue count exceeds {MAX_ISSUES}")
    issues = [_issue(item) for item in raw_issues]
    ids = [item["id"] for item in issues]
    if len(ids) != len(set(ids)):
        raise ValueError("open reliability issue ids must be unique")

    urgent = sorted(
        (item for item in issues if item["priority"] in {"P0", "P1"}),
        key=lambda item: (0 if item["priority"] == "P0" else 1, item["id"]),
    )
    blocked = change["kind"] == "cosmetic_refactor" and bool(urgent)
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "RELIABILITY_WORK_REQUIRED" if blocked else "ALLOWED",
        "change_id": change["id"],
        "change_kind": change["kind"],
        "open_reliability_count": len(issues),
        "urgent_reliability_count": len(urgent),
        "urgent_reliability_ids": [item["id"] for item in urgent],
        "reason_code": (
            "cosmetic_refactor_blocked_by_open_p0_p1_reliability"
            if blocked
            else "admission_allowed"
        ),
    }


def _load_bytes(path: Path | None) -> bytes:
    if path is None:
        data = __import__("sys").stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        if path.is_symlink():
            raise ValueError("input path must not be a symlink")
        if not path.is_file():
            raise ValueError("input path must be a regular file")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
        with path.open("rb") as handle:
            data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Admit or block cosmetic zCloud changes against open P0/P1 reliability debt"
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--require-allowed", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    raw = _load_bytes(args.input)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("invalid UTF-8 JSON input") from exc
    result = decide(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if (result["decision"] == "ALLOWED" or not args.require_allowed) else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed lint for the non-authorizing deploy-ops decision record."""
from pathlib import Path
import re
import sys

RECORD = Path(__file__).resolve().parents[1] / "docs" / "deploy-ops-gate-release-decision-record.md"
REQUIRED = ("#580", "#1089", "PWQ-258", "HOLD", "main SHA", "PR-less", "exact-head")
FLAGS = ("mutation_performed", "merge_authorized", "deploy_authorized")


def validate(text: str) -> list[str]:
    failures = [f"missing requirement: {item}" for item in REQUIRED if item not in text]
    sections = re.split(r"(?m)^## Decision\s*$", text)
    if len(sections) != 2:
        return failures + ["expected exactly one decision section"]
    decision = sections[1]
    for flag in FLAGS:
        values = re.findall(rf"(?im)^\s*-\s*{flag}:\s*(\S+)\s*$", decision)
        if values != ["false"]:
            failures.append(f"{flag} must occur once and be false in decision")
    dispositions = re.findall(r"(?im)^\s*-\s*Gate disposition:\s*(.+?)\s*$", decision)
    if dispositions != ["HOLD (default)"]:
        failures.append("decision must have exactly one HOLD (default) disposition")
    return failures


def main() -> int:
    if not RECORD.is_file():
        print(f"missing decision record: {RECORD}", file=sys.stderr)
        return 1
    failures = validate(RECORD.read_text(encoding="utf-8"))
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print("Decision record lint OK (does not authorize release)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

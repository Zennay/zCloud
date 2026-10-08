#!/usr/bin/env python3
"""Fail-closed lint for the non-authorizing deploy-ops decision record."""
from pathlib import Path
import re
import sys

RECORD = Path(__file__).resolve().parents[1] / "docs" / "deploy-ops-gate-release-decision-record.md"
REQUIRED = (
    "#580", "#1089", "PWQ-258", "HOLD", "main SHA",
    "PR-less", "exact-head", "mutation_performed: false",
    "merge_authorized: false", "deploy_authorized: false",
)
DENIED = (
    r"(?im)^- (?:merge_authorized|deploy_authorized|mutation_performed):\s*true\s*$",
    r"(?im)^- Gate disposition:\s*(?:GO|RELEASE|APPROVED)\b",
)


def validate(text: str) -> list[str]:
    failures = [f"missing requirement: {item}" for item in REQUIRED if item not in text]
    failures.extend(f"unsafe declaration: {pattern}" for pattern in DENIED if re.search(pattern, text))
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

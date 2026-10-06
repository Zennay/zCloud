#!/usr/bin/env python3
"""Audit zCloud evidence/log retention without mutating runtime state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOWS = ROOT / ".github" / "workflows"
UPLOAD_RE = re.compile(r"^\s*uses:\s*actions/upload-artifact@\S+", re.IGNORECASE)
RETENTION_RE = re.compile(r"^\s*retention-days:\s*([^#\s]+)")


class RetentionAuditError(ValueError):
    """Raised when workflow retention evidence is malformed."""


def _step_block(lines: list[str], uses_index: int) -> list[str]:
    uses_line = lines[uses_index]
    uses_indent = len(uses_line) - len(uses_line.lstrip())
    start = uses_index
    while start > 0:
        previous = lines[start - 1]
        indent = len(previous) - len(previous.lstrip())
        stripped = previous.lstrip()
        if stripped.startswith("- ") and indent < uses_indent:
            start -= 1
            break
        start -= 1

    end = uses_index + 1
    while end < len(lines):
        current = lines[end]
        indent = len(current) - len(current.lstrip())
        stripped = current.lstrip()
        if stripped.startswith("- ") and indent < uses_indent:
            break
        end += 1
    return lines[max(0, start):end]


def audit_workflow(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    uploads: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not UPLOAD_RE.match(line):
            continue
        block = _step_block(lines, index)
        values = []
        for candidate in block:
            match = RETENTION_RE.match(candidate)
            if match:
                values.append(match.group(1))
        if len(values) > 1:
            raise RetentionAuditError(
                f"{path.name}:{index + 1}: multiple retention-days values"
            )
        retention_days: int | None = None
        state = "implicit_repository_default"
        if values:
            raw = values[0]
            if not raw.isdigit() or int(raw) <= 0:
                raise RetentionAuditError(
                    f"{path.name}:{index + 1}: invalid retention-days {raw!r}"
                )
            retention_days = int(raw)
            state = "explicit"
        uploads.append(
            {
                "workflow": path.name,
                "line": index + 1,
                "retention_state": state,
                "retention_days": retention_days,
            }
        )
    return uploads


def audit_repository(workflows_dir: Path = DEFAULT_WORKFLOWS) -> dict[str, Any]:
    if not workflows_dir.is_dir():
        raise RetentionAuditError("workflows_directory_missing")
    paths = sorted(
        set(workflows_dir.glob("*.yml")) | set(workflows_dir.glob("*.yaml"))
    )
    uploads: list[dict[str, Any]] = []
    for path in paths:
        uploads.extend(audit_workflow(path))

    explicit = [item for item in uploads if item["retention_state"] == "explicit"]
    implicit = [
        item for item in uploads
        if item["retention_state"] == "implicit_repository_default"
    ]
    return {
        "schema_version": 1,
        "workflow_count": len(paths),
        "artifact_upload_count": len(uploads),
        "explicit_retention_count": len(explicit),
        "implicit_retention_count": len(implicit),
        "explicit_coverage_percent": (
            round(100.0 * len(explicit) / len(uploads), 1) if uploads else 100.0
        ),
        "uploads": uploads,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit explicit retention for GitHub evidence artifacts"
    )
    parser.add_argument("--workflows", type=Path, default=DEFAULT_WORKFLOWS)
    parser.add_argument("--require-explicit", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = audit_repository(args.workflows)
        if args.require_explicit and report["implicit_retention_count"]:
            raise RetentionAuditError("implicit_artifact_retention_present")
    except (OSError, UnicodeError, RetentionAuditError) as exc:
        error = str(exc) if isinstance(exc, RetentionAuditError) else "workflow_read_failed"
        if args.json:
            print(json.dumps({"ok": False, "error": error}, sort_keys=True))
        else:
            print(f"ZCLOUD_LOG_RETENTION_BLOCKED error={error}")
        return 2

    payload = {"ok": True, "report": report}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            "ZCLOUD_LOG_RETENTION_AUDIT_GREEN",
            f"uploads={report['artifact_upload_count']}",
            f"explicit={report['explicit_retention_count']}",
            f"implicit={report['implicit_retention_count']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

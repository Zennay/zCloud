#!/usr/bin/env python3
"""Read-only audit for the dashboard resource-priority control feedback contract."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

MAX_SOURCE_BYTES = 512 * 1024


class AuditError(ValueError):
    pass


def read_bounded_regular_file(path: Path) -> str:
    try:
        st = path.lstat()
    except OSError as exc:
        raise AuditError("source_unavailable") from exc
    if stat.S_ISLNK(st.st_mode):
        raise AuditError("source_symlink")
    if not stat.S_ISREG(st.st_mode):
        raise AuditError("source_not_regular")
    if st.st_size > MAX_SOURCE_BYTES:
        raise AuditError("source_too_large")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AuditError("source_unreadable") from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise AuditError("source_too_large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditError("source_not_utf8") from exc


def _ordered(text: str, *needles: str) -> bool:
    cursor = 0
    for needle in needles:
        idx = text.find(needle, cursor)
        if idx < 0:
            return False
        cursor = idx + len(needle)
    return True


def audit_source(text: str) -> dict[str, object]:
    control_present = all(
        marker in text
        for marker in (
            'data-resource-priority="',
            'data-previous-value="',
            "document.addEventListener('change'",
            "fetch('/api/resource-priority'",
        )
    )

    pending_disable = _ordered(
        text,
        "var previous=el.dataset.previousValue||'normal';",
        "el.disabled=true;",
        "fetch('/api/resource-priority'",
    )
    backend_confirmation = _ordered(
        text,
        "fetch('/api/resource-priority'",
        "if(!response.ok)",
        "el.dataset.previousValue=saved;",
    )
    failure_feedback = all(
        marker in text
        for marker in (
            "el.value=previous;",
            "$('notice').hidden=false;",
            "Project priority could not be saved:",
        )
    )
    degraded_feedback = "Priority saved. The live VPS weight could not be applied yet" in text
    reenabled = _ordered(text, "}finally{", "el.disabled=false;")
    confirmed_refresh = _ordered(
        text,
        "el.dataset.previousValue=saved;",
        "await refresh(true);",
    )

    pending_feedback = any(
        marker in text
        for marker in (
            "Saving priority",
            "Saving…",
            "aria-busy",
            "resource-priority-pending",
        )
    )
    success_feedback = any(
        marker in text
        for marker in (
            "Priority saved.",
            "Priority updated.",
            "resource-priority-saved",
        )
    )
    # The degraded warning is not a generic success signal: it is only shown
    # when persistence succeeded but live application did not.
    if degraded_feedback and success_feedback:
        success_feedback = any(
            marker in text
            for marker in (
                "Priority updated.",
                "resource-priority-saved",
            )
        )

    checks = {
        "control_present": control_present,
        "pending_disable": pending_disable,
        "pending_feedback": pending_feedback,
        "backend_confirmation": backend_confirmation,
        "confirmed_refresh": confirmed_refresh,
        "success_feedback": success_feedback,
        "degraded_feedback": degraded_feedback,
        "failure_feedback": failure_feedback,
        "reenabled": reenabled,
    }
    required = (
        "control_present",
        "pending_disable",
        "pending_feedback",
        "backend_confirmation",
        "success_feedback",
        "degraded_feedback",
        "failure_feedback",
        "reenabled",
    )
    missing = [name for name in required if not checks[name]]
    state = "complete" if not missing else "needs_hardening"
    return {
        "schema_version": 1,
        "control": "resource_priority",
        "state": state,
        "missing": missing,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="public/enhancements.js",
        help="Path to the dashboard enhancement source.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = audit_source(read_bounded_regular_file(Path(args.source)))
    except AuditError as exc:
        report = {
            "schema_version": 1,
            "control": "resource_priority",
            "state": "invalid",
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"resource_priority: invalid ({exc})")
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        missing = ",".join(report["missing"]) or "none"
        print(f"resource_priority: {report['state']} missing={missing}")

    if args.require_complete and report["state"] != "complete":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

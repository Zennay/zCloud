#!/usr/bin/env python3
"""Read-only audit for pending/error feedback on project layout actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MAX_SOURCE_BYTES = 2_000_000


def _handler_block(source: str, marker: str, next_marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        return ""
    end = source.find(next_marker, start)
    return source[start:] if end < 0 else source[start:end]


def _control_result(source: str, name: str, render_marker: str, handler_marker: str, next_marker: str) -> dict:
    rendered = render_marker in source
    block = _handler_block(source, handler_marker, next_marker)
    handler_present = bool(block)
    disabled_while_waiting = f"{name}.disabled=true" in block
    visible_pending_copy = (
        f"{name}.textContent=" in block
        or f"{name}.setAttribute('aria-busy'" in block
        or f'{name}.setAttribute("aria-busy"' in block
    )
    error_feedback = (
        ("catch(" in block or "catch (" in block)
        and ("window.alert" in block or "$('notice')" in block or '"notice"' in block)
    )
    complete = rendered and handler_present and disabled_while_waiting and visible_pending_copy and error_feedback
    missing = []
    if not rendered:
        missing.append("render_marker")
    if not handler_present:
        missing.append("handler")
    if handler_present and not disabled_while_waiting:
        missing.append("pending_disable")
    if handler_present and not visible_pending_copy:
        missing.append("pending_copy")
    if handler_present and not error_feedback:
        missing.append("error_feedback")
    return {
        "control": name,
        "complete": complete,
        "missing": missing,
    }


def audit_source(source: str) -> dict:
    controls = [
        _control_result(
            source,
            "archive",
            'data-archive="',
            "const archive=e.target.closest('[data-archive]')",
            "const restore=e.target.closest('[data-restore]')",
        ),
        _control_result(
            source,
            "restore",
            'data-restore="',
            "const restore=e.target.closest('[data-restore]')",
            "const projectCardTarget=",
        ),
    ]
    incomplete = [item for item in controls if not item["complete"]]
    return {
        "schema_version": 1,
        "status": "complete" if not incomplete else "needs_hardening",
        "controls_observed": len(controls),
        "controls_complete": len(controls) - len(incomplete),
        "controls_missing_feedback": len(incomplete),
        "missing_by_control": {
            item["control"]: item["missing"]
            for item in incomplete
        },
        "policy": {
            "scope": "project_layout_archive_restore",
            "requires": [
                "disable_while_backend_pending",
                "visible_pending_copy_or_aria_busy",
                "visible_error_feedback",
            ],
        },
    }


def read_source(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("source must not be a symlink")
    data = path.read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds bounded audit size")
    return data.decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="public/app.js")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit_source(read_source(Path(args.source)))
    except Exception as exc:
        result = {
            "schema_version": 1,
            "status": "error",
            "controls_observed": 0,
            "controls_complete": 0,
            "controls_missing_feedback": 0,
            "error": str(exc)[:300],
        }
        print(json.dumps(result, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "ZCLOUD_LAYOUT_ACTION_FEEDBACK_"
            + ("GREEN" if result["status"] == "complete" else "NEEDS_HARDENING")
        )
    if args.require_complete and result["status"] != "complete":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

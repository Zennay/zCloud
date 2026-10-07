#!/usr/bin/env python3
"""Inventory zCloud workflows that can write directly to main."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any

MAX_WORKFLOW_BYTES = 512 * 1024
MAX_WORKFLOWS = 512

CONTENTS_WRITE_RE = re.compile(r"(?m)^\s*contents:\s*write\s*$")
DIRECT_PUSH_PATTERNS = (
    re.compile(r"(?m)^\s*[^#\n]*\bgit\s+push\b[^\n]*\bHEAD:main\b"),
    re.compile(r"(?m)^\s*[^#\n]*\bgit\s+push\b[^\n]*\brefs/heads/main\b"),
    re.compile(r"(?m)^\s*[^#\n]*\bgit\s+push\b[^\n]*\borigin\s+main(?:\s|$)"),
)
REF_TARGET_RE = re.compile(
    r"(?:git/refs/(?:heads/)?main|git/refs/heads/main|refs/heads/main)",
    re.IGNORECASE,
)
WRITE_METHOD_RE = re.compile(
    r"(?:--method\s+(?:POST|PUT|PATCH|DELETE)|-X\s*(?:POST|PUT|PATCH|DELETE))",
    re.IGNORECASE,
)


class InventoryError(ValueError):
    """Raised when workflow inventory evidence cannot be trusted."""


def _read_workflow(path: Path) -> str:
    if path.is_symlink():
        raise InventoryError("workflow_symlink_rejected")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise InventoryError("workflow_unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise InventoryError("workflow_not_regular")
    if metadata.st_size > MAX_WORKFLOW_BYTES:
        raise InventoryError("workflow_too_large")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InventoryError("workflow_unreadable") from exc
    if len(raw) > MAX_WORKFLOW_BYTES:
        raise InventoryError("workflow_too_large")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise InventoryError("workflow_non_utf8") from exc


def _signals(text: str) -> list[str]:
    signals: list[str] = []
    if CONTENTS_WRITE_RE.search(text):
        signals.append("contents_write")
    if any(pattern.search(text) for pattern in DIRECT_PUSH_PATTERNS):
        signals.append("git_push_main")
    if REF_TARGET_RE.search(text) and WRITE_METHOD_RE.search(text):
        signals.append("git_ref_write_main")
    return signals


def inventory_workflows(workflows_dir: Path) -> dict[str, Any]:
    if workflows_dir.is_symlink():
        raise InventoryError("workflows_dir_symlink_rejected")
    if not workflows_dir.is_dir():
        raise InventoryError("workflows_dir_invalid")

    files = sorted(
        {
            *workflows_dir.glob("*.yml"),
            *workflows_dir.glob("*.yaml"),
        },
        key=lambda path: path.name,
    )
    if len(files) > MAX_WORKFLOWS:
        raise InventoryError("too_many_workflows")

    writers: list[dict[str, Any]] = []
    contents_write_only: list[str] = []

    for path in files:
        text = _read_workflow(path)
        signals = _signals(text)
        direct = "git_push_main" in signals or "git_ref_write_main" in signals
        relative = f".github/workflows/{path.name}"
        if direct:
            writers.append(
                {
                    "path": relative,
                    "signals": signals,
                }
            )
        elif "contents_write" in signals:
            contents_write_only.append(relative)

    writers.sort(key=lambda row: row["path"])
    contents_write_only.sort()

    return {
        "ok": True,
        "schema_version": 1,
        "coverage_complete": True,
        "workflow_count": len(files),
        "direct_main_writer_count": len(writers),
        "contents_write_without_direct_main_count": len(contents_write_only),
        "direct_main_writers": writers,
        "contents_write_without_direct_main": contents_write_only,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory workflow surfaces that can write directly to zCloud main"
    )
    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=Path(".github/workflows"),
    )
    parser.add_argument(
        "--require-zero-direct-writers",
        action="store_true",
    )
    args = parser.parse_args(argv)

    try:
        result = inventory_workflows(args.workflows_dir)
    except InventoryError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "schema_version": 1,
                    "coverage_complete": False,
                    "errors": [str(exc)],
                    "mutation_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    if args.require_zero_direct_writers and result["direct_main_writer_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

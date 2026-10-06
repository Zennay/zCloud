#!/usr/bin/env python3
"""Bounded, read-only inventory of legacy-looking GitHub Actions workflows.

This tool deliberately does not decide that a workflow is obsolete. It only
classifies conservative filename markers and operational-risk hints so humans
or later coordinated automation can prioritize review without touching active
workflow state.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1
MAX_WORKFLOWS = 512
MAX_WORKFLOW_BYTES = 256 * 1024
DEFAULT_MAX_CANDIDATES = 100

DATE_MARKER = re.compile(r"(?:^|[-_])20\d{6}(?=[-_.]|$)", re.IGNORECASE)
PR_MARKER = re.compile(r"(?:^|[-_])pr\d+(?=[-_.]|$)", re.IGNORECASE)
TASK_MARKER = re.compile(r"(?:^|[-_])task\d+(?=[-_.]|$)", re.IGNORECASE)

TRIGGERS = {
    "workflow_dispatch": re.compile(r"(?m)^\s*workflow_dispatch\s*:"),
    "schedule": re.compile(r"(?m)^\s*schedule\s*:"),
    "pull_request": re.compile(r"(?m)^\s*pull_request(?:_target)?\s*:"),
    "push": re.compile(r"(?m)^\s*push\s*:"),
    "workflow_run": re.compile(r"(?m)^\s*workflow_run\s*:"),
}

RISK_HINTS = {
    "contents_write": re.compile(r"(?m)^\s*contents\s*:\s*write\s*$"),
    "actions_write": re.compile(r"(?m)^\s*actions\s*:\s*write\s*$"),
    "git_push": re.compile(r"(?m)^\s*git\s+push\b"),
    "service_mutation": re.compile(r"\bsystemctl\s+(?:start|stop|restart|enable|disable|reset-failed)\b"),
    "docker_mutation": re.compile(r"\bdocker\s+(?:build|run|rm|image\s+rm|compose\s+up|compose\s+down)\b"),
    "http_post": re.compile(r"(?:\bmethod\s*=\s*[\"']POST[\"']|\bcurl\b[^\n]*\s-X\s*POST\b)", re.IGNORECASE),
}


class InventoryError(RuntimeError):
    pass


def _marker_kinds(name: str) -> list[str]:
    kinds: list[str] = []
    if DATE_MARKER.search(name):
        kinds.append("dated_filename")
    if PR_MARKER.search(name):
        kinds.append("pr_scoped_filename")
    if TASK_MARKER.search(name):
        kinds.append("task_scoped_filename")
    return kinds


def _load_workflow(path: Path) -> str:
    if path.is_symlink():
        raise InventoryError(f"workflow path must not be a symlink: {path}")
    if not path.is_file():
        raise InventoryError(f"workflow path is not a regular file: {path}")
    size = path.stat().st_size
    if size > MAX_WORKFLOW_BYTES:
        raise InventoryError(
            f"workflow exceeds {MAX_WORKFLOW_BYTES} byte bound: {path} ({size} bytes)"
        )
    return path.read_text(encoding="utf-8")


def _relative_display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root.parent.parent))
    except ValueError:
        return str(path)


def inventory(root: Path, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> dict:
    if max_candidates < 1 or max_candidates > MAX_WORKFLOWS:
        raise InventoryError(
            f"max_candidates must be between 1 and {MAX_WORKFLOWS}"
        )
    if root.is_symlink():
        raise InventoryError(f"workflow root must not be a symlink: {root}")
    if not root.is_dir():
        raise InventoryError(f"workflow root is not a directory: {root}")

    workflows = sorted(
        [*root.glob("*.yml"), *root.glob("*.yaml")],
        key=lambda path: path.name,
    )
    if len(workflows) > MAX_WORKFLOWS:
        raise InventoryError(
            f"workflow count exceeds {MAX_WORKFLOWS}: {len(workflows)}"
        )

    marker_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    candidates: list[dict] = []

    for path in workflows:
        markers = _marker_kinds(path.name)
        if not markers:
            continue

        text = _load_workflow(path)
        triggers = sorted(name for name, pattern in TRIGGERS.items() if pattern.search(text))
        risks = sorted(name for name, pattern in RISK_HINTS.items() if pattern.search(text))
        self_hosted = "self-hosted" in text

        marker_counts.update(markers)
        risk_counts.update(risks)
        candidates.append(
            {
                "workflow": _relative_display(path, root),
                "markers": markers,
                "triggers": triggers,
                "self_hosted": self_hosted,
                "risk_flags": risks,
            }
        )

    candidates.sort(key=lambda item: item["workflow"])
    total_candidates = len(candidates)
    bounded_candidates = candidates[:max_candidates]

    return {
        "schema_version": SCHEMA_VERSION,
        "workflows_scanned": len(workflows),
        "legacy_candidates": total_candidates,
        "candidates_truncated": total_candidates > len(bounded_candidates),
        "candidate_limit": max_candidates,
        "marker_counts": dict(sorted(marker_counts.items())),
        "risk_counts": dict(sorted(risk_counts.items())),
        "candidates": bounded_candidates,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".github/workflows"),
        help="Workflow directory to inspect (default: .github/workflows)",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=DEFAULT_MAX_CANDIDATES,
        help=f"Maximum candidate rows emitted (1..{MAX_WORKFLOWS})",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = inventory(args.root, max_candidates=args.max_candidates)
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_ONE_SHOT_WORKFLOW_INVENTORY "
            f"workflows_scanned={report['workflows_scanned']} "
            f"legacy_candidates={report['legacy_candidates']} "
            f"truncated={str(report['candidates_truncated']).lower()}"
        )
        for key, value in report["marker_counts"].items():
            print(f"marker.{key}={value}")
        for key, value in report["risk_counts"].items():
            print(f"risk.{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

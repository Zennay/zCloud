#!/usr/bin/env python3
"""Report project-result adapter coverage without touching runtime state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import enhancements

SCHEMA_VERSION = 1
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class AdapterCoverageError(ValueError):
    """Raised when registry input is unsafe or malformed."""


def _project_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) > 32 or not PROJECT_RE.fullmatch(text):
        raise AdapterCoverageError(f"invalid project id: {text!r}")
    return text


def load_registry(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.is_symlink():
        raise AdapterCoverageError("projects registry must be a regular non-symlink file")
    if not path.exists():
        raise AdapterCoverageError("projects registry is missing")
    if not path.is_file():
        raise AdapterCoverageError("projects registry must be a regular non-symlink file")
    if path.stat().st_size > MAX_REGISTRY_BYTES:
        raise AdapterCoverageError("projects registry exceeds 2 MiB")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterCoverageError("projects registry is unreadable JSON") from exc
    if not isinstance(raw, list):
        raise AdapterCoverageError("projects registry must be a JSON list")
    return raw


def build_report(
    registry: list[dict[str, Any]],
    *,
    adapter_projects_reader: Callable[[], Iterable[str]] = enhancements.telemetry_adapter_projects,
) -> dict[str, Any]:
    adapters = {_project_id(value) for value in adapter_projects_reader()}

    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in registry:
        if not isinstance(row, dict):
            raise AdapterCoverageError("project registry rows must be JSON objects")
        if str(row.get("status") or "").strip().lower() != "active":
            continue

        project_id = _project_id(row.get("id"))
        if project_id in seen:
            raise AdapterCoverageError(f"duplicate active project id: {project_id}")
        seen.add(project_id)

        name = str(row.get("name") or project_id).strip()[:120]
        adapter_backed = project_id in adapters
        projects.append(
            {
                "project_id": project_id,
                "name": name,
                "result_mode": "project_adapter" if adapter_backed else "generic_only",
                "adapter_backed": adapter_backed,
            }
        )

    projects.sort(key=lambda item: item["project_id"])
    missing = [item["project_id"] for item in projects if not item["adapter_backed"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "active_project_count": len(projects),
        "adapter_backed_count": len(projects) - len(missing),
        "generic_only_count": len(missing),
        "coverage_complete": not missing,
        "generic_only_projects": missing,
        "projects": projects,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report active-project telemetry adapter coverage.")
    parser.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "projects.json",
        help="Read-only projects registry path.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit 3 when at least one active project still uses generic fallback.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(load_registry(args.projects))
    except AdapterCoverageError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "invalid", "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(report, sort_keys=True))
    if args.require_complete and not report["coverage_complete"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

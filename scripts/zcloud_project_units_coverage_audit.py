#!/usr/bin/env python3
"""Read-only audit of active projects versus enhancements.PROJECT_UNITS coverage."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _project_units_keys(path: Path) -> set[str]:
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path}")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PROJECT_UNITS"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, dict):
                raise ValueError("PROJECT_UNITS must be a dict literal")
            keys = set()
            for key in value:
                if not isinstance(key, str) or not key or len(key) > 64:
                    raise ValueError("PROJECT_UNITS contains invalid project id")
                keys.add(key)
            return keys
    raise ValueError("PROJECT_UNITS assignment not found")


def audit(projects_path: Path, enhancements_path: Path) -> dict[str, Any]:
    raw = _load_json(projects_path)
    if not isinstance(raw, list):
        raise ValueError("projects.json must contain a list")
    active: set[str] = set()
    duplicates: set[str] = set()
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("project entry must be an object")
        project_id = item.get("id")
        if not isinstance(project_id, str) or not project_id or len(project_id) > 64:
            raise ValueError("project entry has invalid id")
        if project_id in seen:
            duplicates.add(project_id)
        seen.add(project_id)
        if item.get("status") == "active":
            active.add(project_id)
    if duplicates:
        raise ValueError("duplicate project ids: " + ",".join(sorted(duplicates)))

    units = _project_units_keys(enhancements_path)
    missing = sorted(active - units)
    stale = sorted(units - seen)
    return {
        "format": "zcloud-project-units-coverage-v1",
        "active_project_count": len(active),
        "project_units_count": len(units),
        "missing_active_projects": missing,
        "stale_project_units": stale,
        "coverage_complete": not missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", default="projects.json")
    parser.add_argument("--enhancements", default="enhancements.py")
    args = parser.parse_args()
    report = audit(Path(args.projects), Path(args.enhancements))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail closed when a mass dependency upgrade is mixed with functional changes.

The guard is intentionally heuristic and bounded. It does not forbid dependency
maintenance. It only requires broad/large dependency churn to be isolated from
runtime or operational changes so each change-set has a smaller blast radius.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import PurePosixPath
from typing import Iterable

MASS_DEPENDENCY_LINE_THRESHOLD = 250
MASS_DEPENDENCY_FILE_THRESHOLD = 4


def dependency_group(path: str) -> str | None:
    p = path.lower().lstrip("./")
    name = PurePosixPath(p).name

    if (
        (name.startswith("requirements") and name.endswith(".txt"))
        or name in {"pyproject.toml", "poetry.lock", "pipfile", "pipfile.lock", "uv.lock"}
    ):
        return "python"
    if name in {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json"}:
        return "node"
    if name in {"gemfile", "gemfile.lock"}:
        return "ruby"
    if name in {"cargo.toml", "cargo.lock"}:
        return "rust"
    if name in {"go.mod", "go.sum"}:
        return "go"
    if name in {"pom.xml", "gradle.lockfile", "build.gradle", "build.gradle.kts"}:
        return "jvm"
    if name.endswith(".csproj") or name in {"packages.lock.json", "nuget.config"}:
        return "dotnet"
    return None


def is_nonfunctional_path(path: str) -> bool:
    p = path.lower().lstrip("./")
    name = PurePosixPath(p).name
    if p.startswith(("docs/", "tests/", "test/", ".github/issue_template/", ".github/pull_request_template")):
        return True
    if name.startswith(("readme", "changelog", "license", "contributing")):
        return True
    return p.endswith((".md", ".rst"))


def evaluate_change(changes: Iterable[dict]) -> dict:
    normalized = []
    for raw in changes:
        path = str(raw.get("path") or "").strip()
        if not path:
            raise ValueError("changed path is required")
        added = int(raw.get("added") or 0)
        deleted = int(raw.get("deleted") or 0)
        if added < 0 or deleted < 0:
            raise ValueError("line counts must be non-negative")
        normalized.append(
            {
                "path": path,
                "status": str(raw.get("status") or "M"),
                "added": added,
                "deleted": deleted,
            }
        )

    dependency = []
    functional = []
    groups = set()
    dependency_lines = 0

    for row in normalized:
        group = dependency_group(row["path"])
        if group:
            item = dict(row)
            item["group"] = group
            dependency.append(item)
            groups.add(group)
            dependency_lines += row["added"] + row["deleted"]
        elif not is_nonfunctional_path(row["path"]):
            functional.append(dict(row))

    reasons = []
    if len(groups) >= 2:
        reasons.append("multiple_dependency_ecosystems")
    if len(dependency) >= MASS_DEPENDENCY_FILE_THRESHOLD:
        reasons.append("many_dependency_files")
    if dependency_lines >= MASS_DEPENDENCY_LINE_THRESHOLD:
        reasons.append("large_dependency_diff")

    blocked = bool(reasons and functional)
    return {
        "schema_version": 1,
        "ok": not blocked,
        "blocked": blocked,
        "reason_codes": reasons,
        "dependency_groups": sorted(groups),
        "dependency_file_count": len(dependency),
        "dependency_changed_lines": dependency_lines,
        "functional_file_count": len(functional),
        "dependency_paths": sorted(row["path"] for row in dependency),
        "functional_paths": sorted(row["path"] for row in functional),
        "policy": {
            "line_threshold": MASS_DEPENDENCY_LINE_THRESHOLD,
            "file_threshold": MASS_DEPENDENCY_FILE_THRESHOLD,
            "rule": "isolate mass dependency upgrades from functional changes",
        },
    }


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git command failed").strip()
        raise RuntimeError(detail[:500])
    return proc.stdout


def collect_changes(base: str, head: str) -> list[dict]:
    range_spec = f"{base}...{head}"
    names = _git("diff", "--name-status", "--find-renames", range_spec)
    numstat = _git("diff", "--numstat", range_spec)

    counts: dict[str, tuple[int, int]] = {}
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw = parts[0], parts[1]
        path = parts[-1]
        if added_raw == "-" or deleted_raw == "-":
            counts[path] = (MASS_DEPENDENCY_LINE_THRESHOLD, 0)
            continue
        try:
            counts[path] = (int(added_raw), int(deleted_raw))
        except ValueError:
            continue

    changes = []
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        added, deleted = counts.get(path, (0, 0))
        changes.append({"path": path, "status": status, "added": added, "deleted": deleted})
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="base commit SHA/ref")
    parser.add_argument("--head", required=True, help="head commit SHA/ref")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = evaluate_change(collect_changes(args.base, args.head))
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "ok": False,
            "blocked": True,
            "reason_codes": ["guard_error"],
            "error": str(exc)[:500],
        }
        print(json.dumps(payload, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "DEPENDENCY_CHANGE_GUARD_"
            + ("GREEN" if result["ok"] else "BLOCKED")
            + " "
            + ",".join(result["reason_codes"])
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

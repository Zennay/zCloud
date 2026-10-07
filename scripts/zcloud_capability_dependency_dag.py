#!/usr/bin/env python3
"""Validate a bounded zCloud capability dependency DAG without mutating control-plane state."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 256 * 1024
MAX_CAPABILITIES = 300
MAX_DEPENDENCIES_PER_CAPABILITY = 64
DEFAULT_MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SKEW_SECONDS = 60

TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")
CAPABILITY_STATE = {"open", "complete", "cancelled"}


class SnapshotError(ValueError):
    """Raised when dependency evidence is malformed or stale."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _token(value: Any, code: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise SnapshotError(code)
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise SnapshotError("observed_at_invalid")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SnapshotError("observed_at_invalid") from exc
    if parsed.tzinfo is None:
        raise SnapshotError("observed_at_timezone_missing")
    return parsed.astimezone(timezone.utc)


def load_snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SnapshotError("snapshot_symlink_rejected")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SnapshotError("snapshot_unreadable") from exc
    if size > MAX_BYTES:
        raise SnapshotError("snapshot_too_large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("snapshot_invalid_json") from exc
    if not isinstance(data, dict):
        raise SnapshotError("snapshot_not_object")
    return data


def _find_cycle(graph: dict[str, tuple[str, ...]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def walk(node: str) -> list[str]:
        if node in visiting:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return []

        visiting.add(node)
        stack.append(node)
        for dependency in graph[node]:
            cycle = walk(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(graph):
        cycle = walk(node)
        if cycle:
            return cycle
    return []


def audit_snapshot(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise SnapshotError("max_age_invalid")
    if set(snapshot) != {"schema_version", "observed_at", "lane", "capabilities"}:
        raise SnapshotError("snapshot_keys_invalid")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise SnapshotError("schema_version_unsupported")

    lane = _token(snapshot["lane"], "lane_invalid")
    observed_at = _timestamp(snapshot["observed_at"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - observed_at).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise SnapshotError("snapshot_from_future")
    if age > max_age_seconds:
        raise SnapshotError("snapshot_stale")

    rows = snapshot["capabilities"]
    if not isinstance(rows, list) or len(rows) > MAX_CAPABILITIES:
        raise SnapshotError("capabilities_invalid")

    capabilities: dict[str, dict[str, Any]] = {}
    graph: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "lane",
            "state",
            "dependencies",
        }:
            raise SnapshotError("capability_invalid")
        capability_id = _token(row["id"], "capability_id_invalid")
        capability_lane = _token(row["lane"], "capability_lane_invalid")
        state = row["state"]
        if not isinstance(state, str) or state not in CAPABILITY_STATE:
            raise SnapshotError("capability_state_invalid")
        dependencies = row["dependencies"]
        if (
            not isinstance(dependencies, list)
            or len(dependencies) > MAX_DEPENDENCIES_PER_CAPABILITY
        ):
            raise SnapshotError("dependencies_invalid")

        normalized_dependencies: list[str] = []
        seen: set[str] = set()
        for value in dependencies:
            dependency_id = _token(value, "dependency_id_invalid")
            if dependency_id == capability_id:
                raise SnapshotError("self_dependency")
            if dependency_id in seen:
                raise SnapshotError("dependency_duplicate")
            seen.add(dependency_id)
            normalized_dependencies.append(dependency_id)
        canonical_dependencies = tuple(sorted(normalized_dependencies))

        if capability_id in capabilities:
            raise SnapshotError("capability_duplicate")
        capabilities[capability_id] = {
            "lane": capability_lane,
            "state": state,
            "dependencies": canonical_dependencies,
        }
        graph[capability_id] = canonical_dependencies

    for dependencies in graph.values():
        for dependency_id in dependencies:
            if dependency_id not in capabilities:
                raise SnapshotError("dependency_unknown")

    cycle = _find_cycle(graph)
    if cycle:
        return {
            "ok": False,
            "schema_version": 1,
            "lane": lane,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
            "state": "cycle",
            "ready_capability_ids": [],
            "waiting_capability_ids": [],
            "blocked_capability_ids": [],
            "dependency_gaps": {},
            "cycle": cycle,
            "counts": {
                "capabilities": len(capabilities),
                "selected_open": sum(
                    1
                    for row in capabilities.values()
                    if row["lane"] == lane and row["state"] == "open"
                ),
            },
        }

    ready: list[str] = []
    waiting: list[str] = []
    blocked: list[str] = []
    gaps: dict[str, list[str]] = {}

    for capability_id in sorted(capabilities):
        row = capabilities[capability_id]
        if row["lane"] != lane or row["state"] != "open":
            continue

        unresolved: list[str] = []
        cancelled: list[str] = []
        for dependency_id in row["dependencies"]:
            dependency_state = capabilities[dependency_id]["state"]
            if dependency_state == "cancelled":
                cancelled.append(dependency_id)
            elif dependency_state != "complete":
                unresolved.append(dependency_id)

        if cancelled:
            blocked.append(capability_id)
            gaps[capability_id] = sorted(cancelled + unresolved)
        elif unresolved:
            waiting.append(capability_id)
            gaps[capability_id] = sorted(unresolved)
        else:
            ready.append(capability_id)

    selected_open = len(ready) + len(waiting) + len(blocked)
    if ready:
        state = "ready"
    elif blocked:
        state = "blocked"
    elif waiting:
        state = "waiting"
    else:
        state = "complete"

    return {
        "ok": True,
        "schema_version": 1,
        "lane": lane,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "state": state,
        "ready_capability_ids": ready,
        "waiting_capability_ids": waiting,
        "blocked_capability_ids": blocked,
        "dependency_gaps": gaps,
        "cycle": [],
        "counts": {
            "capabilities": len(capabilities),
            "selected_open": selected_open,
            "ready": len(ready),
            "waiting": len(waiting),
            "blocked": len(blocked),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a bounded zCloud capability dependency DAG"
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
    )
    parser.add_argument("--require-acyclic", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit_snapshot(
            load_snapshot(args.snapshot),
            max_age_seconds=args.max_age_seconds,
        )
    except SnapshotError as exc:
        print(
            json.dumps(
                {"ok": False, "state": "incomplete", "errors": [exc.code]},
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    if args.require_acyclic and result["state"] == "cycle":
        return 2
    if args.require_ready and result["state"] != "ready":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

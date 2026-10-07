#!/usr/bin/env python3
"""Render evidence-backed project results as a small, read-only control-plane view."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import re
from typing import Any, Callable, Iterable

import enhancements

SCHEMA_VERSION = 1
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
KINDS = ("latest", "current", "best")
DEFAULT_LABELS = {
    "latest": "Nieuwste",
    "current": "Huidig",
    "best": "Beste gevalideerd",
}


class ProjectResultSummaryError(ValueError):
    """Raised when the requested projection cannot be represented safely."""


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:160]
    return None


def _project_point(kind: str, point: Any) -> dict[str, Any] | None:
    if not isinstance(point, dict):
        return None
    value = _safe_scalar(point.get("value"))
    if value is None or value == "":
        return None

    observed_at = _bounded_text(point.get("observed_at"), 80)
    source = _bounded_text(point.get("source"), 512)
    return {
        "kind": kind,
        "label": _bounded_text(point.get("label"), 120) or DEFAULT_LABELS[kind],
        "value": value,
        "unit": _bounded_text(point.get("unit"), 24),
        "note": _bounded_text(point.get("note"), 240),
        "validated": bool(point.get("validated")),
        "observed_at": observed_at or None,
        "evidence_present": bool(source and observed_at),
    }


def project_summary(project_id: str, quality: dict[str, Any]) -> dict[str, Any]:
    project_id = _bounded_text(project_id, 32).lower()
    if not PROJECT_RE.fullmatch(project_id):
        raise ProjectResultSummaryError(f"invalid project id: {project_id!r}")

    comparison = quality.get("comparison") if isinstance(quality, dict) else None
    comparison = comparison if isinstance(comparison, dict) else {}
    points = {kind: _project_point(kind, comparison.get(kind)) for kind in KINDS}
    points = {kind: point for kind, point in points.items() if point is not None}

    return {
        "project_id": project_id,
        "source_mode": "project_adapter" if bool(quality.get("available") or points) else "generic_only",
        "stage": _bounded_text(quality.get("stage"), 120) or None,
        "available": bool(points),
        "results": points,
        "result_count": len(points),
    }


def build_report(
    project_ids: Iterable[str] | None = None,
    *,
    quality_reader: Callable[[str], dict[str, Any]] = enhancements.quality_for,
    adapter_projects_reader: Callable[[], Iterable[str]] = enhancements.telemetry_adapter_projects,
) -> dict[str, Any]:
    requested = list(adapter_projects_reader()) if project_ids is None else list(project_ids)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        project_id = _bounded_text(raw, 32).lower()
        if not PROJECT_RE.fullmatch(project_id):
            raise ProjectResultSummaryError(f"invalid project id: {project_id!r}")
        if project_id not in seen:
            normalized.append(project_id)
            seen.add(project_id)

    if len(normalized) > 32:
        raise ProjectResultSummaryError("at most 32 projects may be projected")

    projects = [project_summary(project_id, quality_reader(project_id) or {}) for project_id in normalized]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_count": len(projects),
        "available_count": sum(1 for item in projects if item["available"]),
        "projects": projects,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render latest/current/best project evidence without mutating zCloud state."
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Project id to render. Repeat for more projects; defaults to all registered adapters.",
    )
    parser.add_argument(
        "--require-data",
        action="store_true",
        help="Exit 3 when one or more requested projects have no result comparison.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args.project or None)
    except ProjectResultSummaryError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "invalid", "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(report, sort_keys=True))
    if args.require_data and report["available_count"] != report["project_count"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

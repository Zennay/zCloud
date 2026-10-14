#!/usr/bin/env python3
"""Render evidence-backed project results as a small, read-only control-plane view."""

from __future__ import annotations

import argparse
import importlib.util
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _normalize_project_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) > 32 or not PROJECT_RE.fullmatch(text):
        raise ProjectResultSummaryError(f"invalid project id: {text!r}")
    return text


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


def _plain_status(points: dict[str, dict[str, Any]]) -> str:
    if not points:
        return "Geen projectspecifieke resultaatdata"

    latest = points.get("latest")
    current = points.get("current")
    best = points.get("best")

    if latest and not latest.get("validated"):
        return "Nieuw resultaat wacht op validatie"
    if current and best and current.get("value") == best.get("value") and best.get("validated"):
        return "Huidig resultaat is gevalideerd"
    if current:
        return "Huidig resultaat beschikbaar"
    if best and best.get("validated"):
        return "Gevalideerd resultaat beschikbaar"
    return "Resultaatdata beschikbaar"


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


def project_summary(
    project_id: str,
    quality: dict[str, Any] | Any,
    *,
    adapter_backed: bool | None = None,
) -> dict[str, Any]:
    project_id = _normalize_project_id(project_id)
    quality = quality if isinstance(quality, dict) else {}

    comparison = quality.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    points = {kind: _project_point(kind, comparison.get(kind)) for kind in KINDS}
    points = {kind: point for kind, point in points.items() if point is not None}

    if adapter_backed is None:
        adapter_backed = bool(quality.get("available") or comparison.get("available") or points)

    return {
        "project_id": project_id,
        "source_mode": "project_adapter" if adapter_backed else "generic_only",
        "stage": _bounded_text(quality.get("stage"), 120) or None,
        "available": bool(points),
        "evidence_state": "available" if points else "missing",
        "plain_status": _plain_status(points),
        "results": points,
        "result_count": len(points),
    }


def _load_adapter_contract():
    module_path = ROOT / "enhancements.py"
    spec = importlib.util.spec_from_file_location("_zcloud_project_result_enhancements", module_path)
    if spec is None or spec.loader is None:
        raise ProjectResultSummaryError("telemetry adapter contract cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "quality_for", None)):
        raise ProjectResultSummaryError("telemetry adapter quality reader is unavailable")
    if not callable(getattr(module, "telemetry_adapter_projects", None)):
        raise ProjectResultSummaryError("telemetry adapter registry is unavailable")
    return module


def build_report(
    project_ids: Iterable[str] | None = None,
    *,
    quality_reader: Callable[[str], dict[str, Any]] | None = None,
    adapter_projects_reader: Callable[[], Iterable[str]] | None = None,
) -> dict[str, Any]:
    if quality_reader is None or adapter_projects_reader is None:
        contract = _load_adapter_contract()
        quality_reader = quality_reader or contract.quality_for
        adapter_projects_reader = adapter_projects_reader or contract.telemetry_adapter_projects

    registered = {_normalize_project_id(item) for item in adapter_projects_reader()}
    requested = sorted(registered) if project_ids is None else list(project_ids)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        project_id = _normalize_project_id(raw)
        if project_id not in seen:
            normalized.append(project_id)
            seen.add(project_id)

    if len(normalized) > 32:
        raise ProjectResultSummaryError("at most 32 projects may be projected")

    projects = [
        project_summary(
            project_id,
            quality_reader(project_id) or {},
            adapter_backed=project_id in registered,
        )
        for project_id in normalized
    ]
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
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "status": "invalid", "error": str(exc)},
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(report, sort_keys=True))
    if args.require_data and report["available_count"] != report["project_count"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

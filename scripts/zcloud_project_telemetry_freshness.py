#!/usr/bin/env python3
"""Read-only freshness audit for project-specific zCloud telemetry adapters."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from typing import Callable, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_enhancements_module():
    path = _REPO_ROOT / "enhancements.py"
    spec = importlib.util.spec_from_file_location("_zcloud_telemetry_enhancements", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load zCloud enhancements module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATUSES = ("fresh", "stale", "missing", "incomplete", "generic_only")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: object, *, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} timestamp missing")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_regular_file(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} path must be a regular file")


def _active_project_ids(projects_path: Path) -> list[str]:
    _require_regular_file(projects_path, label="projects")
    try:
        payload = json.loads(projects_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("projects JSON is unreadable") from exc
    if not isinstance(payload, list):
        raise ValueError("projects JSON must be a list")

    result: list[str] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("project entries must be objects")
        project_id = str(row.get("id") or "").strip()
        if not project_id:
            raise ValueError("project id must be non-empty")
        if project_id in seen:
            raise ValueError(f"duplicate project id {project_id!r}")
        seen.add(project_id)
        if str(row.get("status") or "active").strip().lower() != "archived":
            result.append(project_id)
    if not result:
        raise ValueError("no active projects found")
    return result


def _evidence_points(snapshot: dict) -> list[dict]:
    points: list[dict] = []
    headline = snapshot.get("headline")
    if isinstance(headline, dict) and headline.get("value") not in (None, ""):
        points.append(headline)
    for row in snapshot.get("items") or []:
        if isinstance(row, dict) and row.get("value") not in (None, ""):
            points.append(row)
    comparison = snapshot.get("comparison") or {}
    if isinstance(comparison, dict):
        for key in ("latest", "current", "best"):
            point = comparison.get(key)
            if isinstance(point, dict) and point.get("value") not in (None, ""):
                points.append(point)
    return points


def _select_projects(active: list[str], requested: Iterable[str] | None) -> list[str]:
    if not requested:
        return active
    selected: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        project_id = str(raw or "").strip()
        if not project_id:
            raise ValueError("project filter must be non-empty")
        if project_id not in active:
            raise ValueError(f"unknown or archived project {project_id!r}")
        if project_id not in seen:
            seen.add(project_id)
            selected.append(project_id)
    return selected


def build_report(
    projects_path: Path,
    *,
    project_ids: list[str] | None = None,
    stale_minutes: int = 240,
    now: datetime | None = None,
    quality_reader: Callable[[str], dict] | None = None,
    adapter_projects_reader: Callable[[], Iterable[str]] | None = None,
) -> dict:
    if stale_minutes < 1 or stale_minutes > 7 * 24 * 60:
        raise ValueError("stale_minutes must be between 1 and 10080")

    observed_at = (now or _now()).astimezone(timezone.utc)
    if quality_reader is None or adapter_projects_reader is None:
        enhancements_module = _load_enhancements_module()
        if quality_reader is None:
            quality_reader = getattr(enhancements_module, "quality_for", None)
        if adapter_projects_reader is None:
            adapter_projects_reader = getattr(enhancements_module, "telemetry_adapter_projects", None)
    if not callable(quality_reader) or not callable(adapter_projects_reader):
        raise RuntimeError("zCloud telemetry adapter contract is unavailable")

    active = _active_project_ids(projects_path)
    selected = _select_projects(active, project_ids)
    adapter_projects = {str(value).strip() for value in adapter_projects_reader()}
    if "" in adapter_projects:
        raise ValueError("telemetry adapter project id must be non-empty")

    projects: list[dict] = []
    stale_after_seconds = stale_minutes * 60
    for project_id in selected:
        if project_id not in adapter_projects:
            projects.append(
                {
                    "project_id": project_id,
                    "has_adapter": False,
                    "status": "generic_only",
                    "warning": False,
                    "evidence_points": 0,
                    "missing_provenance_points": 0,
                    "newest_evidence_age_seconds": None,
                    "oldest_evidence_age_seconds": None,
                }
            )
            continue

        snapshot = quality_reader(project_id)
        if not isinstance(snapshot, dict):
            raise RuntimeError(f"{project_id} telemetry adapter returned a non-object snapshot")
        points = _evidence_points(snapshot)

        missing_provenance = 0
        ages: list[int] = []
        for index, point in enumerate(points):
            source = str(point.get("source") or "").strip()
            timestamp = str(point.get("observed_at") or "").strip()
            if not source or not timestamp:
                missing_provenance += 1
                continue
            parsed = _parse_timestamp(
                timestamp,
                label=f"{project_id} telemetry point {index}",
            )
            delta_seconds = int((observed_at - parsed).total_seconds())
            if delta_seconds < -300:
                raise ValueError(f"{project_id} telemetry timestamp is implausibly in the future")
            ages.append(max(0, delta_seconds))

        available = bool(snapshot.get("available"))
        if not available or not points:
            status = "missing"
        elif missing_provenance:
            status = "incomplete"
        elif not ages:
            status = "missing"
        elif min(ages) >= stale_after_seconds:
            status = "stale"
        else:
            status = "fresh"

        projects.append(
            {
                "project_id": project_id,
                "has_adapter": True,
                "status": status,
                "warning": status in {"stale", "missing", "incomplete"},
                "evidence_points": len(points),
                "missing_provenance_points": missing_provenance,
                "newest_evidence_age_seconds": min(ages) if ages else None,
                "oldest_evidence_age_seconds": max(ages) if ages else None,
            }
        )

    counts = {
        status: sum(1 for project in projects if project["status"] == status)
        for status in STATUSES
    }
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "stale_after_seconds": stale_after_seconds,
        "counts": counts,
        "warning_count": sum(1 for project in projects if project["warning"]),
        "projects": projects,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--stale-minutes", type=int, default=240)
    parser.add_argument("--now", help="Timezone-aware ISO timestamp for deterministic validation.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    observed_at = _parse_timestamp(args.now, label="--now") if args.now else None
    payload = build_report(
        args.projects,
        project_ids=args.project or None,
        stale_minutes=args.stale_minutes,
        now=observed_at,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

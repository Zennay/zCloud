#!/usr/bin/env python3
"""Evidence-only project progress dimensions derived from existing milestones."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECTS = ROOT / "projects.json"
SCHEMA_VERSION = 1
DIMENSIONS = ("research", "build", "validation", "operations")

# Conservative fallback only. An explicit milestone "dimension" field, when present,
# is canonical. If fallback rules match zero or multiple dimensions the milestone is
# deliberately left unclassified rather than guessing.
KEYWORDS = {
    "research": (
        "research",
        "analysis",
        "profiling",
        "training",
        "strategy generations",
        "discovery",
        "experiment",
    ),
    "build": (
        "pipeline",
        "engine",
        "model",
        "bot",
        "ui",
        "ux",
        "flow",
        "app",
        "api",
        "core",
        "cli",
        "integration",
        "architecture",
        "mvp",
        "workflow",
        "capabilities",
        "agent",
        "playbooks",
        "design",
        "foundation",
    ),
    "validation": (
        "validation",
        "validated",
        "test",
        "proof",
        "benchmark",
        "measurement",
        "measurements",
        "evaluation",
        "holdout",
        "qa",
        "proven",
        "real user",
    ),
    "operations": (
        "deploy",
        "deployment",
        "production",
        "runtime",
        "monitoring",
        "telemetry",
        "recovery",
        "release",
        "submission",
        "systemd",
        "install",
        "live match",
        "onboarding",
        "hardening",
        "ci",
    ),
}


class ProgressDimensionError(ValueError):
    """Raised when registry evidence cannot be interpreted safely."""


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _matches_keyword(title: str, keyword: str) -> bool:
    haystack = f" {_normalize(title)} "
    needle = f" {_normalize(keyword)} "
    return needle in haystack


def classify_milestone(milestone: dict[str, Any]) -> tuple[str | None, str]:
    explicit = milestone.get("dimension")
    if explicit is not None:
        if not isinstance(explicit, str):
            return None, "invalid_explicit"
        normalized = explicit.strip().lower()
        if normalized not in DIMENSIONS:
            return None, "invalid_explicit"
        return normalized, "explicit"

    title = milestone.get("title")
    if not isinstance(title, str) or not title.strip():
        return None, "unclassified"

    matches = [
        dimension
        for dimension, keywords in KEYWORDS.items()
        if any(_matches_keyword(title, keyword) for keyword in keywords)
    ]
    if len(matches) == 1:
        return matches[0], "keyword_v1"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "unclassified"


def _valid_progress(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 100:
        return None
    return numeric


def project_dimensions(project: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(project, dict):
        raise ProgressDimensionError("project_invalid")
    project_id = project.get("id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ProgressDimensionError("project_id_missing")

    milestones = project.get("milestones")
    if not isinstance(milestones, list):
        raise ProgressDimensionError("milestones_missing")

    buckets: dict[str, list[float]] = {dimension: [] for dimension in DIMENSIONS}
    counts = {
        "total_milestones": len(milestones),
        "valid_milestones": 0,
        "classified_milestones": 0,
        "explicit_milestones": 0,
        "inferred_milestones": 0,
        "ambiguous_milestones": 0,
        "unclassified_milestones": 0,
        "invalid_milestones": 0,
    }

    for milestone in milestones:
        if not isinstance(milestone, dict):
            counts["invalid_milestones"] += 1
            continue

        progress = _valid_progress(milestone.get("progress"))
        if progress is None:
            counts["invalid_milestones"] += 1
            continue
        counts["valid_milestones"] += 1

        dimension, basis = classify_milestone(milestone)
        if dimension is None:
            if basis == "ambiguous":
                counts["ambiguous_milestones"] += 1
            elif basis == "invalid_explicit":
                counts["invalid_milestones"] += 1
            else:
                counts["unclassified_milestones"] += 1
            continue

        buckets[dimension].append(progress)
        counts["classified_milestones"] += 1
        if basis == "explicit":
            counts["explicit_milestones"] += 1
        else:
            counts["inferred_milestones"] += 1

    dimensions = {}
    for dimension in DIMENSIONS:
        values = buckets[dimension]
        if not values:
            continue
        dimensions[dimension] = {
            "progress": round(sum(values) / len(values), 1),
            "milestone_count": len(values),
            "evidence": "mean_of_existing_milestone_progress",
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id.strip(),
        "status": "available" if dimensions else "unavailable",
        "dimensions": dimensions,
        "counts": counts,
        "source_revision": project.get("milestone_revision"),
        "progress_basis": project.get("progress_basis"),
    }


def load_registry(path: Path = DEFAULT_PROJECTS) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProgressDimensionError("projects_json_invalid") from exc
    if not isinstance(payload, list):
        raise ProgressDimensionError("projects_root_invalid")
    return payload, hashlib.sha256(raw).hexdigest()


def registry_dimensions(path: Path = DEFAULT_PROJECTS) -> dict[str, Any]:
    projects, source_sha256 = load_registry(path)
    results = [project_dimensions(project) for project in projects]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": path.name,
        "source_sha256": source_sha256,
        "projects": results,
        "project_count": len(results),
        "available_count": sum(item["status"] == "available" for item in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive evidence-only progress dimensions from zCloud milestones"
    )
    parser.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS)
    parser.add_argument("--project")
    parser.add_argument("--require-available", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = registry_dimensions(args.projects)
        if args.project:
            matches = [
                item for item in report["projects"]
                if item["project_id"] == args.project
            ]
            if not matches:
                raise ProgressDimensionError("project_unknown")
            report = {
                "schema_version": SCHEMA_VERSION,
                "source": report["source"],
                "source_sha256": report["source_sha256"],
                "project": matches[0],
            }
            available = matches[0]["status"] == "available"
        else:
            available = report["available_count"] > 0
        if args.require_available and not available:
            raise ProgressDimensionError("progress_dimensions_unavailable")
    except (OSError, ProgressDimensionError) as exc:
        error = str(exc) if isinstance(exc, ProgressDimensionError) else "projects_unreadable"
        if args.json:
            print(json.dumps({"ok": False, "error": error}, sort_keys=True))
        else:
            print(f"ZCLOUD_PROGRESS_DIMENSIONS_BLOCKED error={error}")
        return 2

    if args.json:
        print(json.dumps({"ok": True, "report": report}, sort_keys=True))
    else:
        print("ZCLOUD_PROGRESS_DIMENSIONS_GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

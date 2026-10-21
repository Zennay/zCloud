#!/usr/bin/env python3
"""Fail-closed freshness policy for durable Notion handoff evidence.

A normal Notion page last-edited timestamp is deliberately not accepted as
handoff evidence. Only an explicit durable handoff event may be classified as
fresh or stale.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "notion-handoff-freshness-v1"
SOURCE_SCHEMA_VERSION = "notion-handoff-source-v1"
ALLOWED_SOURCE_KIND = "durable_handoff_event"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PROJECTS = 100
MAX_STALE_MINUTES = 7 * 24 * 60
FUTURE_SKEW_SECONDS = 300
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _bounded_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.is_file():
        raise ValueError(f"{label} file is missing")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{label} file exceeds bounded size")


def _read_json(path: Path, label: str):
    _bounded_regular_file(path, label)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} file is not valid UTF-8 JSON") from exc


def _active_projects(path: Path) -> list[str]:
    payload = _read_json(path, "projects")
    if not isinstance(payload, list):
        raise ValueError("projects catalog must be a list")
    active: list[str] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("project catalog entries must be objects")
        status = str(item.get("status") or "active").strip().lower()
        if status == "archived":
            continue
        project_id = str(item.get("id") or "").strip()
        if not PROJECT_RE.fullmatch(project_id):
            raise ValueError("active project id is not canonical")
        if project_id in seen:
            raise ValueError("duplicate active project id")
        seen.add(project_id)
        active.append(project_id)
    if not active:
        raise ValueError("projects catalog has no active projects")
    if len(active) > MAX_PROJECTS:
        raise ValueError(f"active project count exceeds {MAX_PROJECTS}")
    return sorted(active)


def _parse_time(value: object, *, label: str, now: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} timestamp must be timezone-aware")
    parsed = parsed.astimezone(timezone.utc)
    if (parsed - now).total_seconds() > FUTURE_SKEW_SECONDS:
        raise ValueError(f"{label} timestamp is implausibly in the future")
    return parsed


def _load_source(path: Path, active: set[str], now: datetime) -> tuple[datetime, dict[str, dict]]:
    payload = _read_json(path, "handoff source")
    if not isinstance(payload, dict):
        raise ValueError("handoff source must be an object")
    if payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError("handoff source schema_version is unsupported")
    captured_at = _parse_time(payload.get("captured_at"), label="captured_at", now=now)
    rows = payload.get("handoffs")
    if not isinstance(rows, list):
        raise ValueError("handoffs must be a list")
    if len(rows) > MAX_PROJECTS:
        raise ValueError(f"handoff row count exceeds {MAX_PROJECTS}")

    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("handoff rows must be objects")
        allowed = {"project_id", "source_kind", "handoff_at", "recorded_at"}
        if set(row) - allowed:
            raise ValueError("handoff row contains unsupported fields")
        project_id = str(row.get("project_id") or "").strip()
        if not PROJECT_RE.fullmatch(project_id):
            raise ValueError("handoff project id is not canonical")
        if project_id not in active:
            raise ValueError("handoff source references unknown or archived project")
        if project_id in result:
            raise ValueError("duplicate handoff project id")
        source_kind = str(row.get("source_kind") or "").strip()
        handoff_at = _parse_time(row.get("handoff_at"), label="handoff_at", now=now)
        recorded_at = _parse_time(row.get("recorded_at"), label="recorded_at", now=now)
        if handoff_at > recorded_at:
            raise ValueError("handoff_at must not be after recorded_at")
        if recorded_at > captured_at:
            raise ValueError("recorded_at must not be after captured_at")
        result[project_id] = {
            "source_kind": source_kind,
            "handoff_at": handoff_at,
            "recorded_at": recorded_at,
        }
    return captured_at, result


def build_report(
    projects_path: Path,
    source_path: Path,
    *,
    stale_minutes: int = 240,
    now: datetime | None = None,
) -> dict:
    if stale_minutes < 1 or stale_minutes > MAX_STALE_MINUTES:
        raise ValueError(f"stale_minutes must be between 1 and {MAX_STALE_MINUTES}")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("reference time must be timezone-aware")
    reference = reference.astimezone(timezone.utc)

    project_ids = _active_projects(projects_path)
    captured_at, source = _load_source(source_path, set(project_ids), reference)
    stale_after_seconds = stale_minutes * 60

    projects: list[dict] = []
    for project_id in project_ids:
        row = source.get(project_id)
        if row is None:
            projects.append({
                "project_id": project_id,
                "status": "missing",
                "warning": True,
                "reason": "durable_handoff_event_missing",
                "age_seconds": None,
            })
            continue

        if row["source_kind"] != ALLOWED_SOURCE_KIND:
            status = "invalid"
            reason = (
                "page_age_proxy_forbidden"
                if row["source_kind"] in {"notion_page_last_edited", "page_last_edited", "page_age"}
                else "unsupported_source_kind"
            )
            projects.append({
                "project_id": project_id,
                "status": status,
                "warning": True,
                "reason": reason,
                "age_seconds": None,
            })
            continue

        age_seconds = max(0, int((reference - row["handoff_at"]).total_seconds()))
        status = "stale" if age_seconds >= stale_after_seconds else "fresh"
        projects.append({
            "project_id": project_id,
            "status": status,
            "warning": status != "fresh",
            "reason": "handoff_too_old" if status == "stale" else "durable_handoff_fresh",
            "age_seconds": age_seconds,
        })

    counts = Counter(item["status"] for item in projects)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": reference.isoformat(),
        "source_captured_at": captured_at.isoformat(),
        "stale_after_seconds": stale_after_seconds,
        "ready": all(item["status"] == "fresh" for item in projects),
        "counts": {
            "fresh": counts.get("fresh", 0),
            "stale": counts.get("stale", 0),
            "missing": counts.get("missing", 0),
            "invalid": counts.get("invalid", 0),
        },
        "source_contract": {
            "required_kind": ALLOWED_SOURCE_KIND,
            "page_last_edited_is_handoff_evidence": False,
        },
        "projects": projects,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--projects", type=Path, default=root / "projects.json")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--stale-minutes", type=int, default=240)
    parser.add_argument("--now")
    parser.add_argument("--require-fresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SystemExit(f"--now timestamp invalid: {exc}") from exc

    payload = build_report(
        args.projects,
        args.source,
        stale_minutes=args.stale_minutes,
        now=now,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if (payload["ready"] or not args.require_fresh) else 3


if __name__ == "__main__":
    raise SystemExit(main())

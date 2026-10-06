#!/usr/bin/env python3
"""Read-only evidence-based project throughput from immutable queue completion receipts."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_HOURS = 24
MAX_HOURS = 720
REQUIRED_COLUMNS = {
    "id",
    "project_id",
    "ci_status",
    "source",
    "observed_at",
    "evidence_json",
}


def _parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is empty")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _validate_db_path(db_path: Path) -> Path:
    path = Path(db_path)
    if path.is_symlink():
        raise ValueError("database path must not be a symlink")
    if not path.exists() or not path.is_file():
        raise ValueError("database path must be an existing regular file")
    return path.resolve()


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    resolved = _validate_db_path(db_path)
    connection = sqlite3.connect(
        f"file:{resolved}?mode=ro",
        uri=True,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA query_only=ON")
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(project_state_receipts)").fetchall()
    }
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        connection.close()
        raise ValueError(
            "project_state_receipts missing required columns: " + ",".join(missing)
        )
    return connection


def build_report(
    db_path: Path,
    *,
    hours: int = DEFAULT_HOURS,
    projects: list[str] | None = None,
    now_value: str | None = None,
) -> dict:
    try:
        hours = int(hours)
    except (TypeError, ValueError) as exc:
        raise ValueError("hours must be an integer") from exc
    if hours < 1 or hours > MAX_HOURS:
        raise ValueError(f"hours must be between 1 and {MAX_HOURS}")

    now_dt = _parse_utc(now_value) if now_value else datetime.now(timezone.utc)
    start_dt = now_dt - timedelta(hours=hours)
    selected_projects = sorted(
        {
            str(project).strip().lower()
            for project in (projects or [])
            if str(project).strip()
        }
    )
    project_filter = set(selected_projects)

    connection = _connect_read_only(Path(db_path))
    try:
        rows = connection.execute(
            """SELECT id,project_id,ci_status,source,observed_at,evidence_json
               FROM project_state_receipts
               WHERE source LIKE 'portfolio_queue:%'
               ORDER BY id"""
        ).fetchall()
    finally:
        connection.close()

    malformed_receipts = 0
    malformed_receipt_ids: list[int] = []
    duplicate_done_receipts = 0
    seen_queue_ids: dict[str, tuple[int, datetime, str]] = {}
    completions: list[tuple[str, str, datetime, int]] = []

    for row in rows:
        receipt_id = int(row["id"])
        project_id = str(row["project_id"] or "").strip().lower()
        source = str(row["source"] or "").strip()
        queue_id = source.split(":", 1)[1].strip() if ":" in source else ""
        try:
            observed_at = _parse_utc(row["observed_at"])
            evidence = json.loads(str(row["evidence_json"] or "{}"))
            if not isinstance(evidence, dict):
                raise ValueError("evidence must be an object")
        except (TypeError, ValueError, json.JSONDecodeError):
            malformed_receipts += 1
            malformed_receipt_ids.append(receipt_id)
            continue

        if not project_id or not queue_id:
            malformed_receipts += 1
            malformed_receipt_ids.append(receipt_id)
            continue
        if str(row["ci_status"] or "").strip().lower() != "success":
            continue
        if str(evidence.get("result") or "").strip().upper() != "DONE":
            continue
        evidence_queue_id = str(evidence.get("queue_id") or "").strip()
        if evidence_queue_id and evidence_queue_id != queue_id:
            malformed_receipts += 1
            malformed_receipt_ids.append(receipt_id)
            continue
        if project_filter and project_id not in project_filter:
            continue

        previous = seen_queue_ids.get(queue_id)
        if previous is not None:
            duplicate_done_receipts += 1
            if observed_at >= previous[1]:
                continue
            completions = [
                item for item in completions
                if not (item[1] == queue_id and item[3] == previous[0])
            ]
        seen_queue_ids[queue_id] = (receipt_id, observed_at, project_id)
        if start_dt <= observed_at <= now_dt:
            completions.append((project_id, queue_id, observed_at, receipt_id))

    counts: dict[str, int] = defaultdict(int)
    last_completed: dict[str, datetime] = {}
    hourly_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    daily_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for project_id, _queue_id, observed_at, _receipt_id in completions:
        counts[project_id] += 1
        previous = last_completed.get(project_id)
        if previous is None or observed_at > previous:
            last_completed[project_id] = observed_at
        hour_key = observed_at.strftime("%Y-%m-%dT%H:00:00Z")
        day_key = observed_at.strftime("%Y-%m-%d")
        hourly_counts[project_id][hour_key] += 1
        daily_counts[project_id][day_key] += 1

    all_projects = sorted(set(counts) | set(selected_projects))
    project_rows = {}
    for project_id in all_projects:
        completed = int(counts.get(project_id, 0))
        project_rows[project_id] = {
            "completed": completed,
            "completed_per_hour": round(completed / hours, 4),
            "completed_per_day": round(completed * 24 / hours, 4),
            "last_completed_at": (
                _iso(last_completed[project_id])
                if project_id in last_completed
                else None
            ),
            "hourly": dict(sorted(hourly_counts.get(project_id, {}).items())),
            "daily": dict(sorted(daily_counts.get(project_id, {}).items())),
        }

    total_completed = sum(counts.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now_dt),
        "window": {
            "hours": hours,
            "start": _iso(start_dt),
            "end": _iso(now_dt),
        },
        "coverage_complete": malformed_receipts == 0,
        "malformed_receipts": malformed_receipts,
        "malformed_receipt_ids": malformed_receipt_ids[:50],
        "duplicate_done_receipts": duplicate_done_receipts,
        "total_completed": total_completed,
        "completed_per_hour": round(total_completed / hours, 4),
        "completed_per_day": round(total_completed * 24 / hours, 4),
        "projects": project_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report meaningful completed portfolio work-items per project from "
            "append-only project_state_receipts. The database is opened read-only."
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Optional project id filter; may be repeated.",
    )
    args = parser.parse_args()
    report = build_report(args.db, hours=args.hours, projects=args.project)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

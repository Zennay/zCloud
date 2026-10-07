#!/usr/bin/env python3
"""Read-only activity-source coverage audit for every zCloud project contract."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_STALE_HOURS = 24 * 90


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _open_ro(db: Path) -> sqlite3.Connection:
    if db.is_symlink():
        raise ValueError("refusing symlink database path")
    if not db.is_file():
        raise FileNotFoundError(db)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _source_stats(
    connection: sqlite3.Connection,
    table: str,
    project_column: str,
    projects: list[str],
) -> dict[str, dict]:
    if not projects:
        return {}
    placeholders = ",".join("?" for _ in projects)
    rows = connection.execute(
        f"SELECT {project_column} project_id,COUNT(*) event_count,MAX(ts) latest_at "
        f"FROM {table} WHERE {project_column} IN ({placeholders}) GROUP BY {project_column}",
        projects,
    ).fetchall()
    return {
        str(row["project_id"]): {
            "event_count": int(row["event_count"]),
            "latest_at": str(row["latest_at"]) if row["latest_at"] else None,
        }
        for row in rows
    }


def _load_contract_projects(path: Path) -> list[str]:
    if path.is_symlink():
        raise ValueError("refusing symlink project-contract path")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) < 1:
        raise ValueError("project contracts require schema_version >= 1")
    projects = payload.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("project contracts contain no projects")
    ids = [str(project_id).strip() for project_id in projects]
    if any(not project_id for project_id in ids):
        raise ValueError("project id must be non-empty")
    return sorted(ids)


def report(
    db: Path,
    contracts: Path,
    stale_hours: float = 168.0,
    now: datetime | None = None,
) -> dict:
    stale_hours = float(stale_hours)
    if not (1.0 <= stale_hours <= MAX_STALE_HOURS):
        raise ValueError(f"stale_hours must be between 1 and {MAX_STALE_HOURS}")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now - timedelta(hours=stale_hours)
    projects = _load_contract_projects(Path(contracts))

    with closing(_open_ro(Path(db))) as connection:
        runner_cols = _columns(connection, "runner_events")
        generic_cols = _columns(connection, "events")
        if not {"ts", "project_id"}.issubset(runner_cols):
            missing = sorted({"ts", "project_id"} - runner_cols)
            raise RuntimeError("runner_events missing required columns: " + ", ".join(missing))
        runner = _source_stats(connection, "runner_events", "project_id", projects)
        generic = (
            _source_stats(connection, "events", "project", projects)
            if {"ts", "project"}.issubset(generic_cols)
            else {}
        )

    rows = []
    for project_id in projects:
        sources = []
        total = 0
        latest_values: list[datetime] = []
        for source_name, stats_by_project in (
            ("runner_events", runner),
            ("events", generic),
        ):
            stats = stats_by_project.get(project_id)
            if not stats:
                continue
            total += int(stats["event_count"])
            latest = _dt(stats["latest_at"])
            if latest:
                latest_values.append(latest)
            sources.append({
                "source": source_name,
                "event_count": int(stats["event_count"]),
                "latest_at": stats["latest_at"],
            })
        latest = max(latest_values) if latest_values else None
        if total == 0:
            state = "never_seen"
        elif latest is None:
            state = "timestamp_invalid"
        elif latest < cutoff:
            state = "inactive_or_stale"
        else:
            state = "recent"
        rows.append({
            "project_id": project_id,
            "state": state,
            "event_count": total,
            "latest_at": latest.isoformat() if latest else None,
            "sources": sources,
        })

    summary = {
        state: sum(1 for row in rows if row["state"] == state)
        for state in ("recent", "inactive_or_stale", "never_seen", "timestamp_invalid")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "stale_hours": stale_hours,
        "cutoff": cutoff.isoformat(),
        "method": "read-only coverage inventory across runner_events and generic events; inactivity alone is not treated as a logging defect",
        "summary": summary,
        "projects": rows,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Audit zCloud activity-source coverage")
    parser.add_argument("--db", type=Path, default=root / "history.db")
    parser.add_argument("--contracts", type=Path, default=root / "project-contracts.json")
    parser.add_argument("--stale-hours", type=float, default=168.0)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        report(args.db, args.contracts, args.stale_hours),
        ensure_ascii=False,
        indent=2 if args.pretty else None,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

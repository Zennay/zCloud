#!/usr/bin/env python3
"""Compute bounded success/failure ratios for zCloud runner actions."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "zcloud-action-success-ratio-v1"
ACTION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
COMMAND_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:::[a-z0-9][a-z0-9_-]{0,63})?$")
MAX_HOURS = 24 * 90


class RatioError(RuntimeError):
    pass


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def build_report(
    db_path: Path,
    *,
    hours: int = 24,
    project: str | None = None,
    now: datetime | None = None,
) -> dict:
    if hours < 1 or hours > MAX_HOURS:
        raise ValueError(f"hours must be between 1 and {MAX_HOURS}")
    if project is not None and not PROJECT_RE.fullmatch(project):
        raise ValueError("project must be a lowercase zCloud project identifier")
    path = Path(db_path)
    if path.is_symlink():
        raise RatioError("database symlink refused")
    if not path.is_file():
        raise RatioError("database file missing")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    since = (current.astimezone(timezone.utc) - timedelta(hours=hours)).isoformat()

    uri = f"file:{path.resolve()}?mode=ro"
    malformed = 0
    groups: dict[str, dict[str, int]] = {}
    with sqlite3.connect(uri, uri=True) as conn:
        required = {"id", "project_id", "action", "status", "updated_at"}
        columns = _columns(conn, "runner_commands")
        if not columns:
            raise RatioError("runner_commands table missing")
        missing = required - columns
        if missing:
            raise RatioError("runner_commands missing required columns: " + ",".join(sorted(missing)))

        where = "status IN ('completed','failed') AND updated_at>=?"
        args: list[object] = [since]
        if project:
            where += " AND (project_id=? OR project_id LIKE ?)"
            args.extend((project, project + "::%"))
        rows = conn.execute(
            "SELECT project_id,action,status,updated_at FROM runner_commands "
            f"WHERE {where} ORDER BY id DESC",
            tuple(args),
        )
        for row in rows:
            action = str(row[1] or "").strip()
            status = str(row[2] or "").strip().lower()
            pid = str(row[0] or "").strip().lower()
            try:
                ts = datetime.fromisoformat(str(row[3]).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                malformed += 1
                continue
            if not ACTION_RE.fullmatch(action):
                malformed += 1
                continue
            if pid and not COMMAND_PROJECT_RE.fullmatch(pid):
                malformed += 1
                continue
            bucket = groups.setdefault(action, {"completed": 0, "failed": 0})
            bucket[status] += 1

    actions = []
    total_completed = total_failed = 0
    for action in sorted(groups):
        completed = groups[action]["completed"]
        failed = groups[action]["failed"]
        attempts = completed + failed
        total_completed += completed
        total_failed += failed
        actions.append(
            {
                "action": action,
                "attempts": attempts,
                "completed": completed,
                "failed": failed,
                "success_ratio": round(completed / attempts, 4) if attempts else None,
            }
        )
    total_attempts = total_completed + total_failed
    return {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "window_hours": hours,
        "since": since,
        "coverage_complete": malformed == 0,
        "malformed_rows": malformed,
        "summary": {
            "attempts": total_attempts,
            "completed": total_completed,
            "failed": total_failed,
            "success_ratio": round(total_completed / total_attempts, 4) if total_attempts else None,
        },
        "actions": actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--project")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.db, hours=args.hours, project=args.project)
    except (RatioError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["coverage_complete"] or not args.require_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only runner command outcome report for zCloud control-plane actions."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

REQUIRED_COLUMNS = {"action", "status", "created_at", "updated_at"}
TERMINAL_STATUSES = {"completed", "failed"}
KNOWN_STATUSES = TERMINAL_STATUSES | {"pending"}


class ReportError(RuntimeError):
    pass


def _db_uri(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if path.is_symlink():
        raise ReportError("database path must not be a symlink")
    if not resolved.is_file():
        raise ReportError("database path must be a regular file")
    return f"file:{resolved.as_posix()}?mode=ro"


def _connect(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(_db_uri(path), uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise ReportError(f"unable to open database read-only: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _validate_schema(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_commands'"
    ).fetchone()
    if not table:
        raise ReportError("runner_commands table missing")
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(runner_commands)").fetchall()
    }
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise ReportError("runner_commands missing required columns: " + ",".join(missing))


def build_report(db_path: str | os.PathLike[str], hours: int = 24) -> dict[str, Any]:
    if hours < 1 or hours > 720:
        raise ReportError("hours must be between 1 and 720")

    path = Path(db_path)
    timestamp_expr = "COALESCE(NULLIF(updated_at,''), created_at)"
    with _connect(path) as conn:
        _validate_schema(conn)
        malformed_timestamps = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM runner_commands
                WHERE unixepoch({timestamp_expr}) IS NULL
                """
            ).fetchone()[0]
        )
        rows = conn.execute(
            f"""
            SELECT action, status, COUNT(*) AS count
            FROM runner_commands
            WHERE unixepoch({timestamp_expr}) >= unixepoch('now', ?)
            GROUP BY action, status
            ORDER BY action, status
            """,
            (f"-{hours} hours",),
        ).fetchall()

    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        action = str(row["action"] or "").strip() or "<empty>"
        status = str(row["status"] or "").strip() or "<empty>"
        grouped.setdefault(action, {})[status] = int(row["count"] or 0)

    actions = []
    totals = {
        "completed": 0,
        "failed": 0,
        "pending": 0,
        "unknown": 0,
        "terminal": 0,
        "commands": 0,
    }
    for action in sorted(grouped):
        statuses = grouped[action]
        completed = int(statuses.get("completed", 0))
        failed = int(statuses.get("failed", 0))
        pending = int(statuses.get("pending", 0))
        unknown = sum(
            count for status, count in statuses.items() if status not in KNOWN_STATUSES
        )
        commands = sum(statuses.values())
        terminal = completed + failed
        success_rate = round((completed / terminal) * 100.0, 2) if terminal else None
        failure_rate = round((failed / terminal) * 100.0, 2) if terminal else None
        actions.append(
            {
                "action": action,
                "commands": commands,
                "terminal": terminal,
                "completed": completed,
                "failed": failed,
                "pending": pending,
                "unknown_status": unknown,
                "success_rate_pct": success_rate,
                "failure_rate_pct": failure_rate,
            }
        )
        totals["completed"] += completed
        totals["failed"] += failed
        totals["pending"] += pending
        totals["unknown"] += unknown
        totals["terminal"] += terminal
        totals["commands"] += commands

    totals["success_rate_pct"] = (
        round((totals["completed"] / totals["terminal"]) * 100.0, 2)
        if totals["terminal"]
        else None
    )
    totals["failure_rate_pct"] = (
        round((totals["failed"] / totals["terminal"]) * 100.0, 2)
        if totals["terminal"]
        else None
    )

    return {
        "ok": True,
        "window_hours": hours,
        "source": "runner_commands",
        "semantics": {
            "success": "status=completed",
            "failure": "status=failed",
            "excluded_from_ratio": ["pending", "unknown_status"],
            "payload_fields_read": ["action", "status", "created_at", "updated_at"],
        },
        "data_quality": {
            "timestamp_coverage_complete": malformed_timestamps == 0,
            "unparseable_timestamp_rows": malformed_timestamps,
        },
        "totals": totals,
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report read-only zCloud runner-command success/failure ratios."
    )
    parser.add_argument("db", help="Path to zCloud history.db")
    parser.add_argument("--hours", type=int, default=24, help="Window in hours (1..720)")
    args = parser.parse_args()

    try:
        report = build_report(args.db, args.hours)
    except (ReportError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

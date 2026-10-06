#!/usr/bin/env python3
"""Read-only runner_events retention inventory.

Outputs only aggregate counts and timestamps. Event payload fields such as target,
reason, error, title, conversation ids and assistant content are never selected.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


class RetentionAuditError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _open_ro(path: Path) -> sqlite3.Connection:
    if path.is_symlink():
        raise RetentionAuditError("refusing symlink database path")
    if not path.is_file():
        raise RetentionAuditError(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def audit(db: Path, *, observed_at: datetime | None = None) -> dict:
    observed_at = (observed_at or utc_now()).astimezone(timezone.utc)
    cutoffs = {
        "older_7d": _iso(observed_at - timedelta(days=7)),
        "older_30d": _iso(observed_at - timedelta(days=30)),
        "older_90d": _iso(observed_at - timedelta(days=90)),
    }
    with _open_ro(db) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_events'"
        ).fetchone()
        if not table:
            raise RetentionAuditError("runner_events table missing")

        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(runner_events)").fetchall()
        }
        required = {"id", "ts", "event"}
        if not required.issubset(columns):
            raise RetentionAuditError(
                f"runner_events missing required columns: {sorted(required - columns)}"
            )

        rows = conn.execute(
            """SELECT
                   event,
                   COUNT(*) AS total,
                   MIN(ts) AS oldest_ts,
                   MAX(ts) AS newest_ts,
                   SUM(CASE WHEN ts<? THEN 1 ELSE 0 END) AS older_7d,
                   SUM(CASE WHEN ts<? THEN 1 ELSE 0 END) AS older_30d,
                   SUM(CASE WHEN ts<? THEN 1 ELSE 0 END) AS older_90d
               FROM runner_events
               GROUP BY event
               ORDER BY total DESC,event""",
            (
                cutoffs["older_7d"],
                cutoffs["older_30d"],
                cutoffs["older_90d"],
            ),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM runner_events").fetchone()["n"]
        max_id = conn.execute(
            "SELECT COALESCE(MAX(id),0) AS max_id FROM runner_events"
        ).fetchone()["max_id"]
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])

    events = [
        {
            "event": str(row["event"] or ""),
            "total": int(row["total"] or 0),
            "oldest_ts": row["oldest_ts"],
            "newest_ts": row["newest_ts"],
            "older_7d": int(row["older_7d"] or 0),
            "older_30d": int(row["older_30d"] or 0),
            "older_90d": int(row["older_90d"] or 0),
        }
        for row in rows
    ]
    return {
        "ok": True,
        "mode": "read-only",
        "observed_at": _iso(observed_at),
        "runner_events": {
            "total": int(total or 0),
            "max_id": int(max_id or 0),
            "event_types": len(events),
            "events": events,
        },
        "database": {
            "page_count": page_count,
            "page_size": page_size,
            "allocated_bytes": page_count * page_size,
        },
        "selected_columns": ["event", "ts", "id"],
        "sensitive_fields_selected": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only aggregate retention inventory for zCloud runner_events"
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = audit(args.db)
    except RetentionAuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bounded, non-mutating local SQLite counters for a manual Firefox diagnostic.

Never call zCloud HTTP GET status handlers: they may reconcile and mutate the
queue database (#1341). Never emit worker IDs, prompts, URLs or SQLite errors.
"""
from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

DEFAULT_DB = Path("/home/ubuntu/zennay-cloud/history.db")
SAFE_TABLES = ("runner_workers", "portfolio_queue")


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Use SQLite read-only VFS plus query_only defense-in-depth."""
    db_path = Path(db_path)
    if not db_path.is_absolute():
        raise ValueError("absolute_path_required")
    for entry in (db_path, *db_path.parents):
        if entry.is_symlink():
            raise ValueError("symlink_path_denied")
    mode = db_path.stat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError("not_regular_file")
    uri = "file:" + quote(str(db_path), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise ValueError("query_only_not_enabled")
        return connection
    except BaseException:
        connection.close()
        raise


def snapshot(db_path: Path) -> dict[str, object]:
    conn = open_readonly(db_path)
    try:
        available = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name IN ('runner_workers', 'portfolio_queue')"
            )
        }
        counts: dict[str, int | None] = {}
        for table in SAFE_TABLES:
            # Table names are compile-time constants; never interpolated from DB.
            counts[table] = (
                int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                if table in available else None
            )
        return {
            "ok": True,
            "read_only": True,
            "mutations_performed": False,
            "runner_worker_rows": counts["runner_workers"],
            "queue_rows": counts["portfolio_queue"],
            "worker_generation_verified": False,
            "production_healthy_verified": False,
        }
    finally:
        conn.close()


def main() -> int:
    try:
        result = snapshot(DEFAULT_DB)
    except (OSError, sqlite3.Error, ValueError):
        # Fail closed; never expose the filesystem path or raw SQLite rows.
        print("FIREFOX_DIAGNOSTIC_SQLITE_UNAVAILABLE=1")
        return 2
    print("FIREFOX_DIAGNOSTIC_SQLITE=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

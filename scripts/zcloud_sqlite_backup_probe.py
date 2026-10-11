#!/usr/bin/env python3
"""Bounded SQLite backup readability probe; never restores production state."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

TABLES = ("project_state_receipts", "resource_leases", "portfolio_queue", "task_claims")


def probe_backup(path: Path, *, max_pages: int = 10000, timeout_seconds: float = 5.0) -> dict:
    if type(max_pages) is not int or not 1 <= max_pages <= 10000:
        raise ValueError("invalid page bound")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30 or not math.isfinite(timeout_seconds):
        raise ValueError("invalid time bound")
    path = Path(path).absolute()
    if not path.is_file():
        raise ValueError("existing SQLite database required")
    deadline = time.monotonic() + timeout_seconds

    def progress(status, remaining, total):
        if total > max_pages or total * page_size > 40 * 1024 * 1024 or time.monotonic() >= deadline:
            raise ValueError("backup probe exceeded its bound")

    # as_uri percent-encodes #, ?, &, spaces and Unicode before adding mode=ro.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                 timeout=min(timeout_seconds, 1.0))) as source:
        source.execute("PRAGMA query_only=ON")
        page_count = source.execute("PRAGMA page_count").fetchone()[0]
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        if page_count > max_pages or page_size * page_count > 40 * 1024 * 1024:
            raise ValueError("backup probe exceeded its bound")
        with closing(sqlite3.connect(":memory:")) as restored:
            source.backup(restored, pages=64, progress=progress, sleep=0.05)
            restored.execute("PRAGMA query_only=ON")
            restored.set_progress_handler(
                lambda: int(time.monotonic() >= deadline), 1000)
            try:
                integrity_ok = restored.execute("PRAGMA quick_check").fetchmany(2) == [("ok",)]
                foreign_keys_ok = restored.execute("SELECT 1 FROM pragma_foreign_key_check LIMIT 1").fetchone() is None
                schema = restored.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                present = {name for name, sql in schema}
                counts = {
                    name: restored.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                    for name in TABLES if name in present
                }
            finally:
                restored.set_progress_handler(None, 0)
            if time.monotonic() >= deadline:
                raise ValueError("backup probe exceeded its bound")
    missing = [name for name in ("project_state_receipts", "resource_leases") if name not in counts]
    return {
        "schema_version": 1,
        "probe": "sqlite-backup-readability-v1",
        "readable": integrity_ok and foreign_keys_ok and not missing,
        "quick_check_ok": integrity_ok,
        "foreign_keys_ok": foreign_keys_ok,
        "required_tables_missing": missing,
        "row_counts": counts,
        "schema_sha256": hashlib.sha256(json.dumps(schema, ensure_ascii=False).encode()).hexdigest(),
        "source_rows_modified": False,
        "production_restore_performed": False,
        "authority_granted": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=10000)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        report = probe_backup(args.db, max_pages=args.max_pages,
                              timeout_seconds=args.timeout_seconds)
    except (ValueError, OSError, sqlite3.Error):
        print(json.dumps({"readable": False, "reason": "probe_unavailable",
                          "production_restore_performed": False, "authority_granted": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["readable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Restart-safe, privacy-bounded persistence for zCloud worker-attention cycles."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
POLICY = "worker-attention-persistence-v1"
MAX_ID_LENGTH = 128
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")

CATEGORIES = {
    "feature_code",
    "bugfix",
    "architecture",
    "validation",
    "polling_waiting",
    "audit",
    "recovery_retry",
    "vps_delegable",
}
MATERIAL_FIELDS = (
    "commit_count",
    "code_change_count",
    "milestone_move_count",
    "blocker_removed_count",
    "deploy_outcome_count",
)
ROW_KEYS = {
    "cycle_id",
    "worker_id",
    "project_id",
    "category",
    "occurred_at",
    *MATERIAL_FIELDS,
}


class AttentionStoreError(ValueError):
    """Bounded validation or store error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _token(value: Any, code: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise AttentionStoreError(code)
    return value


def _timestamp(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AttentionStoreError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttentionStoreError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AttentionStoreError(code)
    return parsed.astimezone(dt.timezone.utc).isoformat()


def _counter(value: Any, code: str) -> int:
    if type(value) is not int or value < 0 or value > 1_000_000:
        raise AttentionStoreError(code)
    return value


def open_store(path: Path) -> sqlite3.Connection:
    """Open/create an explicit SQLite store without following a symlink."""
    path = Path(path)
    if path.exists() and path.is_symlink():
        raise AttentionStoreError("db_symlink_rejected")
    if path.exists() and not path.is_file():
        raise AttentionStoreError("db_not_regular")
    if not path.parent.exists() or not path.parent.is_dir():
        raise AttentionStoreError("db_parent_invalid")

    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS worker_attention_schema (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO worker_attention_schema(singleton, schema_version)
        VALUES (1, 1);

        CREATE TABLE IF NOT EXISTS worker_attention_cycles (
            cycle_id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            category TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            commit_count INTEGER NOT NULL DEFAULT 0 CHECK (commit_count >= 0),
            code_change_count INTEGER NOT NULL DEFAULT 0 CHECK (code_change_count >= 0),
            milestone_move_count INTEGER NOT NULL DEFAULT 0 CHECK (milestone_move_count >= 0),
            blocker_removed_count INTEGER NOT NULL DEFAULT 0 CHECK (blocker_removed_count >= 0),
            deploy_outcome_count INTEGER NOT NULL DEFAULT 0 CHECK (deploy_outcome_count >= 0)
        );
        CREATE INDEX IF NOT EXISTS idx_worker_attention_occurred
        ON worker_attention_cycles(occurred_at);
        CREATE INDEX IF NOT EXISTS idx_worker_attention_worker_occurred
        ON worker_attention_cycles(worker_id, occurred_at);
        """
    )
    version = conn.execute(
        "SELECT schema_version FROM worker_attention_schema WHERE singleton=1"
    ).fetchone()
    if not version or int(version["schema_version"]) != SCHEMA_VERSION:
        conn.close()
        raise AttentionStoreError("schema_version_unsupported")
    return conn


def normalize_cycle(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != ROW_KEYS:
        raise AttentionStoreError("cycle_keys_invalid")
    category = payload["category"]
    if not isinstance(category, str) or category not in CATEGORIES:
        raise AttentionStoreError("category_invalid")

    normalized = {
        "cycle_id": _token(payload["cycle_id"], "cycle_id_invalid"),
        "worker_id": _token(payload["worker_id"], "worker_id_invalid"),
        "project_id": _token(payload["project_id"], "project_id_invalid"),
        "category": category,
        "occurred_at": _timestamp(payload["occurred_at"], "occurred_at_invalid"),
    }
    for field in MATERIAL_FIELDS:
        normalized[field] = _counter(payload[field], f"{field}_invalid")
    return normalized


def record_cycle(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    row = normalize_cycle(payload)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO worker_attention_cycles(
                    cycle_id, worker_id, project_id, category, occurred_at,
                    commit_count, code_change_count, milestone_move_count,
                    blocker_removed_count, deploy_outcome_count
                ) VALUES(
                    :cycle_id, :worker_id, :project_id, :category, :occurred_at,
                    :commit_count, :code_change_count, :milestone_move_count,
                    :blocker_removed_count, :deploy_outcome_count
                )
                """,
                row,
            )
    except sqlite3.IntegrityError as exc:
        raise AttentionStoreError("cycle_duplicate") from exc


def query_window(
    conn: sqlite3.Connection,
    *,
    start: str,
    end: str,
    worker_id: str | None = None,
) -> dict[str, Any]:
    start_utc = _timestamp(start, "window_start_invalid")
    end_utc = _timestamp(end, "window_end_invalid")
    if start_utc >= end_utc:
        raise AttentionStoreError("window_invalid")

    params: list[Any] = [start_utc, end_utc]
    where = "occurred_at >= ? AND occurred_at < ?"
    if worker_id is not None:
        where += " AND worker_id = ?"
        params.append(_token(worker_id, "worker_id_invalid"))

    rows = conn.execute(
        f"""
        SELECT worker_id, project_id, category,
               COUNT(*) AS cycles,
               SUM(commit_count) AS commit_count,
               SUM(code_change_count) AS code_change_count,
               SUM(milestone_move_count) AS milestone_move_count,
               SUM(blocker_removed_count) AS blocker_removed_count,
               SUM(deploy_outcome_count) AS deploy_outcome_count
        FROM worker_attention_cycles
        WHERE {where}
        GROUP BY worker_id, project_id, category
        ORDER BY worker_id, project_id, category
        """,
        params,
    ).fetchall()

    bounded_rows = []
    totals = {"cycles": 0, **{field: 0 for field in MATERIAL_FIELDS}}
    for row in rows:
        item = {
            "worker_id": str(row["worker_id"]),
            "project_id": str(row["project_id"]),
            "category": str(row["category"]),
            "cycles": int(row["cycles"]),
        }
        totals["cycles"] += item["cycles"]
        for field in MATERIAL_FIELDS:
            item[field] = int(row[field] or 0)
            totals[field] += item[field]
        bounded_rows.append(item)

    return {
        "ok": True,
        "policy": POLICY,
        "schema_version": SCHEMA_VERSION,
        "window": {"start": start_utc, "end": end_utc},
        "worker_id": worker_id,
        "rows": bounded_rows,
        "totals": totals,
        "free_text_stored": False,
    }


def _load_cycle(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise AttentionStoreError("cycle_file_symlink_rejected")
    if not path.is_file():
        raise AttentionStoreError("cycle_file_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AttentionStoreError("cycle_file_invalid") from exc
    if not isinstance(payload, dict):
        raise AttentionStoreError("cycle_file_invalid")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    record = sub.add_parser("record")
    record.add_argument("cycle_json", type=Path)

    query = sub.add_parser("query")
    query.add_argument("--start", required=True)
    query.add_argument("--end", required=True)
    query.add_argument("--worker-id")

    args = parser.parse_args(argv)
    try:
        conn = open_store(args.db)
        with conn:
            if args.command == "record":
                record_cycle(conn, _load_cycle(args.cycle_json))
                result = {"ok": True, "policy": POLICY, "recorded": True}
            elif args.command == "query":
                result = query_window(
                    conn,
                    start=args.start,
                    end=args.end,
                    worker_id=args.worker_id,
                )
            else:
                result = {
                    "ok": True,
                    "policy": POLICY,
                    "schema_version": SCHEMA_VERSION,
                    "initialized": True,
                    "free_text_stored": False,
                }
        conn.close()
    except (AttentionStoreError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, AttentionStoreError) else "sqlite_error"
        print(json.dumps({"ok": False, "policy": POLICY, "error": code}, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

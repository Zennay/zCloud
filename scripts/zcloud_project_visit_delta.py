#!/usr/bin/env python3
"""Read-only evidence-backed project delta since a user's last visit."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

_REQUIRED_COLUMNS = {
    "id",
    "project_id",
    "phase",
    "action",
    "commit_sha",
    "ci_status",
    "blocker",
    "next_gate",
    "source",
    "observed_at",
    "created_at",
}
_SEMANTIC_FIELDS = (
    "phase",
    "action",
    "commit_sha",
    "ci_status",
    "blocker",
    "next_gate",
    "source",
)
_MAX_TEXT = 1000


def _parse_timestamp(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("since timestamp is required")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("since must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("since timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _clean(value: object) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > _MAX_TEXT:
        return text[: _MAX_TEXT - 1] + "…"
    return text


def _row_payload(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "observed_at": _clean(row["observed_at"]),
        **{field: _clean(row[field]) for field in _SEMANTIC_FIELDS},
    }


def _semantic(payload: dict | None) -> tuple:
    if not payload:
        return tuple("" for _ in _SEMANTIC_FIELDS)
    return tuple(payload.get(field, "") for field in _SEMANTIC_FIELDS)


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    if db_path.is_symlink():
        raise ValueError("refusing symlink database path")
    if not db_path.is_file():
        raise ValueError("database path must be an existing regular file")
    uri = "file:" + quote(str(db_path.resolve()), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_state_receipts'"
    ).fetchone()
    if not exists:
        raise ValueError("project_state_receipts table missing")
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(project_state_receipts)")
    }
    missing = sorted(_REQUIRED_COLUMNS - columns)
    if missing:
        raise ValueError("project_state_receipts missing columns: " + ",".join(missing))


def build_delta(
    db_path: Path,
    *,
    project_id: str,
    since: str,
    limit: int = 50,
) -> dict:
    project = str(project_id or "").strip().lower()
    if not project or len(project) > 80:
        raise ValueError("project must be 1..80 characters")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    since_dt = _parse_timestamp(since)
    since_iso = since_dt.isoformat()

    connection = _open_read_only(Path(db_path))
    try:
        _validate_schema(connection)
        invalid_time = connection.execute(
            """
            SELECT id FROM project_state_receipts
            WHERE project_id=? AND julianday(observed_at) IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (project,),
        ).fetchone()
        if invalid_time is not None:
            raise ValueError(
                "project_state_receipts contains invalid observed_at for project "
                + project
            )
        before_row = connection.execute(
            """
            SELECT id,project_id,phase,action,commit_sha,ci_status,blocker,next_gate,
                   source,observed_at,created_at
            FROM project_state_receipts
            WHERE project_id=? AND julianday(observed_at)<=julianday(?)
            ORDER BY julianday(observed_at) DESC,id DESC
            LIMIT 1
            """,
            (project, since_iso),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT id,project_id,phase,action,commit_sha,ci_status,blocker,next_gate,
                   source,observed_at,created_at
            FROM project_state_receipts
            WHERE project_id=? AND julianday(observed_at)>julianday(?)
            ORDER BY julianday(observed_at) ASC,id ASC
            LIMIT ?
            """,
            (project, since_iso, limit + 1),
        ).fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
        total_changes = int(connection.total_changes)
    finally:
        connection.close()

    if total_changes != 0:
        raise RuntimeError("read-only delta unexpectedly mutated SQLite state")

    baseline = _row_payload(before_row)
    previous = baseline
    changes = []
    for row in rows:
        current = _row_payload(row)
        if _semantic(current) == _semantic(previous):
            previous = current
            continue
        changed_fields = []
        for field in _SEMANTIC_FIELDS:
            if (previous or {}).get(field, "") != current.get(field, ""):
                changed_fields.append(field)
        changes.append(
            {
                "id": current["id"],
                "observed_at": current["observed_at"],
                "changed_fields": changed_fields,
                **{field: current[field] for field in _SEMANTIC_FIELDS},
            }
        )
        previous = current

    current = previous or baseline
    return {
        "schema_version": 1,
        "project_id": project,
        "since": since_iso,
        "change_count": len(changes),
        "has_changes": bool(changes),
        "cursor_receipt_id": current.get("id") if current else None,
        "truncated": truncated,
        "baseline": baseline,
        "current": current,
        "changes": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize evidence-backed project state changes since a visit timestamp."
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    try:
        payload = build_delta(
            args.db,
            project_id=args.project,
            since=args.since,
            limit=args.limit,
        )
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps({"ok": True, **payload}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

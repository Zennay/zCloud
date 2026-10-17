#!/usr/bin/env python3
"""Bounded read-only projection of existing zCloud event evidence."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "zcloud-central-eventstream-v1"
MAX_LIMIT = 500
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
SOURCE_ORDER = {
    "runner_events": 0,
    "project_state_receipts": 1,
    "config_audit": 2,
    "events": 3,
}


class EventStreamError(RuntimeError):
    pass


def _safe_token(value: object, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if TOKEN_RE.fullmatch(text) else fallback


def _timestamp(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _require_columns(conn: sqlite3.Connection, table: str, required: set[str]) -> bool:
    cols = _table_columns(conn, table)
    if not cols:
        return False
    missing = required - cols
    if missing:
        raise EventStreamError(f"{table} missing required columns: {','.join(sorted(missing))}")
    return True


def _append_event(
    out: list[dict],
    malformed: dict[str, int],
    *,
    source: str,
    source_id: object,
    observed_at: object,
    project_id: object,
    kind: object,
    extra: dict | None = None,
) -> None:
    ts = _timestamp(observed_at)
    if ts is None:
        malformed[source] += 1
        return
    project = str(project_id or "").strip()
    if project and not PROJECT_RE.fullmatch(project):
        malformed[source] += 1
        return
    item = {
        "observed_at": ts,
        "project_id": project or None,
        "source": source,
        "kind": _safe_token(kind),
        "_source_id": str(source_id),
    }
    if extra:
        item.update(extra)
    out.append(item)


def build_snapshot(db_path: Path, *, limit: int = 100, project: str | None = None) -> dict:
    limit = int(limit)
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if project is not None and not PROJECT_RE.fullmatch(project):
        raise ValueError("project must be a lowercase zCloud project identifier")
    path = Path(db_path)
    if path.is_symlink():
        raise EventStreamError("database symlink refused")
    if not path.is_file():
        raise EventStreamError("database file missing")

    uri = f"file:{path.resolve()}?mode=ro"
    events: list[dict] = []
    malformed = {name: 0 for name in SOURCE_ORDER}
    missing_sources: list[str] = []
    fetch_limit = min(MAX_LIMIT, max(limit * 2, limit))

    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row

        if _require_columns(conn, "runner_events", {"id", "ts", "event", "project_id", "worker_slot"}):
            where = " WHERE project_id=?" if project else ""
            args = (project, fetch_limit) if project else (fetch_limit,)
            rows = conn.execute(
                "SELECT id,ts,event,project_id,worker_slot FROM runner_events"
                + where
                + " ORDER BY id DESC LIMIT ?",
                args,
            )
            for row in rows:
                slot = row["worker_slot"]
                _append_event(
                    events,
                    malformed,
                    source="runner_events",
                    source_id=row["id"],
                    observed_at=row["ts"],
                    project_id=row["project_id"],
                    kind=row["event"],
                    extra={"worker_slot": int(slot) if slot is not None else None},
                )
        else:
            missing_sources.append("runner_events")

        if _require_columns(
            conn,
            "project_state_receipts",
            {"id", "project_id", "phase", "ci_status", "blocker", "observed_at"},
        ):
            where = " WHERE project_id=?" if project else ""
            args = (project, fetch_limit) if project else (fetch_limit,)
            rows = conn.execute(
                "SELECT id,project_id,phase,ci_status,blocker,observed_at "
                "FROM project_state_receipts"
                + where
                + " ORDER BY id DESC LIMIT ?",
                args,
            )
            for row in rows:
                _append_event(
                    events,
                    malformed,
                    source="project_state_receipts",
                    source_id=row["id"],
                    observed_at=row["observed_at"],
                    project_id=row["project_id"],
                    kind="state_receipt",
                    extra={
                        "phase": _safe_token(row["phase"]),
                        "ci_status": _safe_token(row["ci_status"]),
                        "blocker_present": bool(str(row["blocker"] or "").strip()),
                    },
                )
        else:
            missing_sources.append("project_state_receipts")

        if _require_columns(
            conn,
            "config_audit",
            {"id", "ts", "config_key", "target", "result"},
        ):
            where = " WHERE target=?" if project else ""
            args = (project, fetch_limit) if project else (fetch_limit,)
            rows = conn.execute(
                "SELECT id,ts,config_key,target,result FROM config_audit"
                + where
                + " ORDER BY id DESC LIMIT ?",
                args,
            )
            for row in rows:
                target = str(row["target"] or "").strip()
                target_project = target if PROJECT_RE.fullmatch(target) else None
                _append_event(
                    events,
                    malformed,
                    source="config_audit",
                    source_id=row["id"],
                    observed_at=row["ts"],
                    project_id=target_project,
                    kind="config_change",
                    extra={
                        "config_key": _safe_token(row["config_key"]),
                        "result": _safe_token(row["result"]),
                    },
                )
        else:
            missing_sources.append("config_audit")

        if _require_columns(conn, "events", {"id", "ts", "project", "kind"}):
            where = " WHERE project=?" if project else ""
            args = (project, fetch_limit) if project else (fetch_limit,)
            rows = conn.execute(
                "SELECT id,ts,project,kind FROM events"
                + where
                + " ORDER BY ts DESC LIMIT ?",
                args,
            )
            for row in rows:
                _append_event(
                    events,
                    malformed,
                    source="events",
                    source_id=row["id"],
                    observed_at=row["ts"],
                    project_id=row["project"],
                    kind=row["kind"],
                )
        else:
            missing_sources.append("events")

    events.sort(
        key=lambda item: (
            item["observed_at"],
            -SOURCE_ORDER[item["source"]],
            item["_source_id"],
        ),
        reverse=True,
    )
    events = events[:limit]
    for item in events:
        item.pop("_source_id", None)

    counts = {name: 0 for name in SOURCE_ORDER}
    for item in events:
        counts[item["source"]] += 1
    malformed_total = sum(malformed.values())

    return {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "limit": limit,
        "coverage_complete": not missing_sources and malformed_total == 0,
        "missing_sources": missing_sources,
        "malformed_rows": malformed,
        "source_counts": counts,
        "events": events,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--project")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(args.db, limit=args.limit, project=args.project)
    except (EventStreamError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0 if snapshot["coverage_complete"] or not args.require_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())

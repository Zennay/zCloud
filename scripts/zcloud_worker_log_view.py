#!/usr/bin/env python3
"""Bounded read-only per-worker log view for zCloud control-plane telemetry.

The view intentionally exposes lifecycle/control metadata only. Raw target,
title, reason, error, command result and conversation payloads are never read.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MIN_HOURS = 0.25
MAX_HOURS = 168.0
MAX_LIMIT = 200


def _token(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not TOKEN_RE.fullmatch(text):
        raise ValueError(f"invalid_{name}")
    return text


def _output_token(value: object) -> str:
    text = str(value or "").strip()
    return text if TOKEN_RE.fullmatch(text) else "unclassified"


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive_timestamp")
    return parsed.astimezone(timezone.utc)


def _open_ro(db: Path) -> sqlite3.Connection:
    if db.is_symlink():
        raise ValueError("database_symlink_not_allowed")
    if not db.exists() or not db.is_file():
        raise ValueError("database_missing")
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> None:
    if not _table_exists(connection, table):
        raise ValueError(f"missing_table:{table}")
    missing = sorted(required - _columns(connection, table))
    if missing:
        raise ValueError(f"missing_columns:{table}:{','.join(missing)}")


def report(
    db: Path,
    *,
    project: str,
    worker_slot: int,
    hours: float = 12.0,
    limit: int = 50,
    kind: str = "all",
    event: str | None = None,
    action: str | None = None,
    status: str | None = None,
    now: datetime | None = None,
) -> dict:
    project_id = _token("project", project)
    event_filter = _token("event", event)
    action_filter = _token("action", action)
    status_filter = _token("status", status)
    if project_id is None:
        raise ValueError("project_required")
    if not isinstance(worker_slot, int) or worker_slot < 1 or worker_slot > 64:
        raise ValueError("invalid_worker_slot")
    if kind not in {"all", "event", "control"}:
        raise ValueError("invalid_kind")
    hours = float(hours)
    if hours < MIN_HOURS or hours > MAX_HOURS:
        raise ValueError("invalid_hours")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT:
        raise ValueError("invalid_limit")
    if kind == "event" and (action_filter or status_filter):
        raise ValueError("control_filter_with_event_kind")
    if kind == "control" and event_filter:
        raise ValueError("event_filter_with_control_kind")

    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = generated_at - timedelta(hours=hours)
    cutoff_text = cutoff.isoformat()
    entries: list[dict] = []

    with closing(_open_ro(Path(db))) as connection:
        _require_columns(
            connection,
            "runner_events",
            {"id", "ts", "event", "generating", "sending", "project_id", "worker_slot"},
        )

        if kind in {"all", "event"}:
            where = ["project_id=?", "worker_slot=?", "ts>=?"]
            params: list[object] = [project_id, worker_slot, cutoff_text]
            if event_filter:
                where.append("event=?")
                params.append(event_filter)
            params.append(limit + 1)
            rows = connection.execute(
                "SELECT id,ts,event,generating,sending "
                "FROM runner_events WHERE "
                + " AND ".join(where)
                + " ORDER BY ts DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
            for row in rows:
                observed_at = str(row["ts"])
                _timestamp(observed_at)
                entries.append(
                    {
                        "kind": "event",
                        "id": int(row["id"]),
                        "observed_at": observed_at,
                        "event": _output_token(row["event"]),
                        "generating": bool(row["generating"]),
                        "sending": bool(row["sending"]),
                    }
                )

        if kind in {"all", "control"} and _table_exists(connection, "runner_commands"):
            _require_columns(
                connection,
                "runner_commands",
                {"id", "project_id", "action", "status", "created_at", "updated_at"},
            )
            worker_key = f"{project_id}::w{worker_slot}"
            where = ["project_id=?", "updated_at>=?"]
            params = [worker_key, cutoff_text]
            if action_filter:
                where.append("action=?")
                params.append(action_filter)
            if status_filter:
                where.append("status=?")
                params.append(status_filter)
            params.append(limit + 1)
            rows = connection.execute(
                "SELECT id,updated_at,action,status "
                "FROM runner_commands WHERE "
                + " AND ".join(where)
                + " ORDER BY updated_at DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
            for row in rows:
                observed_at = str(row["updated_at"])
                _timestamp(observed_at)
                entries.append(
                    {
                        "kind": "control",
                        "id": int(row["id"]),
                        "observed_at": observed_at,
                        "action": _output_token(row["action"]),
                        "status": _output_token(row["status"]),
                    }
                )

        if connection.total_changes != 0:
            raise RuntimeError("read_only_contract_violated")

    entries.sort(
        key=lambda item: (_timestamp(item["observed_at"]), int(item["id"]), item["kind"]),
        reverse=True,
    )
    truncated = len(entries) > limit
    entries = entries[:limit]
    counts = {
        "event": sum(1 for item in entries if item["kind"] == "event"),
        "control": sum(1 for item in entries if item["kind"] == "control"),
    }
    return {
        "generated_at": generated_at.isoformat(),
        "cutoff": cutoff_text,
        "window_hours": hours,
        "project_id": project_id,
        "worker_slot": worker_slot,
        "worker_key": f"{project_id}::w{worker_slot}",
        "filters": {
            "kind": kind,
            "event": event_filter,
            "action": action_filter,
            "status": status_filter,
        },
        "limit": limit,
        "truncated": truncated,
        "returned": len(entries),
        "counts": counts,
        "privacy": "sanitized lifecycle/control metadata only; raw payload fields are not read",
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read a bounded sanitized per-worker zCloud log")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "history.db")
    parser.add_argument("--project", required=True)
    parser.add_argument("--worker-slot", type=int, required=True)
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--kind", choices=("all", "event", "control"), default="all")
    parser.add_argument("--event")
    parser.add_argument("--action")
    parser.add_argument("--status")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    if args.kind == "event" and (args.action or args.status):
        parser.error("--action/--status require --kind all|control")
    if args.kind == "control" and args.event:
        parser.error("--event requires --kind all|event")

    payload = report(
        args.db,
        project=args.project,
        worker_slot=args.worker_slot,
        hours=args.hours,
        limit=args.limit,
        kind=args.kind,
        event=args.event,
        action=args.action,
        status=args.status,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

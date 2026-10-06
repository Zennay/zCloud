#!/usr/bin/env python3
"""Read-only project activity timeline from zCloud control-plane telemetry.

The report intentionally emits a small, curated set of meaningful lifecycle
events instead of exposing raw runner logs or command result payloads.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_WINDOW_HOURS = 24 * 30
MAX_LIMIT = 200

RUNNER_EVENT_LABELS = {
    "prompt-sent": ("work", "Prompt verzonden"),
    "generation-started": ("work", "Generatie gestart"),
    "generation-finished": ("work", "Generatie afgerond"),
    "conversation-adopted": ("recovery", "Conversation gekoppeld"),
    "conversation-reset-requested": ("recovery", "Nieuwe chat aangevraagd"),
    "runner-drained": ("control", "Worker gedraineerd"),
    "startup-blocked": ("problem", "Workerstart geblokkeerd"),
    "send-blocked": ("problem", "Prompt geblokkeerd"),
    "stall-detected": ("problem", "Stilstand gedetecteerd"),
    "composer-stalled": ("problem", "Composer vastgelopen"),
    "auto-continue-blocked": ("problem", "Automatisch doorgaan geblokkeerd"),
    "push-skipped": ("problem", "Push overgeslagen"),
}

ACTION_LABELS = {
    "start": "Start",
    "push": "Push nu",
    "pause": "Pauzeer",
    "drain": "Drain",
    "new-chat": "Nieuwe chat",
    "new_chat": "Nieuwe chat",
    "restart": "Herstart",
}

SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


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


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> set[str]:
    cols = _columns(connection, table)
    missing = sorted(required - cols)
    if missing:
        raise RuntimeError(f"{table} missing required columns: {', '.join(missing)}")
    return cols


def _safe_reason(value: object) -> str | None:
    text = str(value or "")
    return text if SAFE_REASON_RE.fullmatch(text) else None


def _event_entry(row: sqlite3.Row) -> dict:
    category, summary = RUNNER_EVENT_LABELS[str(row["event"])]
    entry = {
        "ts": str(row["ts"]),
        "project_id": str(row["project_id"]),
        "source": "runner_event",
        "category": category,
        "code": str(row["event"]),
        "summary": summary,
    }
    if "worker_slot" in row.keys() and row["worker_slot"] is not None:
        entry["worker_slot"] = int(row["worker_slot"])
    if "reason" in row.keys():
        reason = _safe_reason(row["reason"])
        if reason:
            entry["reason_code"] = reason
    return entry


def _command_entry(row: sqlite3.Row) -> dict:
    action = str(row["action"] or "")
    status = str(row["status"] or "unknown").lower()
    label = ACTION_LABELS.get(action, action.replace("-", " ").replace("_", " ").strip().title() or "Controlactie")
    if status == "pending":
        summary = f"{label} aangevraagd"
        category = "control"
        ts = str(row["created_at"])
    elif status in {"completed", "success", "succeeded", "done"}:
        summary = f"{label} afgerond"
        category = "control"
        ts = str(row["updated_at"] or row["created_at"])
    elif status in {"failed", "error", "cancelled", "canceled"}:
        summary = f"{label} mislukt"
        category = "problem"
        ts = str(row["updated_at"] or row["created_at"])
    else:
        summary = f"{label}: {status}"
        category = "control"
        ts = str(row["updated_at"] or row["created_at"])
    return {
        "ts": ts,
        "project_id": str(row["project_id"]),
        "source": "runner_command",
        "category": category,
        "code": f"control:{action}:{status}",
        "summary": summary,
        "state": status,
    }


def _generic_event_entry(row: sqlite3.Row) -> dict:
    kind = _safe_reason(row["kind"])
    code = f"project:{kind}" if kind else "project:event"
    return {
        "ts": str(row["ts"]),
        "project_id": str(row["project"]),
        "source": "project_event",
        "category": "project",
        "code": code,
        "summary": f"Projectevent: {kind}" if kind else "Projectevent",
    }


def report(
    db: Path,
    hours: float = 24.0,
    project: str | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> dict:
    hours = float(hours)
    limit = int(limit)
    if not (0.25 <= hours <= MAX_WINDOW_HOURS):
        raise ValueError(f"hours must be between 0.25 and {MAX_WINDOW_HOURS}")
    if not (1 <= limit <= MAX_LIMIT):
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    timeline: list[dict] = []

    with closing(_open_ro(Path(db))) as connection:
        runner_cols = _require_columns(
            connection,
            "runner_events",
            {"ts", "event", "project_id"},
        )
        worker_expr = "worker_slot" if "worker_slot" in runner_cols else "NULL AS worker_slot"
        reason_expr = "reason" if "reason" in runner_cols else "NULL AS reason"
        event_names = sorted(RUNNER_EVENT_LABELS)
        placeholders = ",".join("?" for _ in event_names)
        params: list[object] = [cutoff.isoformat(), *event_names]
        where = f"ts>=? AND project_id IS NOT NULL AND project_id<>'' AND event IN ({placeholders})"
        if project:
            where += " AND project_id=?"
            params.append(project)
        rows = connection.execute(
            f"SELECT ts,event,project_id,{worker_expr},{reason_expr} "
            f"FROM runner_events WHERE {where} ORDER BY id DESC LIMIT ?",
            [*params, max(limit * 8, 200)],
        ).fetchall()
        timeline.extend(_event_entry(row) for row in rows)

        generic_cols = _columns(connection, "events")
        required_events = {"ts", "project", "kind"}
        if required_events.issubset(generic_cols):
            params = [cutoff.isoformat()]
            where = "ts>=? AND project IS NOT NULL AND project<>''"
            if project:
                where += " AND project=?"
                params.append(project)
            generic_rows = connection.execute(
                "SELECT ts,project,kind FROM events "
                f"WHERE {where} ORDER BY ts DESC LIMIT ?",
                [*params, max(limit * 4, 100)],
            ).fetchall()
            timeline.extend(_generic_event_entry(row) for row in generic_rows)

        command_cols = _columns(connection, "runner_commands")
        required_commands = {"project_id", "action", "status", "created_at", "updated_at"}
        if required_commands.issubset(command_cols):
            params = [cutoff.isoformat()]
            where = "COALESCE(updated_at,created_at)>=? AND project_id IS NOT NULL AND project_id<>''"
            if project:
                where += " AND project_id=?"
                params.append(project)
            commands = connection.execute(
                "SELECT project_id,action,status,created_at,updated_at "
                f"FROM runner_commands WHERE {where} ORDER BY id DESC LIMIT ?",
                [*params, max(limit * 4, 100)],
            ).fetchall()
            timeline.extend(_command_entry(row) for row in commands)

    timeline.sort(key=lambda item: (_dt(item["ts"]), item["source"], item["code"]), reverse=True)
    timeline = timeline[:limit]
    counts = Counter(item["category"] for item in timeline)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "cutoff": cutoff.isoformat(),
        "project_id": project,
        "limit": limit,
        "method": "curated read-only runner lifecycle + project events + control-action outcomes; raw payloads excluded",
        "counts": dict(sorted(counts.items())),
        "timeline": timeline,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a meaningful zCloud project activity timeline")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "history.db")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--project")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = report(args.db, args.hours, args.project, args.limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

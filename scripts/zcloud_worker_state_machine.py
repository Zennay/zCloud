#!/usr/bin/env python3
"""Evidence-backed read-only worker state machine for zCloud.

The classifier exposes only six durable UI states:
waiting, running, blocked, failed, paused, draining.

It intentionally reads only bounded lifecycle/control metadata and never reads
raw event payloads, command results, conversation ids or task contents.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VALID_STATES = ("waiting", "running", "blocked", "failed", "paused", "draining")
VALID_DESIRED_STATES = {"running", "paused", "draining"}
BLOCK_EVENTS = {
    "startup-blocked",
    "send-blocked",
    "stall-detected",
    "composer-stalled",
    "auto-continue-blocked",
    "push-skipped",
}
UNBLOCK_EVENTS = {
    "prompt-sent",
    "generation-started",
    "generation-finished",
    "injection-success",
    "conversation-adopted",
}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
DEFAULT_HEARTBEAT_STALE_SECONDS = 180
MAX_HEARTBEAT_STALE_SECONDS = 3600


def _token(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not TOKEN_RE.fullmatch(text):
        raise ValueError(f"invalid_{name}")
    return text


def _dt(value: str) -> datetime:
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


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> None:
    if not _table_exists(connection, table):
        raise ValueError(f"missing_table:{table}")
    missing = sorted(required - _columns(connection, table))
    if missing:
        raise ValueError(f"missing_columns:{table}:{','.join(missing)}")


def _latest_event(
    connection: sqlite3.Connection,
    project_id: str,
    worker_slot: int,
    *,
    event: str | None = None,
    events: set[str] | None = None,
) -> sqlite3.Row | None:
    where = ["project_id=?", "worker_slot=?"]
    params: list[object] = [project_id, worker_slot]
    if event is not None:
        where.append("event=?")
        params.append(event)
    elif events:
        placeholders = ",".join("?" for _ in events)
        where.append(f"event IN ({placeholders})")
        params.extend(sorted(events))
    return connection.execute(
        "SELECT id,ts,event,generating,sending FROM runner_events WHERE "
        + " AND ".join(where)
        + " ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()


def _latest_worker_command(
    connection: sqlite3.Connection,
    project_id: str,
    worker_slot: int,
) -> sqlite3.Row | None:
    if not _table_exists(connection, "runner_commands"):
        return None
    return connection.execute(
        "SELECT id,action,status,updated_at FROM runner_commands "
        "WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (f"{project_id}::w{worker_slot}",),
    ).fetchone()


def _newer(left: sqlite3.Row | None, left_key: str, right: sqlite3.Row | None, right_key: str) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    return _dt(str(left[left_key])) > _dt(str(right[right_key]))


def classify_worker(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    worker_slot: int,
    desired_state: str,
    now: datetime,
    heartbeat_stale_seconds: int,
) -> dict:
    desired = str(desired_state or "").strip().lower()
    if desired not in VALID_DESIRED_STATES:
        raise ValueError(f"invalid_desired_state:{project_id}::w{worker_slot}:{desired}")

    heartbeat = _latest_event(connection, project_id, worker_slot, event="heartbeat")
    semantic = _latest_event(
        connection,
        project_id,
        worker_slot,
        events=BLOCK_EVENTS | UNBLOCK_EVENTS,
    )
    command = _latest_worker_command(connection, project_id, worker_slot)

    heartbeat_age = None
    heartbeat_freshness = "missing"
    if heartbeat is not None:
        heartbeat_at = _dt(str(heartbeat["ts"]))
        heartbeat_age = max(0.0, (now - heartbeat_at).total_seconds())
        heartbeat_freshness = (
            "fresh" if heartbeat_age <= heartbeat_stale_seconds else "stale"
        )

    active_heartbeat = (
        heartbeat is not None
        and heartbeat_freshness == "fresh"
        and (bool(heartbeat["generating"]) or bool(heartbeat["sending"]))
    )
    newest_runtime = heartbeat
    newest_runtime_key = "ts"
    if semantic is not None and (
        newest_runtime is None or _dt(str(semantic["ts"])) > _dt(str(newest_runtime["ts"]))
    ):
        newest_runtime = semantic

    if desired in {"paused", "draining"}:
        state = desired
        reason = f"desired_state_{desired}"
    elif command is not None and str(command["status"] or "") == "failed" and _newer(
        command, "updated_at", newest_runtime, newest_runtime_key
    ):
        _dt(str(command["updated_at"]))
        state = "failed"
        reason = "latest_worker_control_failed"
    elif (
        semantic is not None
        and str(semantic["event"] or "") in BLOCK_EVENTS
        and not (
            active_heartbeat
            and _dt(str(heartbeat["ts"])) > _dt(str(semantic["ts"]))
        )
    ):
        _dt(str(semantic["ts"]))
        state = "blocked"
        reason = "latest_semantic_event_blocked"
    elif active_heartbeat:
        state = "running"
        reason = "fresh_heartbeat_active"
    else:
        state = "waiting"
        if heartbeat is None:
            reason = "no_heartbeat_evidence"
        elif heartbeat_freshness == "stale":
            reason = "heartbeat_stale"
        else:
            reason = "fresh_heartbeat_idle"

    latest_event = None
    if heartbeat is not None or semantic is not None:
        candidates = [row for row in (heartbeat, semantic) if row is not None]
        latest_event = max(candidates, key=lambda row: int(row["id"]))

    return {
        "project_id": project_id,
        "worker_slot": worker_slot,
        "worker_key": f"{project_id}::w{worker_slot}",
        "state": state,
        "reason_code": reason,
        "desired_state": desired,
        "heartbeat_freshness": heartbeat_freshness,
        "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
        "active_flags": {
            "generating": bool(heartbeat["generating"]) if heartbeat is not None else False,
            "sending": bool(heartbeat["sending"]) if heartbeat is not None else False,
        },
        "latest_event": (
            {
                "event": str(latest_event["event"] or ""),
                "observed_at": str(latest_event["ts"]),
            }
            if latest_event is not None
            else None
        ),
        "latest_control": (
            {
                "action": str(command["action"] or ""),
                "status": str(command["status"] or ""),
                "observed_at": str(command["updated_at"]),
            }
            if command is not None
            else None
        ),
    }


def report(
    db: Path,
    *,
    project: str | None = None,
    worker_slot: int | None = None,
    heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
    now: datetime | None = None,
) -> dict:
    project_id = _token("project", project)
    if worker_slot is not None and (not isinstance(worker_slot, int) or worker_slot < 1 or worker_slot > 64):
        raise ValueError("invalid_worker_slot")
    if worker_slot is not None and project_id is None:
        raise ValueError("worker_slot_requires_project")
    if (
        not isinstance(heartbeat_stale_seconds, int)
        or heartbeat_stale_seconds < 30
        or heartbeat_stale_seconds > MAX_HEARTBEAT_STALE_SECONDS
    ):
        raise ValueError("invalid_heartbeat_stale_seconds")

    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with closing(_open_ro(Path(db))) as connection:
        _require_columns(
            connection,
            "runner_workers",
            {"project_id", "worker_slot", "desired_state"},
        )
        _require_columns(
            connection,
            "runner_events",
            {"id", "ts", "event", "generating", "sending", "project_id", "worker_slot"},
        )
        if _table_exists(connection, "runner_commands"):
            _require_columns(
                connection,
                "runner_commands",
                {"id", "project_id", "action", "status", "updated_at"},
            )

        where = []
        params: list[object] = []
        if project_id is not None:
            where.append("project_id=?")
            params.append(project_id)
        if worker_slot is not None:
            where.append("worker_slot=?")
            params.append(worker_slot)
        sql = "SELECT project_id,worker_slot,desired_state FROM runner_workers"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY project_id,worker_slot"
        workers = connection.execute(sql, params).fetchall()

        states = [
            classify_worker(
                connection,
                project_id=str(row["project_id"]),
                worker_slot=int(row["worker_slot"]),
                desired_state=str(row["desired_state"]),
                now=generated_at,
                heartbeat_stale_seconds=heartbeat_stale_seconds,
            )
            for row in workers
        ]
        if connection.total_changes != 0:
            raise RuntimeError("read_only_contract_violated")

    counts = {state: 0 for state in VALID_STATES}
    for item in states:
        counts[item["state"]] += 1
    return {
        "generated_at": generated_at.isoformat(),
        "state_contract": list(VALID_STATES),
        "heartbeat_stale_seconds": heartbeat_stale_seconds,
        "filters": {"project": project_id, "worker_slot": worker_slot},
        "worker_count": len(states),
        "counts": counts,
        "privacy": "bounded worker lifecycle/control metadata only; no raw payloads are read",
        "workers": states,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive evidence-backed zCloud worker states")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "history.db")
    parser.add_argument("--project")
    parser.add_argument("--worker-slot", type=int)
    parser.add_argument(
        "--heartbeat-stale-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_STALE_SECONDS,
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    payload = report(
        args.db,
        project=args.project,
        worker_slot=args.worker_slot,
        heartbeat_stale_seconds=args.heartbeat_stale_seconds,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

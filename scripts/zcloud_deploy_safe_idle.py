#!/usr/bin/env python3
"""Hold zCloud browser workers in a reversible safe-idle window for production deploys."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("ZCLOUD_DB", "/home/ubuntu/zennay-cloud/history.db"))
DRAIN_ACK_EVENTS = frozenset({"runner-config-updated", "runner-draining", "injection-success"})
DRAIN_TERMINAL_EVENTS = frozenset({"runner-drained", "runner-stopped"})


class SafeIdleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def worker_key(project_id: str, worker_slot: int) -> str:
    return f"{project_id}::w{worker_slot}"


def worker_observation(
    conn: sqlite3.Connection,
    project_id: str,
    worker_slot: int,
    *,
    now: datetime | None = None,
) -> dict:
    """Return secret-safe liveness evidence for one browser worker."""
    current = now or datetime.now(timezone.utc)
    try:
        row = conn.execute(
            "SELECT ts,event,generating,sending FROM runner_events "
            "WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 1",
            (project_id, worker_slot),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        return {
            "available": False,
            "event": None,
            "age_seconds": None,
            "generating": None,
            "sending": None,
        }
    try:
        observed = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (current - observed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        age_seconds = None
    return {
        "available": True,
        "event": str(row["event"] or ""),
        "observed_at": str(row["ts"] or ""),
        "age_seconds": age_seconds,
        "generating": bool(row["generating"]),
        "sending": bool(row["sending"]),
    }


def drain_command_state(conn: sqlite3.Connection, worker_id: str) -> dict:
    row = conn.execute(
        "SELECT id,status,action,created_at FROM runner_commands "
        "WHERE project_id=? AND action='drain' ORDER BY id DESC LIMIT 1",
        (worker_id,),
    ).fetchone()
    if row is None:
        return {"id": None, "status": None, "action": None, "created_at": None}
    return {
        "id": int(row["id"]),
        "status": str(row["status"] or ""),
        "action": str(row["action"] or ""),
        "created_at": str(row["created_at"] or ""),
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def drain_acknowledged_idle(observation: dict, command: dict) -> bool:
    """Prove an idle worker has observed the deploy drain before auto-pausing it."""
    if (
        observation.get("available") is not True
        or observation.get("generating") is not False
        or observation.get("sending") is not False
    ):
        return False
    if str(command.get("status") or "") == "completed":
        return True
    if str(command.get("status") or "") != "pending":
        return False
    if str(observation.get("event") or "") not in DRAIN_ACK_EVENTS:
        return False
    observed_at = _parse_timestamp(observation.get("observed_at"))
    created_at = _parse_timestamp(command.get("created_at"))
    return bool(observed_at and created_at and observed_at >= created_at)


def drain_terminal_idle(observation: dict, command: dict) -> bool:
    """Accept terminal post-drain stop evidence even if command bookkeeping failed."""
    if (
        observation.get("available") is not True
        or observation.get("generating") is not False
        or observation.get("sending") is not False
        or str(observation.get("event") or "") not in DRAIN_TERMINAL_EVENTS
    ):
        return False
    observed_at = _parse_timestamp(observation.get("observed_at"))
    created_at = _parse_timestamp(command.get("created_at"))
    return bool(observed_at and created_at and observed_at >= created_at)


def blocking_worker_diagnostics(
    conn: sqlite3.Connection,
    workers: dict[str, dict],
) -> list[dict]:
    diagnostics: list[dict] = []
    for key, record in sorted(workers.items()):
        row = conn.execute(
            "SELECT desired_state FROM runner_workers "
            "WHERE project_id=? AND worker_slot=?",
            (record["project_id"], record["worker_slot"]),
        ).fetchone()
        desired = str(row["desired_state"] or "running") if row else "missing"
        if desired == "paused":
            continue
        observation = worker_observation(
            conn, record["project_id"], record["worker_slot"]
        )
        command = drain_command_state(conn, key)
        diagnostics.append({
            "worker_id": key,
            "desired_state": desired,
            "last_event": observation["event"],
            "last_event_age_seconds": (
                round(float(observation["age_seconds"]), 1)
                if observation["age_seconds"] is not None else None
            ),
            "generating": observation["generating"],
            "sending": observation["sending"],
            "drain_command_id": command["id"],
            "drain_command_status": command["status"],
        })
    return diagnostics


def allocated_workers(conn: sqlite3.Connection) -> list[dict]:
    slots = conn.execute(
        "SELECT slot,project_id,worker_slot FROM ai_global_slots ORDER BY slot"
    ).fetchall()
    workers: list[dict] = []
    for slot in slots:
        project_id = str(slot["project_id"])
        worker_slot = int(slot["worker_slot"] or 1)
        row = conn.execute(
            "SELECT desired_state FROM runner_workers WHERE project_id=? AND worker_slot=?",
            (project_id, worker_slot),
        ).fetchone()
        if row is None:
            raise SafeIdleError(
                f"allocated worker is missing durable runner state: "
                f"{worker_key(project_id, worker_slot)}"
            )
        desired_state = str(row["desired_state"] or "running")
        workers.append(
            {
                "global_slot": int(slot["slot"]),
                "project_id": project_id,
                "worker_slot": worker_slot,
                "worker_id": worker_key(project_id, worker_slot),
                "desired_state": desired_state,
            }
        )
    return workers


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def restore_snapshot(db: Path, payload: dict) -> dict:
    restored: list[dict] = []
    cancelled_commands: list[int] = []
    with connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in payload.get("workers") or []:
            project_id = str(item["project_id"])
            worker_slot = int(item["worker_slot"])
            previous_state = str(item.get("previous_state") or "running")
            conn.execute(
                "UPDATE runner_workers SET desired_state=? WHERE project_id=? AND worker_slot=?",
                (previous_state, project_id, worker_slot),
            )
            restored.append(
                {
                    "worker_id": worker_key(project_id, worker_slot),
                    "desired_state": previous_state,
                }
            )
        for command_id in payload.get("drain_command_ids") or []:
            cursor = conn.execute(
                "UPDATE runner_commands SET status='failed',updated_at=?,result=? "
                "WHERE id=? AND status='pending'",
                (
                    utc_now(),
                    "deploy safe-idle ended before pending drain command was consumed",
                    int(command_id),
                ),
            )
            if cursor.rowcount == 1:
                cancelled_commands.append(int(command_id))
        conn.commit()
    return {"restored": restored, "cancelled_commands": cancelled_commands}


def enter_safe_idle(
    db: Path,
    state_path: Path,
    *,
    timeout_seconds: float = 240.0,
    poll_seconds: float = 0.5,
    stable_seconds: float = 2.0,
    offline_after_seconds: float = 300.0,
) -> dict:
    snapshot = {
        "schema_version": 1,
        "entered_at": utc_now(),
        "db": str(db),
        "workers": [],
        "drain_command_ids": [],
        "superseded_push_command_ids": [],
        "safe_idle": False,
    }
    by_key: dict[str, dict] = {}

    def capture_and_drain() -> bool:
        changed = False
        with connect(db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in allocated_workers(conn):
                key = item["worker_id"]
                if key not in by_key:
                    record = {
                        "global_slot": item["global_slot"],
                        "project_id": item["project_id"],
                        "worker_slot": item["worker_slot"],
                        "worker_id": key,
                        "previous_state": item["desired_state"],
                    }
                    by_key[key] = record
                    snapshot["workers"].append(record)
                    # Push intent that predates the deploy drain must never fire
                    # against the restored post-deploy runtime.
                    pending_pushes = conn.execute(
                        "SELECT id FROM runner_commands "
                        "WHERE project_id=? AND action='push' AND status='pending' "
                        "ORDER BY id",
                        (key,),
                    ).fetchall()
                    for push in pending_pushes:
                        push_id = int(push["id"])
                        cursor = conn.execute(
                            "UPDATE runner_commands SET status='failed',updated_at=?,result=? "
                            "WHERE id=? AND status='pending'",
                            (
                                utc_now(),
                                "deploy safe-idle superseded pre-deploy pending push",
                                push_id,
                            ),
                        )
                        if cursor.rowcount == 1:
                            snapshot["superseded_push_command_ids"].append(push_id)
                    command = conn.execute(
                        "INSERT INTO runner_commands("
                        "project_id,action,status,created_at,updated_at,result"
                        ") VALUES(?,?,?,?,?,NULL)",
                        (key, "drain", "pending", utc_now(), utc_now()),
                    )
                    command_id = int(command.lastrowid)
                    record["drain_command_id"] = command_id
                    snapshot["drain_command_ids"].append(command_id)
                    changed = True
                if item["desired_state"] != "paused":
                    conn.execute(
                        "UPDATE runner_workers SET desired_state='draining' "
                        "WHERE project_id=? AND worker_slot=?",
                        (item["project_id"], item["worker_slot"]),
                    )
            conn.commit()
        return changed

    capture_and_drain()
    write_state(state_path, snapshot)

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    stable_since: float | None = None
    try:
        while time.monotonic() < deadline:
            changed = capture_and_drain()
            if changed:
                write_state(state_path, snapshot)

            with connect(db) as conn:
                current_allocated = {
                    item["worker_id"]: item for item in allocated_workers(conn)
                }
                states = {}
                all_paused = True
                auto_quiesced = []
                for key, record in by_key.items():
                    row = conn.execute(
                        "SELECT desired_state FROM runner_workers "
                        "WHERE project_id=? AND worker_slot=?",
                        (record["project_id"], record["worker_slot"]),
                    ).fetchone()
                    desired = str(row["desired_state"] or "running") if row else "missing"
                    if key in current_allocated and desired == "draining":
                        observation = worker_observation(
                            conn, record["project_id"], record["worker_slot"]
                        )
                        age = observation.get("age_seconds")
                        offline_idle = (
                            observation.get("available") is True
                            and age is not None
                            and float(age) >= max(0.0, float(offline_after_seconds))
                            and observation.get("generating") is False
                            and observation.get("sending") is False
                        )
                        command = drain_command_state(conn, key)
                        acknowledged_idle = drain_acknowledged_idle(observation, command)
                        terminal_idle = drain_terminal_idle(observation, command)
                        if offline_idle or acknowledged_idle or terminal_idle:
                            conn.execute(
                                "UPDATE runner_workers SET desired_state='paused' "
                                "WHERE project_id=? AND worker_slot=? AND desired_state='draining'",
                                (record["project_id"], record["worker_slot"]),
                            )
                            command_id = record.get("drain_command_id")
                            if command_id:
                                reason = (
                                    "deploy safe-idle auto-quiesced terminal-stopped worker"
                                    if terminal_idle
                                    else "deploy safe-idle auto-quiesced acknowledged idle worker"
                                    if acknowledged_idle
                                    else "deploy safe-idle auto-quiesced offline idle worker"
                                )
                                conn.execute(
                                    "UPDATE runner_commands SET status='completed',updated_at=?,result=? "
                                    "WHERE id=? AND status='pending'",
                                    (
                                        utc_now(),
                                        reason,
                                        int(command_id),
                                    ),
                                )
                            desired = "paused"
                            auto_quiesced.append({
                                "worker_id": key,
                                "reason": (
                                    "terminal-idle"
                                    if terminal_idle
                                    else "acknowledged-idle"
                                    if acknowledged_idle
                                    else "offline-idle"
                                ),
                                "last_event": observation.get("event"),
                                "last_event_age_seconds": round(float(age), 1),
                            })
                    states[key] = desired
                    if key in current_allocated and desired != "paused":
                        all_paused = False
                if auto_quiesced:
                    conn.commit()
                    snapshot.setdefault("auto_quiesced_workers", []).extend(auto_quiesced)
                    write_state(state_path, snapshot)

            if all_paused:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= max(0.0, stable_seconds):
                    snapshot["safe_idle"] = True
                    snapshot["safe_idle_at"] = utc_now()
                    snapshot["worker_states"] = states
                    write_state(state_path, snapshot)
                    return snapshot
            else:
                stable_since = None
            time.sleep(max(0.01, poll_seconds))
    except Exception:
        restore_snapshot(db, snapshot)
        snapshot["restored_after_error"] = True
        write_state(state_path, snapshot)
        raise

    with connect(db) as conn:
        blockers = blocking_worker_diagnostics(conn, by_key)
    restore_snapshot(db, snapshot)
    snapshot["restored_after_timeout"] = True
    snapshot["worker_states"] = states if "states" in locals() else {}
    snapshot["blocking_workers"] = blockers
    write_state(state_path, snapshot)
    raise SafeIdleError(
        "browser workers did not reach safe-idle before deployment timeout; "
        + json.dumps(blockers, sort_keys=True, separators=(",", ":"))
    )


def restore_safe_idle(db: Path, state_path: Path) -> dict:
    if not state_path.is_file():
        raise SafeIdleError(f"safe-idle state file missing: {state_path}")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    result = restore_snapshot(db, payload)
    payload["restored_at"] = utc_now()
    payload["restored"] = True
    write_state(state_path, payload)
    return {
        "ok": True,
        "safe_idle": bool(payload.get("safe_idle")),
        "restored": result["restored"],
        "cancelled_commands": result["cancelled_commands"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reversible browser safe-idle deploy guard")
    parser.add_argument("action", choices=("enter", "restore"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--stable-seconds", type=float, default=2.0)
    parser.add_argument("--offline-after-seconds", type=float, default=300.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.action == "enter":
            result = enter_safe_idle(
                args.db.resolve(),
                args.state.resolve(),
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
                stable_seconds=args.stable_seconds,
                offline_after_seconds=args.offline_after_seconds,
            )
            output = {
                "ok": True,
                "event": "ZCLOUD_DEPLOY_SAFE_IDLE_ENTERED",
                "worker_count": len(result.get("workers") or []),
                "workers": [item["worker_id"] for item in result.get("workers") or []],
                "state_file": str(args.state.resolve()),
            }
        else:
            result = restore_safe_idle(args.db.resolve(), args.state.resolve())
            output = {
                "ok": True,
                "event": "ZCLOUD_DEPLOY_SAFE_IDLE_RESTORED",
                "worker_count": len(result.get("restored") or []),
                "workers": [item["worker_id"] for item in result.get("restored") or []],
                "state_file": str(args.state.resolve()),
            }
        if args.json:
            print(json.dumps(output, sort_keys=True))
        else:
            print(output["event"], f"workers={output['worker_count']}")
        return 0
    except (SafeIdleError, sqlite3.Error, ValueError, OSError) as exc:
        print(f"ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

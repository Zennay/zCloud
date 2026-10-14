#!/usr/bin/env python3
"""Wait read-only for transient safe-idle blockers to stop active browser work."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("ZCLOUD_DB", "/home/ubuntu/zennay-cloud/history.db"))
WORKER_KEY_RE = re.compile(r"^(?P<project>.+)::w(?P<slot>[1-9][0-9]*)$")


class RetryWaitError(RuntimeError):
    pass


def parse_worker_key(value: str) -> tuple[str, int]:
    match = WORKER_KEY_RE.fullmatch(str(value or "").strip())
    if not match:
        raise RetryWaitError(f"invalid worker key: {value!r}")
    return match.group("project"), int(match.group("slot"))


def connect_read_only(db: Path) -> sqlite3.Connection:
    if db.is_symlink():
        raise RetryWaitError("history database path must not be a symlink")
    if not db.exists() or not db.is_file():
        raise RetryWaitError("history database path must be a regular file")
    resolved = db.resolve(strict=True)
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise RetryWaitError("failed to enable SQLite query_only mode")
    return conn


def allocated_worker_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT project_id,worker_slot FROM ai_global_slots ORDER BY slot"
    ).fetchall()
    return [
        f"{str(row['project_id'])}::w{int(row['worker_slot'] or 1)}"
        for row in rows
    ]


def blocker_state(conn: sqlite3.Connection, worker_id: str) -> dict:
    project_id, worker_slot = parse_worker_key(worker_id)
    allocated = conn.execute(
        "SELECT 1 FROM ai_global_slots WHERE project_id=? AND worker_slot=? LIMIT 1",
        (project_id, worker_slot),
    ).fetchone()
    if allocated is None:
        return {
            "worker_id": worker_id,
            "ready": True,
            "reason": "no-longer-allocated",
            "event": None,
            "generating": None,
            "sending": None,
        }

    event = conn.execute(
        "SELECT event,generating,sending FROM runner_events "
        "WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 1",
        (project_id, worker_slot),
    ).fetchone()
    if event is None:
        return {
            "worker_id": worker_id,
            "ready": False,
            "reason": "missing-runner-evidence",
            "event": None,
            "generating": None,
            "sending": None,
        }

    generating = bool(event["generating"])
    sending = bool(event["sending"])
    return {
        "worker_id": worker_id,
        "ready": not generating and not sending,
        "reason": "idle" if not generating and not sending else "active",
        "event": str(event["event"] or ""),
        "generating": generating,
        "sending": sending,
    }


def wait_until_ready(
    db: Path,
    blockers: list[str],
    *,
    timeout_seconds: float = 900.0,
    poll_seconds: float = 5.0,
    stable_seconds: float = 20.0,
) -> dict:
    if not blockers:
        raise RetryWaitError("at least one blocker is required")
    unique = list(dict.fromkeys(str(item) for item in blockers))
    for worker_id in unique:
        parse_worker_key(worker_id)

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    stable_since: float | None = None
    last_states: list[dict] = []

    with connect_read_only(db) as conn:
        while time.monotonic() < deadline:
            last_states = [blocker_state(conn, worker_id) for worker_id in unique]
            allocation_states = [
                blocker_state(conn, worker_id)
                for worker_id in allocated_worker_ids(conn)
            ]
            blockers_ready = all(item["ready"] for item in last_states)
            allocation_ready = all(item["ready"] for item in allocation_states)
            all_ready = blockers_ready and allocation_ready
            if all_ready:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= max(0.0, float(stable_seconds)):
                    return {
                        "ready": True,
                        "blockers": last_states,
                        "current_allocation": allocation_states,
                        "stable_seconds": max(0.0, float(stable_seconds)),
                    }
            else:
                stable_since = None
            blocker_summary = ",".join(
                f"{item['worker_id']}:{item['reason']}" for item in last_states
            )
            active_allocation = ",".join(
                item["worker_id"]
                for item in allocation_states
                if not item["ready"]
            )
            print(
                "ZCLOUD_SAFE_IDLE_RETRY_WAIT "
                f"blockers={blocker_summary} active_allocation={active_allocation or 'none'}",
                flush=True,
            )
            time.sleep(max(0.1, float(poll_seconds)))

    raise RetryWaitError(
        "transient safe-idle blockers did not become idle before retry budget; "
        + json.dumps(last_states, sort_keys=True, separators=(",", ":"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only wait for transient zCloud safe-idle blockers"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--blockers-json", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--stable-seconds", type=float, default=20.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        blockers = json.loads(args.blockers_json)
        if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
            raise RetryWaitError("--blockers-json must be a JSON list of worker ids")
        result = wait_until_ready(
            args.db,
            blockers,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            stable_seconds=args.stable_seconds,
        )
    except (json.JSONDecodeError, OSError, sqlite3.Error, RetryWaitError, ValueError) as exc:
        print(f"ZCLOUD_SAFE_IDLE_RETRY_BLOCKED: {exc}", file=os.sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "ZCLOUD_SAFE_IDLE_RETRY_READY "
            + ",".join(item["worker_id"] for item in result["blockers"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

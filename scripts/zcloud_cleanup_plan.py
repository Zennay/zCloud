#!/usr/bin/env python3
"""Bounded cleanup for expired zCloud ephemeral SQLite state.

Dry-run is the default. Mutations require an explicit confirmation token and
are limited to rows whose expiry/terminal timestamps are older than a grace
window. Durable queue state, config/audit history, conversations and receipts
are deliberately out of scope.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIRM_TOKEN = "CLEANUP_EXPIRED_EPHEMERAL_STATE"
DEFAULT_GRACE_HOURS = 1
DEFAULT_COMMAND_RETENTION_DAYS = 30
DEFAULT_BATCH_LIMIT = 250
MAX_BATCH_LIMIT = 1000


class CleanupError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def bounded_int(value: int, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CleanupError(f"{label} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise CleanupError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def open_db(path: Path) -> sqlite3.Connection:
    if path.is_symlink():
        raise CleanupError("refusing symlink database path")
    if not path.is_file():
        raise CleanupError(f"database not found: {path}")
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def require_tables(conn: sqlite3.Connection) -> None:
    required = {"task_claims", "worker_preflights", "runner_commands"}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)",
        tuple(sorted(required)),
    ).fetchall()
    found = {str(row["name"]) for row in rows}
    missing = sorted(required - found)
    if missing:
        raise CleanupError(f"required cleanup tables missing: {missing}")


def collect_plan(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime,
    grace_hours: int,
    command_retention_days: int,
    batch_limit: int,
) -> dict:
    grace_cutoff = iso(observed_at - timedelta(hours=grace_hours))
    command_cutoff = iso(observed_at - timedelta(days=command_retention_days))

    claims = [
        {
            "project_id": row["project_id"],
            "claim_key": row["claim_key"],
            "lease_until": row["lease_until"],
        }
        for row in conn.execute(
            """SELECT project_id,claim_key,lease_until
               FROM task_claims
               WHERE lease_until<=?
               ORDER BY lease_until,project_id,claim_key
               LIMIT ?""",
            (grace_cutoff, batch_limit),
        ).fetchall()
    ]
    preflights = [
        {
            "project_id": row["project_id"],
            "worker_id": row["worker_id"],
            "owner_id": row["owner_id"],
            "expires_at": row["expires_at"],
        }
        for row in conn.execute(
            """SELECT project_id,worker_id,owner_id,expires_at
               FROM worker_preflights
               WHERE expires_at<=?
               ORDER BY expires_at,project_id,worker_id,owner_id
               LIMIT ?""",
            (grace_cutoff, batch_limit),
        ).fetchall()
    ]
    commands = [
        {
            "id": int(row["id"]),
            "project_id": row["project_id"],
            "action": row["action"],
            "status": row["status"],
            "updated_at": row["updated_at"],
        }
        for row in conn.execute(
            """SELECT id,project_id,action,status,updated_at
               FROM runner_commands
               WHERE status IN ('completed','failed') AND updated_at<=?
               ORDER BY updated_at,id
               LIMIT ?""",
            (command_cutoff, batch_limit),
        ).fetchall()
    ]
    return {
        "observed_at": iso(observed_at),
        "grace_cutoff": grace_cutoff,
        "command_cutoff": command_cutoff,
        "batch_limit": batch_limit,
        "task_claims": claims,
        "worker_preflights": preflights,
        "runner_commands": commands,
        "counts": {
            "task_claims": len(claims),
            "worker_preflights": len(preflights),
            "runner_commands": len(commands),
        },
    }


def apply_plan(conn: sqlite3.Connection, plan: dict) -> dict:
    deleted = {"task_claims": 0, "worker_preflights": 0, "runner_commands": 0}
    for row in plan["task_claims"]:
        cur = conn.execute(
            """DELETE FROM task_claims
               WHERE project_id=? AND claim_key=? AND lease_until<=?""",
            (row["project_id"], row["claim_key"], plan["grace_cutoff"]),
        )
        deleted["task_claims"] += int(cur.rowcount)
    for row in plan["worker_preflights"]:
        cur = conn.execute(
            """DELETE FROM worker_preflights
               WHERE project_id=? AND worker_id=? AND owner_id=? AND expires_at<=?""",
            (
                row["project_id"],
                row["worker_id"],
                row["owner_id"],
                plan["grace_cutoff"],
            ),
        )
        deleted["worker_preflights"] += int(cur.rowcount)
    command_ids = [int(row["id"]) for row in plan["runner_commands"]]
    for command_id in command_ids:
        cur = conn.execute(
            """DELETE FROM runner_commands
               WHERE id=? AND status IN ('completed','failed') AND updated_at<=?""",
            (command_id, plan["command_cutoff"]),
        )
        deleted["runner_commands"] += int(cur.rowcount)
    return deleted


def run_cleanup(
    db_path: Path,
    *,
    apply: bool,
    confirm: str,
    grace_hours: int,
    command_retention_days: int,
    batch_limit: int,
    observed_at: datetime | None = None,
) -> dict:
    grace_hours = bounded_int(
        grace_hours, minimum=1, maximum=168, label="grace_hours"
    )
    command_retention_days = bounded_int(
        command_retention_days,
        minimum=7,
        maximum=365,
        label="command_retention_days",
    )
    batch_limit = bounded_int(
        batch_limit, minimum=1, maximum=MAX_BATCH_LIMIT, label="batch_limit"
    )
    if apply and confirm != CONFIRM_TOKEN:
        raise CleanupError(
            f"--apply requires --confirm {CONFIRM_TOKEN}"
        )

    observed_at = observed_at or utc_now()
    with open_db(db_path) as conn:
        require_tables(conn)
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        plan = collect_plan(
            conn,
            observed_at=observed_at,
            grace_hours=grace_hours,
            command_retention_days=command_retention_days,
            batch_limit=batch_limit,
        )
        deleted = {"task_claims": 0, "worker_preflights": 0, "runner_commands": 0}
        if apply:
            deleted = apply_plan(conn, plan)
            conn.commit()
        return {
            "ok": True,
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path),
            "scope": [
                "expired_task_claims",
                "expired_worker_preflights",
                "old_terminal_runner_commands",
            ],
            "excluded": [
                "portfolio_queue",
                "runner_workers",
                "runner_targets",
                "ai_global_slots",
                "conversation_ids",
                "config_audit",
                "project_state_receipts",
                "artifacts",
            ],
            "plan": plan,
            "deleted": deleted,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or apply bounded cleanup of expired zCloud ephemeral state"
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--grace-hours", type=int, default=DEFAULT_GRACE_HOURS)
    parser.add_argument(
        "--command-retention-days",
        type=int,
        default=DEFAULT_COMMAND_RETENTION_DAYS,
    )
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    args = parser.parse_args(argv)
    try:
        result = run_cleanup(
            args.db,
            apply=args.apply,
            confirm=args.confirm,
            grace_hours=args.grace_hours,
            command_retention_days=args.command_retention_days,
            batch_limit=args.batch_limit,
        )
    except CleanupError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

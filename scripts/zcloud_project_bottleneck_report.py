#!/usr/bin/env python3
"""Read-only project bottleneck classification from zCloud runtime evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable


ACTIVE_QUEUE_STATUSES = ("claimed", "running", "verifying", "in_progress")
REQUIRED_TABLE_COLUMNS = {
    "portfolio_queue": {
        "project_id",
        "status",
        "eligible",
        "updated_at",
        "blocker",
    },
    "portfolio_attention": {
        "project_id",
        "status",
        "severity",
    },
    "ai_global_slots": {
        "project_id",
        "worker_slot",
    },
    "task_claims": {
        "project_id",
        "lease_until",
    },
    "project_state_receipts": {
        "id",
        "project_id",
        "ci_status",
        "blocker",
        "observed_at",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: object, *, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} timestamp missing")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_regular_file(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} path must be a regular file")


def _load_json(path: Path, *, label: str):
    _require_regular_file(path, label=label)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} JSON is unreadable") from exc


def _active_project_ids(projects_path: Path) -> list[str]:
    projects = _load_json(projects_path, label="projects")
    if not isinstance(projects, list):
        raise ValueError("projects JSON must be a list")
    ids: list[str] = []
    seen: set[str] = set()
    for project in projects:
        if not isinstance(project, dict):
            raise ValueError("project entries must be objects")
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            raise ValueError("project id must be non-empty")
        if project_id in seen:
            raise ValueError(f"duplicate project id {project_id!r}")
        seen.add(project_id)
        if str(project.get("status") or "active").strip().lower() != "archived":
            ids.append(project_id)
    if not ids:
        raise ValueError("no active projects found")
    return ids


def _contracts(contracts_path: Path) -> dict[str, dict]:
    payload = _load_json(contracts_path, label="contracts")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("contracts schema_version must be 1")
    projects = payload.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("contracts projects must be a non-empty object")
    normalized: dict[str, dict] = {}
    for project_id, contract in projects.items():
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("contract project id must be non-empty")
        if not isinstance(contract, dict):
            raise ValueError(f"contract for {project_id!r} must be an object")
        cap = contract.get("ai_worker_cap")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
            raise ValueError(f"contract for {project_id!r} has invalid ai_worker_cap")
        normalized[project_id] = contract
    return normalized


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    _require_regular_file(db_path, label="database")
    connection = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA query_only=ON")
    if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
        connection.close()
        raise RuntimeError("SQLite query_only guard is not active")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _validate_schema(connection: sqlite3.Connection) -> None:
    for table, required in REQUIRED_TABLE_COLUMNS.items():
        columns = _table_columns(connection, table)
        if not columns:
            raise RuntimeError(f"required table {table!r} is missing")
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(
                f"required table {table!r} is missing columns: {', '.join(missing)}"
            )


def _age_seconds(value: object, *, now: datetime, label: str) -> int:
    observed = _parse_timestamp(value, label=label)
    age = int((now - observed).total_seconds())
    return max(0, age)


def _count(connection: sqlite3.Connection, sql: str, params: Iterable[object]) -> int:
    row = connection.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0)


def _project_snapshot(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    contract: dict,
    now: datetime,
    stale_seconds: int,
) -> dict:
    queue_ready = _count(
        connection,
        "SELECT COUNT(*) FROM portfolio_queue "
        "WHERE project_id=? AND eligible=1 AND status='queued'",
        (project_id,),
    )
    queue_active = _count(
        connection,
        "SELECT COUNT(*) FROM portfolio_queue "
        "WHERE project_id=? AND status IN ('claimed','running','verifying','in_progress')",
        (project_id,),
    )
    allocated_workers = _count(
        connection,
        "SELECT COUNT(*) FROM ai_global_slots WHERE project_id=?",
        (project_id,),
    )
    live_claims = _count(
        connection,
        "SELECT COUNT(*) FROM task_claims "
        "WHERE project_id=? AND unixepoch(lease_until) > unixepoch(?)",
        (project_id, now.isoformat()),
    )
    attention_rows = connection.execute(
        "SELECT severity FROM portfolio_attention "
        "WHERE project_id=? AND status='open'",
        (project_id,),
    ).fetchall()
    open_attention = len(attention_rows)
    urgent_attention = sum(
        1 for row in attention_rows if str(row["severity"] or "").lower() == "urgent"
    )

    oldest_active = connection.execute(
        "SELECT updated_at FROM portfolio_queue "
        "WHERE project_id=? AND status IN ('claimed','running','verifying','in_progress') "
        "ORDER BY unixepoch(updated_at) ASC, queue_id ASC LIMIT 1",
        (project_id,),
    ).fetchone()
    oldest_active_age = None
    if oldest_active is not None:
        oldest_active_age = _age_seconds(
            oldest_active["updated_at"],
            now=now,
            label=f"{project_id} active queue",
        )

    receipt = connection.execute(
        "SELECT id,ci_status,blocker,observed_at FROM project_state_receipts "
        "WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    receipt_age = None
    receipt_blocker_present = False
    latest_ci_status = ""
    if receipt is not None:
        receipt_age = _age_seconds(
            receipt["observed_at"],
            now=now,
            label=f"{project_id} receipt",
        )
        receipt_blocker_present = bool(str(receipt["blocker"] or "").strip())
        latest_ci_status = str(receipt["ci_status"] or "").strip()[:64]

    cap = int(contract.get("ai_worker_cap") or 0)
    queue_mode = str(contract.get("queue_mode") or "").strip().lower()
    autonomy = contract.get("autonomy") if isinstance(contract.get("autonomy"), dict) else {}
    autonomy_mode = str(autonomy.get("mode") or "").strip().lower()

    if queue_mode == "human-gated" or (
        cap == 0 and autonomy_mode in {"external_gate", "manual"}
    ):
        code = "external_or_human_gate"
        state = "blocked"
    elif open_attention:
        code = "human_attention_required"
        state = "blocked"
    elif receipt_blocker_present:
        code = "receipt_blocker_present"
        state = "blocked"
    elif oldest_active_age is not None and oldest_active_age >= stale_seconds:
        code = "active_work_stale"
        state = "blocked"
    elif queue_ready and allocated_workers == 0:
        code = "runnable_work_waiting_for_worker"
        state = "waiting"
    elif queue_ready and cap > 0 and allocated_workers >= cap:
        code = "project_worker_cap_saturated"
        state = "waiting"
    elif queue_ready:
        code = "runnable_work_waiting_behind_active_work"
        state = "waiting"
    elif queue_active or live_claims or allocated_workers:
        code = "active_execution"
        state = "running"
    else:
        code = "no_runnable_work"
        state = "idle"

    return {
        "project_id": project_id,
        "state": state,
        "bottleneck_code": code,
        "queue_ready": queue_ready,
        "queue_active": queue_active,
        "allocated_workers": allocated_workers,
        "live_claims": live_claims,
        "ai_worker_cap": cap,
        "open_attention": open_attention,
        "urgent_attention": urgent_attention,
        "oldest_active_age_seconds": oldest_active_age,
        "latest_receipt_age_seconds": receipt_age,
        "latest_ci_status": latest_ci_status,
        "receipt_blocker_present": receipt_blocker_present,
    }


def build_report(
    db_path: Path,
    projects_path: Path,
    contracts_path: Path,
    *,
    project_ids: list[str] | None = None,
    stale_minutes: int = 20,
    now: datetime | None = None,
) -> dict:
    if stale_minutes < 1 or stale_minutes > 24 * 60:
        raise ValueError("stale_minutes must be between 1 and 1440")
    observed_at = (now or _now()).astimezone(timezone.utc)
    active = _active_project_ids(projects_path)
    contracts = _contracts(contracts_path)

    unknown_contracts = sorted(project_id for project_id in active if project_id not in contracts)
    if unknown_contracts:
        raise ValueError(
            "active projects missing runtime contracts: " + ", ".join(unknown_contracts)
        )

    selected = active
    if project_ids:
        requested = []
        seen: set[str] = set()
        for project_id in project_ids:
            normalized = str(project_id or "").strip()
            if not normalized:
                raise ValueError("project filter must be non-empty")
            if normalized not in active:
                raise ValueError(f"unknown or archived project {normalized!r}")
            if normalized not in seen:
                seen.add(normalized)
                requested.append(normalized)
        selected = requested

    connection = _connect_read_only(db_path)
    try:
        _validate_schema(connection)
        before = connection.total_changes
        projects = [
            _project_snapshot(
                connection,
                project_id=project_id,
                contract=contracts[project_id],
                now=observed_at,
                stale_seconds=stale_minutes * 60,
            )
            for project_id in selected
        ]
        if connection.total_changes != before:
            raise RuntimeError("read-only report unexpectedly changed SQLite state")
    finally:
        connection.close()

    counts = {
        state: sum(1 for project in projects if project["state"] == state)
        for state in ("running", "waiting", "blocked", "idle")
    }
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "stale_after_seconds": stale_minutes * 60,
        "counts": counts,
        "projects": projects,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--stale-minutes", type=int, default=20)
    parser.add_argument(
        "--now",
        help="Timezone-aware ISO timestamp for deterministic validation.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    observed_at = None
    if args.now:
        observed_at = _parse_timestamp(args.now, label="--now")
    report = build_report(
        args.db,
        args.projects,
        args.contracts,
        project_ids=args.project or None,
        stale_minutes=args.stale_minutes,
        now=observed_at,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

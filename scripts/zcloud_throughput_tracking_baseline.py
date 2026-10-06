#!/usr/bin/env python3
"""Initialize immutable project throughput tracking baselines safely."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CONFIRM_TOKEN = "INITIALIZE_THROUGHPUT_TRACKING_BASELINES"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TABLE = "throughput_tracking_baselines"


def _parse_utc(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("started_at is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("started_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_regular_file(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} path must be an existing regular file")
    return path.resolve()


def _active_projects(projects_path: Path) -> list[str]:
    path = _validate_regular_file(projects_path, "projects")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("projects file must contain a list")
    projects = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip().lower()
        if not project_id:
            continue
        if str(item.get("status") or "active").strip().lower() == "archived":
            continue
        projects.append(project_id)
    projects = sorted(set(projects))
    if not projects:
        raise ValueError("projects file contains no active project ids")
    return projects


def _table_exists(connection: sqlite3.Connection) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone()
    )


def _validate_existing_table(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({TABLE})").fetchall()
    }
    required = {
        "project_id",
        "started_at",
        "source",
        "production_sha",
        "created_at",
    }
    missing = sorted(required - columns)
    if missing:
        raise ValueError(
            f"{TABLE} missing required columns: " + ",".join(missing)
        )


def _existing_rows(connection: sqlite3.Connection) -> dict[str, dict]:
    if not _table_exists(connection):
        return {}
    _validate_existing_table(connection)
    rows = connection.execute(
        f"""SELECT project_id,started_at,source,production_sha,created_at
            FROM {TABLE} ORDER BY project_id"""
    ).fetchall()
    return {str(row["project_id"]): dict(row) for row in rows}


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    path = _validate_regular_file(db_path, "database")
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA query_only=ON")
    return connection


def _open_write(db_path: Path) -> sqlite3.Connection:
    path = _validate_regular_file(db_path, "database")
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def _normalized_inputs(
    projects_path: Path,
    started_at: str,
    production_sha: str,
    source: str,
) -> tuple[list[str], str, str, str]:
    projects = _active_projects(projects_path)
    started = _parse_utc(started_at)
    sha = str(production_sha or "").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise ValueError("production_sha must be an exact 40-character lowercase commit SHA")
    source_value = str(source or "").strip()
    if not source_value:
        raise ValueError("source is required")
    if len(source_value.encode("utf-8")) > 300:
        raise ValueError("source is too long")
    return projects, started, sha, source_value


def _plan(
    existing: dict[str, dict],
    projects: list[str],
    *,
    started_at: str,
    production_sha: str,
    source: str,
) -> dict:
    planned = []
    unchanged = []
    conflicts = []
    for project_id in projects:
        row = existing.get(project_id)
        if row is None:
            planned.append(project_id)
            continue
        current = {
            "started_at": str(row.get("started_at") or ""),
            "production_sha": str(row.get("production_sha") or ""),
            "source": str(row.get("source") or ""),
        }
        requested = {
            "started_at": started_at,
            "production_sha": production_sha,
            "source": source,
        }
        if current == requested:
            unchanged.append(project_id)
        else:
            conflicts.append(
                {
                    "project_id": project_id,
                    "existing": current,
                    "requested": requested,
                }
            )
    return {
        "planned": planned,
        "unchanged": unchanged,
        "conflicts": conflicts,
    }


def plan_baseline(
    db_path: Path,
    projects_path: Path,
    *,
    started_at: str,
    production_sha: str,
    source: str,
) -> dict:
    projects, started, sha, source_value = _normalized_inputs(
        projects_path,
        started_at,
        production_sha,
        source,
    )
    connection = _open_read_only(db_path)
    try:
        table_exists = _table_exists(connection)
        existing = _existing_rows(connection)
    finally:
        connection.close()
    plan = _plan(
        existing,
        projects,
        started_at=started,
        production_sha=sha,
        source=source_value,
    )
    return {
        "schema_version": 1,
        "dry_run": True,
        "table_exists": table_exists,
        "started_at": started,
        "production_sha": sha,
        "source": source_value,
        "project_count": len(projects),
        "projects": projects,
        "existing_count": len(existing),
        **plan,
    }


def apply_baseline(
    db_path: Path,
    projects_path: Path,
    *,
    started_at: str,
    production_sha: str,
    source: str,
    confirm: str,
) -> dict:
    if str(confirm or "") != CONFIRM_TOKEN:
        raise ValueError("exact confirmation token is required for apply")
    projects, started, sha, source_value = _normalized_inputs(
        projects_path,
        started_at,
        production_sha,
        source,
    )
    connection = _open_write(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"""CREATE TABLE IF NOT EXISTS {TABLE}(
                project_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                production_sha TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        _validate_existing_table(connection)
        existing = _existing_rows(connection)
        plan = _plan(
            existing,
            projects,
            started_at=started,
            production_sha=sha,
            source=source_value,
        )
        if plan["conflicts"]:
            raise ValueError(
                "existing throughput baseline conflicts with requested immutable baseline"
            )
        created_at = datetime.now(timezone.utc).isoformat()
        for project_id in plan["planned"]:
            connection.execute(
                f"""INSERT INTO {TABLE}(
                    project_id,started_at,source,production_sha,created_at
                ) VALUES(?,?,?,?,?)""",
                (project_id, started, source_value, sha, created_at),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "schema_version": 1,
        "dry_run": False,
        "applied": bool(plan["planned"]),
        "started_at": started,
        "production_sha": sha,
        "source": source_value,
        "project_count": len(projects),
        "created_count": len(plan["planned"]),
        "created": plan["planned"],
        "unchanged": plan["unchanged"],
        "conflicts": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or initialize immutable throughput tracking baselines for active "
            "zCloud projects. Dry-run is the default."
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--production-sha", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.apply:
        result = apply_baseline(
            args.db,
            args.projects,
            started_at=args.started_at,
            production_sha=args.production_sha,
            source=args.source,
            confirm=args.confirm,
        )
    else:
        result = plan_baseline(
            args.db,
            args.projects,
            started_at=args.started_at,
            production_sha=args.production_sha,
            source=args.source,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

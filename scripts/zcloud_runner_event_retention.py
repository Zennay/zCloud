#!/usr/bin/env python3
"""Bounded selective retention for high-frequency zCloud runner telemetry.

Dry-run is the default. Apply requires an explicit confirmation token. Only a
small allowlist of high-frequency state/transport telemetry can be removed, and
the newest row for every allowlisted event type is always preserved.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIRM_TOKEN = "PRUNE_BOUNDED_RUNNER_TELEMETRY"
MIN_RETENTION_HOURS = 192  # 168h max scaling window + 24h safety buffer.
DEFAULT_RETENTION_HOURS = 192
DEFAULT_BATCH_LIMIT = 5000
MAX_BATCH_LIMIT = 10000

PRUNABLE_EVENTS = frozenset({
    "heartbeat",
    "targets-loaded",
    "runner-config-updated",
    "violentmonkey-missing-fallback",
    "thinking-effort-high-required",
    "injection-success",
})


class RetentionError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def bounded_int(value: int, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RetentionError(f"{label} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise RetentionError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def open_db(path: Path, *, apply: bool) -> sqlite3.Connection:
    if path.is_symlink():
        raise RetentionError("refusing symlink database path")
    if not path.is_file():
        raise RetentionError(f"database not found: {path}")
    if apply:
        conn = sqlite3.connect(path, timeout=15)
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000" if apply else "PRAGMA busy_timeout=5000")
    return conn


def require_schema(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_events'"
    ).fetchone()
    if not table:
        raise RetentionError("runner_events table missing")
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(runner_events)").fetchall()
    }
    required = {"id", "ts", "event"}
    if not required.issubset(columns):
        raise RetentionError(
            f"runner_events missing required columns: {sorted(required - columns)}"
        )


def select_candidates(
    conn: sqlite3.Connection,
    *,
    cutoff: str,
    batch_limit: int,
) -> list[dict]:
    placeholders = ",".join("?" for _ in PRUNABLE_EVENTS)
    event_values = tuple(sorted(PRUNABLE_EVENTS))
    rows = conn.execute(
        f"""SELECT id,event,ts
            FROM runner_events
            WHERE event IN ({placeholders})
              AND unixepoch(ts) IS NOT NULL
              AND unixepoch(ts) < unixepoch(?)
              AND id NOT IN (
                    SELECT MAX(id)
                    FROM runner_events
                    WHERE event IN ({placeholders})
                    GROUP BY event
              )
            ORDER BY id
            LIMIT ?""",
        (*event_values, cutoff, *event_values, batch_limit),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "event": str(row["event"]),
            "ts": str(row["ts"]),
        }
        for row in rows
    ]


def eligible_counts(conn: sqlite3.Connection, *, cutoff: str) -> dict[str, int]:
    placeholders = ",".join("?" for _ in PRUNABLE_EVENTS)
    event_values = tuple(sorted(PRUNABLE_EVENTS))
    rows = conn.execute(
        f"""SELECT event,COUNT(*) AS n
            FROM runner_events
            WHERE event IN ({placeholders})
              AND unixepoch(ts) IS NOT NULL
              AND unixepoch(ts) < unixepoch(?)
              AND id NOT IN (
                    SELECT MAX(id)
                    FROM runner_events
                    WHERE event IN ({placeholders})
                    GROUP BY event
              )
            GROUP BY event
            ORDER BY event""",
        (*event_values, cutoff, *event_values),
    ).fetchall()
    return {str(row["event"]): int(row["n"] or 0) for row in rows}


def database_meta(conn: sqlite3.Connection) -> dict:
    return {
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(conn.execute("PRAGMA freelist_count").fetchone()[0]),
        "page_size": int(conn.execute("PRAGMA page_size").fetchone()[0]),
    }


def run_retention(
    db_path: Path,
    *,
    apply: bool,
    confirm: str,
    retention_hours: int,
    batch_limit: int,
    observed_at: datetime | None = None,
) -> dict:
    retention_hours = bounded_int(
        retention_hours,
        minimum=MIN_RETENTION_HOURS,
        maximum=24 * 90,
        label="retention_hours",
    )
    batch_limit = bounded_int(
        batch_limit,
        minimum=1,
        maximum=MAX_BATCH_LIMIT,
        label="batch_limit",
    )
    if apply and confirm != CONFIRM_TOKEN:
        raise RetentionError(f"--apply requires --confirm {CONFIRM_TOKEN}")

    observed_at = (observed_at or utc_now()).astimezone(timezone.utc)
    cutoff = iso(observed_at - timedelta(hours=retention_hours))

    with open_db(db_path, apply=apply) as conn:
        require_schema(conn)
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        before = database_meta(conn)
        eligible_by_event = eligible_counts(conn, cutoff=cutoff)
        candidates = select_candidates(
            conn,
            cutoff=cutoff,
            batch_limit=batch_limit,
        )
        deleted = 0
        deleted_by_event: Counter[str] = Counter()
        if apply:
            for item in candidates:
                cur = conn.execute(
                    """DELETE FROM runner_events
                       WHERE id=? AND event=?
                         AND unixepoch(ts) IS NOT NULL
                         AND unixepoch(ts) < unixepoch(?)
                         AND id <> (
                             SELECT MAX(id) FROM runner_events WHERE event=?
                         )""",
                    (item["id"], item["event"], cutoff, item["event"]),
                )
                if cur.rowcount:
                    deleted += int(cur.rowcount)
                    deleted_by_event[item["event"]] += int(cur.rowcount)
            conn.commit()
        after = database_meta(conn)

    planned_by_event = Counter(item["event"] for item in candidates)
    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "observed_at": iso(observed_at),
        "cutoff": cutoff,
        "retention_hours": retention_hours,
        "batch_limit": batch_limit,
        "allowlist": sorted(PRUNABLE_EVENTS),
        "policy": {
            "minimum_retention_hours": MIN_RETENTION_HOURS,
            "max_worker_scaling_window_hours": 168,
            "safety_buffer_hours": MIN_RETENTION_HOURS - 168,
            "preserve_latest_per_event": True,
            "malformed_timestamps_preserved": True,
            "non_allowlisted_events_preserved": True,
            "vacuum": False,
        },
        "eligible": {
            "count": sum(eligible_by_event.values()),
            "by_event": dict(sorted(eligible_by_event.items())),
        },
        "planned": {
            "count": len(candidates),
            "by_event": dict(sorted(planned_by_event.items())),
            "oldest_id": candidates[0]["id"] if candidates else None,
            "newest_id": candidates[-1]["id"] if candidates else None,
            "has_more": sum(eligible_by_event.values()) > len(candidates),
        },
        "deleted": {
            "count": deleted,
            "by_event": dict(sorted(deleted_by_event.items())),
        },
        "database_before": before,
        "database_after": after,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply bounded retention for high-frequency runner telemetry"
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=DEFAULT_RETENTION_HOURS,
    )
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_retention(
            args.db,
            apply=args.apply,
            confirm=args.confirm,
            retention_hours=args.retention_hours,
            batch_limit=args.batch_limit,
        )
    except RetentionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

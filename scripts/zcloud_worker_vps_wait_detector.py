#!/usr/bin/env python3
"""Detect repeated VPS-wait worker attention from existing zCloud telemetry, read only."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

POLICY = "zcloud-worker-vps-wait-detector-v1"
MAX_ROWS = 2000
MAX_WINDOW_HOURS = 24.0
SIGNAL_EVENT = "autonomy-wait-vps"
RESET_EVENTS = frozenset(
    {
        "autonomy-continue",
        "autonomy-complete",
        "prompt-sent",
        "generation-started",
        "generation-finished",
        "portfolio-queue-result",
    }
)
RELEVANT_EVENTS = frozenset({SIGNAL_EVENT, *RESET_EVENTS})
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REQUIRED_COLUMNS = frozenset(
    {"id", "ts", "event", "project_id", "worker_slot", "generating", "sending"}
)


class WaitEvidenceError(ValueError):
    """Fail-closed bounded telemetry error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _parse_timestamp(value: Any, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise WaitEvidenceError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WaitEvidenceError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WaitEvidenceError(code)
    return parsed.astimezone(dt.timezone.utc)


def _validate_db_path(path: Path) -> None:
    try:
        path.lstat()
    except OSError as exc:
        raise WaitEvidenceError("db_unreadable") from exc
    if path.is_symlink():
        raise WaitEvidenceError("db_symlink_rejected")
    if not path.is_file():
        raise WaitEvidenceError("db_not_regular")


def _open_read_only(path: Path) -> sqlite3.Connection:
    _validate_db_path(path)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error as exc:
        raise WaitEvidenceError("db_open_failed") from exc


def _schema_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("PRAGMA table_info(runner_events)").fetchall()
    except sqlite3.Error as exc:
        raise WaitEvidenceError("runner_events_schema_unreadable") from exc
    columns = {str(row["name"]) for row in rows}
    if not columns:
        raise WaitEvidenceError("runner_events_missing")
    if not REQUIRED_COLUMNS.issubset(columns):
        raise WaitEvidenceError("runner_events_columns_incomplete")
    return columns


def detect_vps_wait(
    db: Path,
    *,
    hours: float = 2.0,
    min_signals: int = 2,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if not 0.25 <= float(hours) <= MAX_WINDOW_HOURS:
        raise WaitEvidenceError("window_hours_invalid")
    if not 2 <= int(min_signals) <= 20:
        raise WaitEvidenceError("min_signals_invalid")

    now_utc = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    cutoff = now_utc - dt.timedelta(hours=float(hours))
    placeholders = ",".join("?" for _ in sorted(RELEVANT_EVENTS))
    event_names = sorted(RELEVANT_EVENTS)

    with _open_read_only(db) as conn:
        _schema_columns(conn)
        try:
            rows = conn.execute(
                "SELECT id,ts,event,project_id,worker_slot,generating,sending "
                "FROM runner_events "
                f"WHERE ts>=? AND event IN ({placeholders}) "
                "AND project_id IS NOT NULL AND project_id<>'' "
                "ORDER BY id DESC LIMIT ?",
                [cutoff.isoformat(), *event_names, MAX_ROWS + 1],
            ).fetchall()
        except sqlite3.Error as exc:
            raise WaitEvidenceError("runner_events_query_failed") from exc

    if len(rows) > MAX_ROWS:
        raise WaitEvidenceError("event_inventory_overflow")

    ordered = list(reversed(rows))
    states: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {
            "consecutive_wait_signals": 0,
            "latest_relevant_event": None,
            "latest_signal_at": None,
            "latest_signal_active": False,
            "relevant_event_count": 0,
        }
    )

    for row in ordered:
        project_id = str(row["project_id"] or "")
        if not PROJECT_RE.fullmatch(project_id):
            raise WaitEvidenceError("project_id_invalid")
        try:
            slot = int(row["worker_slot"])
        except (TypeError, ValueError) as exc:
            raise WaitEvidenceError("worker_slot_invalid") from exc
        if slot < 1 or slot > 999:
            raise WaitEvidenceError("worker_slot_invalid")

        event = str(row["event"] or "")
        if event not in RELEVANT_EVENTS:
            raise WaitEvidenceError("event_identity_invalid")
        observed_at = _parse_timestamp(row["ts"], "event_timestamp_invalid")
        if observed_at > now_utc + dt.timedelta(seconds=5):
            raise WaitEvidenceError("event_from_future")

        generating = row["generating"]
        sending = row["sending"]
        if generating not in (0, 1, False, True, None) or sending not in (
            0,
            1,
            False,
            True,
            None,
        ):
            raise WaitEvidenceError("worker_activity_invalid")

        state = states[(project_id, slot)]
        state["relevant_event_count"] += 1
        state["latest_relevant_event"] = event

        if event == SIGNAL_EVENT:
            active = bool(generating) or bool(sending)
            state["latest_signal_at"] = observed_at.isoformat()
            state["latest_signal_active"] = active
            if active:
                state["consecutive_wait_signals"] = 0
            else:
                state["consecutive_wait_signals"] += 1
        else:
            state["consecutive_wait_signals"] = 0
            state["latest_signal_active"] = False

    workers: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for (project_id, slot), state in sorted(states.items()):
        count = int(state["consecutive_wait_signals"])
        candidate = (
            state["latest_relevant_event"] == SIGNAL_EVENT
            and not state["latest_signal_active"]
            and count >= int(min_signals)
        )
        worker_id = f"{project_id}::w{slot}"
        row = {
            "worker_id": worker_id,
            "project_id": project_id,
            "worker_slot": slot,
            "relevant_event_count": int(state["relevant_event_count"]),
            "consecutive_vps_wait_signals": count,
            "latest_relevant_event": state["latest_relevant_event"],
            "latest_signal_at": state["latest_signal_at"],
            "delegate_candidate": candidate,
        }
        workers.append(row)
        if candidate:
            candidates.append(
                {
                    "worker_id": worker_id,
                    "project_id": project_id,
                    "worker_slot": slot,
                    "consecutive_vps_wait_signals": count,
                    "latest_signal_at": state["latest_signal_at"],
                    "reason_code": "repeated_vps_wait_no_progress",
                    "routing_advice": "delegate_vps_wait_and_continue",
                }
            )

    return {
        "ok": True,
        "policy": POLICY,
        "captured_at": now_utc.isoformat(),
        "window_start": cutoff.isoformat(),
        "window_hours": float(hours),
        "min_signals": int(min_signals),
        "event_inventory_complete": True,
        "scanned_event_count": len(rows),
        "worker_count": len(workers),
        "delegate_candidate_count": len(candidates),
        "workers": workers,
        "candidates": candidates,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path)
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--min-signals", type=int, default=2)
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = detect_vps_wait(
            args.db,
            hours=args.hours,
            min_signals=args.min_signals,
        )
    except WaitEvidenceError as exc:
        payload = {
            "ok": False,
            "policy": POLICY,
            "status": "incomplete",
            "errors": [exc.code],
            "mutation_performed": False,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1 if args.require_compatible else 2

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

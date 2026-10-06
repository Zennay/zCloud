#!/usr/bin/env python3
"""Bounded read-only "since last visit" delta report for zCloud.

The reporter intentionally exposes only compact, machine-readable change
categories. It never returns raw action, blocker, error, reason, evidence or
prompt text from runtime state.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "since-visit-v1"
MAX_LOOKBACK = timedelta(days=31)
MAX_ROWS = 2000
MAX_PROJECTS = 50
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")

MEANINGFUL_RUNNER_EVENTS = {
    "generation-finished": "generation_finished",
    "portfolio-queue-result": "task_result",
    "conversation-adopted": "worker_recovered",
    "startup-blocked": "worker_blocked",
    "send-blocked": "worker_blocked",
    "stall-detected": "worker_blocked",
    "composer-stalled": "worker_blocked",
}


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _normalize_since(value: str, now: datetime) -> datetime:
    since = _dt(value)
    now = now.astimezone(timezone.utc)
    if since > now + timedelta(minutes=1):
        raise ValueError("since timestamp cannot be in the future")
    if now - since > MAX_LOOKBACK:
        raise ValueError("since timestamp exceeds the 31-day bounded lookback")
    return since


def _project(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip().lower()
    if not PROJECT_RE.fullmatch(value):
        raise ValueError("project must be a bounded lowercase project id")
    return value


def _open_ro(db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _require_schema(connection: sqlite3.Connection) -> None:
    required = {
        "project_state_receipts": {"id", "project_id", "observed_at", "phase", "ci_status", "commit_sha"},
        "runner_events": {"id", "project_id", "ts", "event"},
    }
    for table, columns in required.items():
        actual = _columns(connection, table)
        if not actual:
            raise RuntimeError(f"required runtime table missing: {table}")
        missing = columns - actual
        if missing:
            raise RuntimeError(f"required runtime columns missing from {table}: {','.join(sorted(missing))}")


def _safe_phase(value: object) -> str:
    phase = str(value or "").strip().lower()
    if not phase:
        return ""
    if re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,47}", phase):
        return phase
    return "other"


def _safe_ci(value: object) -> str:
    status = str(value or "").strip().lower().replace(" ", "_")
    allowed = {
        "", "success", "failure", "failed", "pending", "queued", "running",
        "skipped", "cancelled", "canceled", "neutral", "unknown",
    }
    return status if status in allowed else "other"


def _safe_sha(value: object) -> str | None:
    sha = str(value or "").strip()
    if not HEX_SHA_RE.fullmatch(sha):
        return None
    return sha[:12].lower()


def report(
    db: Path,
    since: str,
    project: str | None = None,
    *,
    now: datetime | None = None,
    row_limit: int = MAX_ROWS,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since_dt = _normalize_since(since, now)
    project_id = _project(project)
    if not isinstance(row_limit, int) or isinstance(row_limit, bool) or row_limit < 1 or row_limit > MAX_ROWS:
        raise ValueError(f"row_limit must be an integer between 1 and {MAX_ROWS}")

    since_iso = since_dt.isoformat()
    with closing(_open_ro(db)) as connection:
        _require_schema(connection)

        receipt_where = "observed_at>=? AND project_id IS NOT NULL AND project_id<>''"
        receipt_params: list[object] = [since_iso]
        event_where = "ts>=? AND project_id IS NOT NULL AND project_id<>''"
        event_params: list[object] = [since_iso]
        if project_id:
            receipt_where += " AND project_id=?"
            event_where += " AND project_id=?"
            receipt_params.append(project_id)
            event_params.append(project_id)

        receipts = connection.execute(
            f"""SELECT id,project_id,observed_at,phase,ci_status,commit_sha
                FROM project_state_receipts
                WHERE {receipt_where}
                ORDER BY observed_at,id
                LIMIT ?""",
            [*receipt_params, row_limit + 1],
        ).fetchall()

        placeholders = ",".join("?" for _ in MEANINGFUL_RUNNER_EVENTS)
        events = connection.execute(
            f"""SELECT id,project_id,ts,event
                FROM runner_events
                WHERE {event_where} AND event IN ({placeholders})
                ORDER BY ts,id
                LIMIT ?""",
            [*event_params, *MEANINGFUL_RUNNER_EVENTS.keys(), row_limit + 1],
        ).fetchall()

    truncated = len(receipts) > row_limit or len(events) > row_limit
    receipts = receipts[:row_limit]
    events = events[:row_limit]

    state: dict[str, dict] = defaultdict(
        lambda: {
            "counts": defaultdict(int),
            "latest_at": None,
            "latest_source": None,
            "latest_phase": "",
            "latest_ci_status": "",
            "latest_commit": None,
        }
    )

    def touch(pid: str, observed_at: str, source: str) -> None:
        entry = state[pid]
        current = entry["latest_at"]
        if current is None or _dt(observed_at) >= _dt(current):
            entry["latest_at"] = _dt(observed_at).isoformat()
            entry["latest_source"] = source

    for row in receipts:
        pid = str(row["project_id"]).strip().lower()
        if not PROJECT_RE.fullmatch(pid):
            continue
        entry = state[pid]
        entry["counts"]["state_receipt"] += 1
        touch(pid, str(row["observed_at"]), "state_receipt")
        if entry["latest_source"] == "state_receipt":
            entry["latest_phase"] = _safe_phase(row["phase"])
            entry["latest_ci_status"] = _safe_ci(row["ci_status"])
            entry["latest_commit"] = _safe_sha(row["commit_sha"])

    for row in events:
        pid = str(row["project_id"]).strip().lower()
        if not PROJECT_RE.fullmatch(pid):
            continue
        category = MEANINGFUL_RUNNER_EVENTS.get(str(row["event"]))
        if not category:
            continue
        state[pid]["counts"][category] += 1
        touch(pid, str(row["ts"]), category)

    projects = []
    for pid, entry in sorted(
        state.items(),
        key=lambda item: (item[1]["latest_at"] or "", item[0]),
        reverse=True,
    )[:MAX_PROJECTS]:
        counts = {key: int(value) for key, value in sorted(entry["counts"].items()) if value}
        projects.append(
            {
                "project_id": pid,
                "latest_at": entry["latest_at"],
                "latest_change": entry["latest_source"],
                "change_count": sum(counts.values()),
                "counts": counts,
                "latest_receipt": {
                    "phase": entry["latest_phase"],
                    "ci_status": entry["latest_ci_status"],
                    "commit": entry["latest_commit"],
                },
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "since": since_iso,
        "project_filter": project_id,
        "project_count": len(projects),
        "truncated": truncated or len(state) > MAX_PROJECTS,
        "source_coverage": {
            "project_state_receipts": True,
            "runner_events": True,
        },
        "privacy_contract": "compact categorical output only",
        "projects": projects,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report compact zCloud changes since a visit timestamp")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "history.db")
    parser.add_argument("--since", required=True, help="Timezone-aware ISO-8601 visit timestamp")
    parser.add_argument("--project")
    parser.add_argument("--row-limit", type=int, default=MAX_ROWS)
    parser.add_argument("--require-changes", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = report(args.db, args.since, args.project, row_limit=args.row_limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    if args.require_changes and not payload["projects"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

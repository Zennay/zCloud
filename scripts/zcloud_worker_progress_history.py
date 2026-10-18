#!/usr/bin/env python3
"""Read-only, privacy-bounded worker progress history for zSentinel.

Browser-supplied events are observations, never proof of an active claim,
independent text generation, source merge or recovered worker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SAFE_EVENTS = frozenset({
    "heartbeat", "prompt-sent", "generation-started", "generation-progress",
    "generation-finished", "runner-started", "runner-paused", "worker-paused",
    "worker-drained", "startup-blocked", "conversation-adopted",
    "target-tab-recovered", "quality-recovery-requested",
})
MAX_ROWS = 40


def _clock(value: object, reference: datetime) -> dict:
    """Return a bounded UTC clock reading without future-time clamping."""
    if not isinstance(value, str) or len(value) > 48:
        return {"status": "invalid", "utc": None, "age_s": None}
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("naive time")
        stamp = stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return {"status": "invalid", "utc": None, "age_s": None}
    age = (reference - stamp).total_seconds()
    if age < -120:
        return {"status": "future", "utc": stamp.isoformat(), "age_s": None}
    return {"status": "observed", "utc": stamp.isoformat(),
            "age_s": max(0, int(age))}


def _item(row: sqlite3.Row | None, reference: datetime) -> dict | None:
    if row is None:
        return None
    kind = row["event"]
    if not isinstance(kind, str) or kind not in SAFE_EVENTS:
        kind = "other_browser_event"
    raw_chars = row["assistant_chars"]
    chars = (raw_chars if type(raw_chars) is int and 0 <= raw_chars <= 2000000
             else None)
    return {
        "id": row["id"], "kind": kind,
        "clock": _clock(row["ts"], reference), "reported_assistant_chars": chars,
    }


def read_worker_progress(db_path: Path, project: str, slot: int,
                         limit: int = 24) -> dict:
    if (not isinstance(project, str) or not PROJECT.fullmatch(project)
            or type(slot) is not int or not 1 <= slot <= 8
            or type(limit) is not int or not 1 <= limit <= MAX_ROWS):
        raise ValueError("invalid worker progress selection")
    reference = datetime.now(timezone.utc)
    result = {
        "schema": "zcloud-worker-progress-readonly-v1",
        "observed_at": reference.isoformat(), "project": project,
        "worker_slot": slot, "available": False,
        "coverage": "unavailable", "latest_event": None,
        "latest_prompt": None, "latest_progress": None,
        "recent_events": [], "progress_since_latest_prompt": False,
        "claim_bound": False, "claim_epoch_verified": False,
        "browser_progress_is_independent_proof": False,
        "worker_resumed_verified": False, "deployment_verified": False,
        "notes": "Browser telemetry only. Cannot prove queue ownership or visible generation.",
    }
    path = Path(db_path)
    # No schema initialization, expiry pruning, transaction or SQLite mutation.
    if path.is_symlink() or not path.is_file():
        return result
    uri = path.absolute().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            query = ("SELECT id,ts,event,assistant_chars FROM runner_events "
                     "WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT ?")
            recent = connection.execute(query, (project, slot, limit)).fetchall()
            progress = connection.execute(
                "SELECT id,ts,event,assistant_chars FROM runner_events "
                "WHERE project_id=? AND worker_slot=? AND event='generation-progress' "
                "ORDER BY id DESC LIMIT 1", (project, slot)).fetchone()
            prompt = connection.execute(
                "SELECT id,ts,event,assistant_chars FROM runner_events "
                "WHERE project_id=? AND worker_slot=? AND event='prompt-sent' "
                "ORDER BY id DESC LIMIT 1", (project, slot)).fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError):
        return result
    latest = _item(recent[0], reference) if recent else None
    current_progress = _item(progress, reference)
    current_prompt = _item(prompt, reference)
    after_prompt = bool(
        current_progress and current_prompt
        and current_progress["id"] > current_prompt["id"]
        and current_progress["clock"]["status"] == "observed"
    )
    result.update({
        "available": True, "coverage": "bounded_observation",
        "latest_event": latest, "latest_progress": current_progress,
        "latest_prompt": current_prompt,
        "recent_events": [_item(row, reference) for row in recent],
        "progress_since_latest_prompt": after_prompt,
        "truncated": len(recent) >= limit,
    })
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only browser progress receipts")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent.parent / "history.db")
    parser.add_argument("--project", required=True)
    parser.add_argument("--slot", required=True, type=int)
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args(argv)
    try:
        data = read_worker_progress(args.db, args.project, args.slot, args.limit)
    except ValueError:
        parser.error("invalid project, worker slot, or bounded limit")
    print(json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if data["available"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only observed browser-worker progress for zSentinel.

Uses existing indexed runner_events metadata, never the live server GET
handlers (which may reconcile/mutate DB state). No target/chat URL, prompt,
title, user message, raw reason, error, or event payload is exported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

UTC = timezone.utc
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
BLOCKED = (
    "assignment-invalid", "send-blocked",
    "assignment-refresh-failed", "thinking-effort-unavailable",
)
SAFE_EVENT = frozenset((
    "heartbeat", "generation-started", "generation-finished",
    "prompt-sent", "runner-paused", "runner-drained", "runner-restarted",
    "generation-progress", "assignment-invalid", "send-blocked",
    "assignment-refresh-failed", "thinking-effort-unavailable",
))
REQUIRED_COLUMNS = frozenset((
    "id", "project_id", "worker_slot", "ts", "event",
    "generating", "sending", "progress_at", "assistant_chars",
))
MAX_PROJECTS = 3
MAX_SLOTS = 8


def _age(value, now):
    if not isinstance(value, str) or len(value) > 55:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    seconds = (now - timestamp.astimezone(UTC)).total_seconds()
    if seconds < -120:
        return None
    return max(0, int(seconds))


def _observe_slot(con, project, slot, now):
    args = (project, slot)
    last = con.execute(
        "SELECT id,ts,event,generating,sending,progress_at,assistant_chars "
        "FROM runner_events WHERE project_id=? AND worker_slot=? "
        "ORDER BY id DESC LIMIT 1", args).fetchone()
    if last is None:
        return None
    last_id, timestamp, event, generating, sending, progress, chars = last
    if type(last_id) is not int or last_id < 1:
        raise ValueError("invalid worker event row identity")
    event_age = _age(timestamp, now)
    if type(generating) is not int or generating not in (0, 1):
        generating = 0
    if type(sending) is not int or sending not in (0, 1):
        sending = 0
    if chars is not None and (type(chars) is not int or not 0 <= chars <= 2000000):
        chars = None
    # A bounded recent slice protects large 14-day histories from a full
    # progress scan. progress_at is browser-reported and may be repeated in
    # heartbeats: it is a signal, NOT proof of useful work or a resumed claim.
    progress_row = con.execute(
        "SELECT progress_at,assistant_chars FROM runner_events "
        "WHERE project_id=? AND worker_slot=? AND id>=? "
        "AND progress_at IS NOT NULL ORDER BY id DESC LIMIT 1",
        (project, slot, max(1, last_id-200))).fetchone()
    if progress_row is not None:
        progress = progress_row[0]
        if chars is None:
            chars = progress_row[1]
    progress_age = _age(progress, now)
    prompt = con.execute(
        "SELECT id,ts FROM runner_events WHERE project_id=? "
        "AND worker_slot=? AND event='prompt-sent' ORDER BY id DESC LIMIT 1",
        args).fetchone()
    blocked = con.execute(
        "SELECT id,ts,event FROM runner_events WHERE project_id=? "
        "AND worker_slot=? AND event IN (?,?,?,?) ORDER BY id DESC LIMIT 1",
        args + BLOCKED).fetchone()
    block_age = _age(blocked[1], now) if blocked is not None else None
    blocked_after_prompt = bool(
        blocked is not None
        and (prompt is None or blocked[0] > prompt[0])
        and block_age is not None and block_age <= 900
    )
    if blocked_after_prompt:
        state = "recent_client_block_event_owner_review"
    elif (generating and event_age is not None and event_age <= 180
          and progress_age is not None and progress_age <= 180):
        state = "generation_progress_timestamp_observed"
    elif generating and event_age is not None and event_age <= 180:
        state = "generating_but_progress_unverified_or_stale"
    elif event_age is None or event_age > 600:
        state = "worker_event_stale_or_unknown"
    elif event == "generation-finished":
        state = "generation_finish_event_observed_not_product_delivery"
    else:
        state = "recent_event_only_no_product_progress_proof"
    return {
        "project_id": project, "worker_slot": slot,
        "last_event_type": event if event in SAFE_EVENT else "other_event",
        "event_age_seconds": event_age,
        "progress_age_seconds": progress_age,
        "assistant_character_count": chars if type(chars) is int and 0 <= chars <= 2000000 else None,
        "browser_generating": bool(generating),
        "browser_sending": bool(sending),
        "recent_block_event_after_last_prompt": blocked_after_prompt,
        "classification": state,
        "event_source": "indexed_local_runner_events_only",
        "owner_claim_verified": False,
        "worker_resumed_verified": False,
        "repair_authorized": False,
    }


def observe_worker_progress(database, projects, *, now=None):
    """One bounded immutable SQLite read; no browser GET, no DB mutations."""
    if (not isinstance(projects, list) or not 1 <= len(projects) <= MAX_PROJECTS
            or len(projects) != len(set(projects))
            or any(type(pid) is not str or not PROJECT.fullmatch(pid)
                   for pid in projects)):
        raise ValueError("select one to three canonical project IDs")
    path = Path(database)
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("existing nonempty worker events database required")
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    entries = []
    missing = []
    truncated = []
    con = sqlite3.connect(uri, uri=True, timeout=1.5, isolation_level=None)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=1500")
        columns = {r[1] for r in con.execute("PRAGMA table_info(runner_events)")}
        if not REQUIRED_COLUMNS.issubset(columns):
            raise ValueError("runner_events source schema absent or incomplete; no migrations")
        indices = {r[1] for r in con.execute("PRAGMA index_list(runner_events)")}
        if not {"runner_events_project_worker_id", "runner_events_project_worker_event_id"}.issubset(indices):
            raise ValueError("indexed read-only worker source unavailable; refusing full-table scan")
        con.execute("BEGIN")
        for pid in projects:
            slots = con.execute(
                "SELECT DISTINCT worker_slot FROM runner_events "
                "INDEXED BY runner_events_project_worker_id "
                "WHERE project_id=? AND worker_slot BETWEEN 1 AND 16 "
                "ORDER BY worker_slot LIMIT ?", (pid, MAX_SLOTS+1)).fetchall()
            if not slots:
                missing.append(pid)
            if len(slots) > MAX_SLOTS:
                truncated.append(pid)
            for (slot,) in slots[:MAX_SLOTS]:
                entry = _observe_slot(con, pid, slot, observed)
                if entry is not None:
                    entries.append(entry)
        con.execute("COMMIT")
    finally:
        con.close()
    entries.sort(key=lambda row: (row["project_id"], row["worker_slot"]))
    return {
        "schema": "zsentinel-worker-progress-observation-v1",
        "observed_at_utc": observed.isoformat(timespec="seconds"),
        "coverage": ("bounded_partial_or_no_events" if missing or truncated
                     else "selected_project_event_slots_only"),
        "source": "existing_sqlite_mode_ro_query_only_indexed_runner_events",
        "selected_projects": list(projects),
        "entries": entries,
        "unobserved_project_ids": missing,
        "truncated_project_ids": truncated,
        "event_inventory_complete": False,
        "live_worker_claims_verified": False,
        "worker_resumed_verified": False,
        "actions_performed": False,
        "database_write_authorized": False,
        "runner_restart_authorized": False,
    }


def operator_text(source):
    if (not isinstance(source, dict)
            or source.get("schema") != "zsentinel-worker-progress-observation-v1"
            or source.get("actions_performed") is not False
            or source.get("worker_resumed_verified") is not False):
        raise ValueError("invalid read-only worker source")
    lines = ["WORKER VOORTGANG · read-only eventbewijs; GEEN bewezen product-/claimhervatting."]
    for row in source["entries"]:
        lines.append(
            f"  {row['project_id']} worker {row['worker_slot']} · {row['classification']}"
            f" · event_age_s={row['event_age_seconds']}"
            f" · progress_age_s={row['progress_age_seconds']}"
            f" · claim=ONBEKEND")
    for pid in source["unobserved_project_ids"]:
        lines.append(f"  {pid} · GEEN eventbron voor gekozen project, geen bewijs van nul workers.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="zSentinel indexed read-only browser worker progress")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        receipt = observe_worker_progress(args.database, args.project)
        print(operator_text(receipt) if args.format == "text"
              else json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, "zSentinel worker source unavailable: " + str(exc)[:180] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

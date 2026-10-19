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


def _observe_slot(con, project, slot, now, desired_state, global_slot):
    args = (project, slot)
    last = con.execute(
        "SELECT id,ts,event,generating,sending,progress_at,assistant_chars "
        "FROM runner_events WHERE project_id=? AND worker_slot=? "
        "ORDER BY id DESC LIMIT 1", args).fetchone()
    if last is None:
        return {
            "project_id": project, "worker_slot": slot,
            "desired_state": desired_state,
            "desired_state_source_observed": desired_state != "unknown",
            "allocated_global_slot": global_slot,
            "last_event_type": None, "event_age_seconds": None,
            "progress_age_seconds": None, "assistant_character_count": None,
            "browser_generating": False, "browser_sending": False,
            "recent_block_event_after_last_prompt": False,
            "classification": (
                "configured_running_no_event_evidence"
                if desired_state == "running" and global_slot is not None
                else "configured_not_allocated_not_a_stalled_worker"
                if global_slot is None and desired_state == "running"
                else "configured_paused_not_incident"
                if desired_state == "paused"
                else "configured_draining_owner_hold"
                if desired_state == "draining"
                else "desired_state_unknown_no_automatic_action"),
            "event_source": "indexed_local_runner_events_only",
            "owner_claim_verified": False, "worker_resumed_verified": False,
            "repair_authorized": False,
        }
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
    if desired_state == "paused":
        state = "configured_paused_not_incident"
    elif desired_state == "draining":
        state = "configured_draining_owner_hold"
    elif desired_state != "running":
        state = "desired_state_unknown_no_automatic_action"
    elif global_slot is None:
        # A lingering running configuration without an assigned global slot
        # is not an active, stalled worker, regardless of old events.
        state = "configured_not_allocated_not_a_stalled_worker"
    elif blocked_after_prompt:
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
        "desired_state": desired_state,
        "desired_state_source_observed": desired_state != "unknown",
        "allocated_global_slot": global_slot,
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
        worker_columns = {r[1] for r in con.execute("PRAGMA table_info(runner_workers)")}
        if not {"project_id", "worker_slot", "desired_state"}.issubset(worker_columns):
            raise ValueError("worker desired_state source missing; cannot classify paused workers")
        con.execute("BEGIN")
        for pid in projects:
            configured = con.execute(
                "SELECT worker_slot,desired_state FROM runner_workers "
                "WHERE project_id=? AND worker_slot BETWEEN 1 AND 16 "
                "ORDER BY worker_slot LIMIT ?", (pid, MAX_SLOTS+1)).fetchall()
            events = con.execute(
                "SELECT DISTINCT worker_slot FROM runner_events "
                "INDEXED BY runner_events_project_worker_id "
                "WHERE project_id=? AND worker_slot BETWEEN 1 AND 16 "
                "ORDER BY worker_slot LIMIT ?", (pid, MAX_SLOTS+1)).fetchall()
            desired = {}
            for slot, value in configured:
                if type(slot) is int and 1 <= slot <= 16:
                    desired[slot] = (value if value in ("running", "paused", "draining")
                                     else "unknown")
            slots = sorted(set(desired) | {
                slot for (slot,) in events if type(slot) is int and 1 <= slot <= 16
            })
            if not slots:
                missing.append(pid)
            if len(slots) > MAX_SLOTS or len(configured) > MAX_SLOTS or len(events) > MAX_SLOTS:
                truncated.append(pid)
            for slot in slots[:MAX_SLOTS]:
                entries.append(_observe_slot(
                    con, pid, slot, observed, desired.get(slot, "unknown")))
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
            f" · desired_state={row['desired_state']}"
            f" · event_age_s={row['event_age_seconds']}"
            f" · progress_age_s={row['progress_age_seconds']}"
            f" · claim=ONBEKEND")
    for pid in source["unobserved_project_ids"]:
        lines.append(f"  {pid} · GEEN eventbron voor gekozen project, geen bewijs van nul workers.")
    return "\n".join(lines)



def worker_progress_html(source):
    """Script-free, bounded view of index-backed event activity, never claims."""
    if (not isinstance(source, dict)
            or source.get("schema") != "zsentinel-worker-progress-observation-v1"
            or source.get("source") != "existing_sqlite_mode_ro_query_only_indexed_runner_events"
            or source.get("actions_performed") is not False
            or source.get("worker_resumed_verified") is not False
            or source.get("live_worker_claims_verified") is not False
            or source.get("database_write_authorized") is not False
            or not isinstance(source.get("entries"), list)
            or len(source["entries"]) > MAX_PROJECTS * MAX_SLOTS):
        raise ValueError("untrusted browser worker event evidence")
    from html import escape
    status = {
        "recent_client_block_event_owner_review": "Recent browserblokkadesignaal; eigenaarcontrole",
        "generation_progress_timestamp_observed": "Recente generatievoortgang waargenomen",
        "generating_but_progress_unverified_or_stale": "Genereren gemeld, werkelijke voortgang onbekend/stale",
        "worker_event_stale_or_unknown": "Geen recent worker-event; geen herstelbewijs",
        "generation_finish_event_observed_not_product_delivery": "Voltooide generatie gemeld; productresultaat onbekend",
        "recent_event_only_no_product_progress_proof": "Recent browser-event; geen productvoortgang bewezen",
        "configured_paused_not_incident": "Opzettelijk gepauzeerde worker; GEEN herstelincident",
        "configured_draining_owner_hold": "Worker wordt door eigenaar gedraineerd; geen automatische herstart",
        "configured_running_no_event_evidence": "Worker staat op running maar heeft geen eventbron; eerst eigendom en start controleren",
        "desired_state_unknown_no_automatic_action": "Gewenste workerstatus onbekend; geen automatische actie",
    }
    rows = []
    for row in source["entries"]:
        pid = row.get("project_id")
        slot = row.get("worker_slot")
        state = row.get("classification")
        desired = row.get("desired_state")
        if (desired not in ("running", "paused", "draining", "unknown")
                or row.get("desired_state_source_observed") is not
                   (desired != "unknown")):
            raise ValueError("invalid worker desired state evidence")
        if (type(pid) is not str or not PROJECT.fullmatch(pid)
                or type(slot) is not int or not 1 <= slot <= 16
                or state not in status
                or row.get("owner_claim_verified") is not False
                or row.get("worker_resumed_verified") is not False
                or row.get("repair_authorized") is not False):
            raise ValueError("invalid browser progress state receipt")
        age = row.get("event_age_seconds")
        progress = row.get("progress_age_seconds")
        if any(value is not None and (type(value) is not int or value < 0)
               for value in (age, progress)):
            raise ValueError("invalid source age")
        rows.append(
            '<article class="panel read"><h3>' + escape(pid) + ' · worker ' +
            str(slot) + '</h3><p>' + escape(status[state]) +
            '</p><p class="small">Gewenste status: ' + escape(desired) +
            ' · Eventleeftijd: ' +
            (str(age) + ' sec' if age is not None else 'ONBEKEND') +
            ' · Laatste bronvoortgang: ' +
            (str(progress) + ' sec' if progress is not None else 'ONBEKEND') +
            ' · actueel claimrecht: ONBEKEND · werkhervatting NIET bewezen.</p>'
            '</article>')
    if not rows:
        rows.append('<p class="empty">Geen eventbron in gekozen projectvenster;'
                    ' dat bewijst GEEN nul actieve workers.</p>')
    return (
        '<section aria-label="Read-only browserworker voortgang">'
        '<h2>Browserworkers · werkelijk eventbewijs</h2>'
        '<p>Lokale geïndexeerde SQLite-read-only bron, geen browserstatus-GET,'
        ' herstart, claim of werkafrondingsbewijs. Stale event != bewezen offline.</p>'
        '<div class="grid">' + ''.join(rows) + '</div></section>'
    )


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

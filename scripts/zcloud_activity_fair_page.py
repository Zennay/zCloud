"""Bounded, fair local-event selector for zCloud issue #1518.

A metadata-only, *source-only* component: deliberately never reads private
legacy event title/detail text. It does not alter server.py, the live SQLite database,
the Activity Feed API, or its serialized integration owner. A page rotates
through allowlisted projects instead of letting 80 events from one noisy
project hide quiet projects. GitHub/PR/CI evidence remains NOT synchronized.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

SLUG = re.compile(r"[a-z][a-z0-9_-]{0,55}\Z")
# Local event IDs serve as keyset cursors and are also returned to the client.
# Refuse freeform/control-character IDs instead of leaking malformed metadata.
EVENT_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,256}\Z")
MAX_PROJECTS = 32
MAX_PAGE = 80
MAX_EVENT_ID = 256
MAX_TS = 64
# Only opaque, known producer kind identifiers may leave the local read model.
# Unknown future/malformed kinds still retain their timestamp and cursor
# position, but arbitrary database text is never rendered in the UI.
EXPOSED_EVENT_KINDS = frozenset({"commit", "milestone", "firefox-recovery", "firefox-crash"})
UNKNOWN_EVENT_KIND = "other_local_event"


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= MAX_TS:
        raise ValueError("invalid event timestamp")
    # zCloud's event writer uses timezone-aware datetime.isoformat().
    # Mixed time zones cannot be keyset-paginated lexically in SQLite.
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid event timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("non-UTC event timestamp")
    if parsed.isoformat() != value:
        raise ValueError("noncanonical event timestamp")
    return parsed


def _key(ts: object, eid: object) -> tuple[str, str]:
    _timestamp(ts)
    if not isinstance(eid, str) or len(eid) > MAX_EVENT_ID or not EVENT_ID.fullmatch(eid):
        raise ValueError("invalid event id")
    return ts, eid


def fair_page(
    connection: sqlite3.Connection,
    projects: list[str],
    *,
    page_size: int = MAX_PAGE,
    cursor: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Read one fair page via index-friendly, bound per-project keyset scans.

    Caller supplies a trusted, canonical project registry and a read-only DB
    connection. Cursor state advances only through *returned* events, and
    cannot skip unreturned events from a quieter project. The response is
    explicitly local-only, never a false claim of GitHub synchronization.

    No attempt is made to mutate, poll or reconcile live systems.
    """
    if (type(page_size) is not int or not 1 <= page_size <= MAX_PAGE
            or not isinstance(projects, list)
            or not 1 <= len(projects) <= MAX_PROJECTS
            or any(not isinstance(p, str) or not SLUG.fullmatch(p) for p in projects)
            or len(set(projects)) != len(projects)):
        raise ValueError("invalid project/page scope")
    if cursor is None:
        cursor = {}
    if not isinstance(cursor, dict) or not set(cursor).issubset(projects):
        raise ValueError("cursor must contain only selected projects")
    positions: dict[str, tuple[str, str]] = {}
    for project, value in cursor.items():
        if not isinstance(value, dict) or set(value) != {"ts", "id"}:
            raise ValueError("invalid project cursor")
        positions[project] = _key(value["ts"], value["id"])

    streams: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        params: list[Any] = [project]
        where = "project=?"
        if project in positions:
            ts, eid = positions[project]
            where += " AND (ts<? OR (ts=? AND id<?))"
            params.extend([ts, ts, eid])
        params.append(page_size + 1)
        # The legacy events table has its own stable event ID. The extra row
        # only detects whether pagination is required and is NEVER returned.
        rows = connection.execute(
            "SELECT id, ts, project, kind FROM events WHERE "
            + where + " ORDER BY ts DESC, id DESC LIMIT ?", params
        ).fetchall()
        clean: list[dict[str, Any]] = []
        for row in rows:
            eid, ts, pid, kind = row
            _key(ts, eid)  # fail closed; malformed source is NOT zero activity
            if pid != project:
                raise ValueError("cross-project event row")
            clean.append({"id": eid, "ts": ts, "project": pid,
                          "kind": kind if type(kind) is str and kind in EXPOSED_EVENT_KINDS
                          else UNKNOWN_EVENT_KIND})
        streams[project] = clean

    # Stable order across repeat reads, independent of caller registry order.
    # Newest project gets first slot, then EVERY nonempty project gets a
    # chance before the busiest can claim another slot.
    project_order = sorted(
        projects,
        key=lambda pid: (
            # With page_size < project_count, cursor-less projects have not
            # appeared in the current browsing session. Give those active
            # projects their first slot before repeating already shown ones.
            bool(streams[pid]) and pid not in positions,
            _timestamp(streams[pid][0]["ts"]) if streams[pid]
            else datetime.min.replace(tzinfo=timezone.utc),
            pid,
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    offsets = {pid: 0 for pid in projects}
    while len(selected) < page_size:
        advanced = False
        for pid in project_order:
            i = offsets[pid]
            if i >= len(streams[pid]) or len(selected) >= page_size:
                continue
            item = streams[pid][i]
            selected.append(item)
            offsets[pid] += 1
            advanced = True
            if len(selected) >= page_size:
                break
        if not advanced:
            break

    next_positions = {pid: {"ts": ts, "id": eid} for pid, (ts, eid) in positions.items()}
    for item in selected:
        next_positions[item["project"]] = {"ts": item["ts"], "id": item["id"]}
    # len(stream) > consumed => known more; len(stream)==page_size+1
    # means the extra row is already included in this check.
    has_more = any(offsets[pid] < len(streams[pid]) for pid in projects)
    return {
        "events": selected,
        "next_cursor": next_positions if has_more else None,
        "coverage": {
            "local_sqlite": "read_only",
            "github_pr_branch_ci": "not_synchronized",
            "feed_complete": False,
            "projects_considered": len(projects),
            "projects_shown_in_page": len({item["project"] for item in selected}),
            # Remaining events in an unvisited project are not a cleared
            # backlog. This captures page-size smaller than project count.
            "projects_not_yet_shown_with_events": sum(
                bool(streams[pid]) and pid not in next_positions
                for pid in projects
            ),
        },
    }

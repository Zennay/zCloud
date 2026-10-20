#!/usr/bin/env python3
"""Private, read-only local zCloud lease visibility for zSentinel operators.

Only counts of observed unexpired task_claims are released. This observer
never derives a free-owner decision from missing local rows: Notion, GitHub
and workers outside this SQLite snapshot may still have active ownership.
No raw owner_id, worker_id, claim_key, metadata or SQLite row leaves the tool.
"""
import datetime as dt
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

MAX_CLAIMS = 1000
MAX_PROJECTS = 3
WRITER_CAPABILITIES = frozenset({"mainwriter", "main-merge"})


def _parse_time(value):
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("malformed lease time")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("untrusted timezone-less lease")
    return stamp.astimezone(dt.timezone.utc)


def _empty(status):
    return {
        "source": "LOCAL_SQLITE_READ_ONLY",
        "status": status,
        "active_local_leases_observed": None,
        "active_local_writers_observed": None,
        "ambiguous_active_leases": None,
        "notion_claim_status": "NOT_OBSERVED",
        "owner_free": "NOT_INFERABLE",
        "authorization": "NOT_GRANTED",
    }


def observe(db_path, projects):
    """Bounded one-transaction snapshot; return source-limited aggregate only."""
    if (not isinstance(projects, (list, tuple)) or not projects
            or len(projects) > MAX_PROJECTS or
            any(not isinstance(p, str) or not p or len(p) > 64 for p in projects)
            or len(set(projects)) != len(projects)):
        raise ValueError("one to three canonical distinct project IDs required")
    result = {project: _empty("LOCAL_SOURCE_UNAVAILABLE") for project in projects}
    con = None
    try:
        path = Path(db_path).expanduser().resolve(strict=True)
        if not path.is_file():
            return result
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro&cache=private"
        con = sqlite3.connect(uri, uri=True, timeout=2)
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=2000")
        con.execute("BEGIN")
        mark = ",".join("?" for _ in projects)
        rows = con.execute(
            "SELECT project_id, lease_until, metadata_json FROM task_claims "
            f"WHERE project_id IN ({mark}) LIMIT {MAX_CLAIMS + 1}",
            tuple(projects)
        ).fetchall()
        con.rollback()
        if len(rows) > MAX_CLAIMS:
            return {p: _empty("CLAIM_INVENTORY_TRUNCATED") for p in projects}
        now = dt.datetime.now(dt.timezone.utc)
        counts = {p: [0, 0, 0] for p in projects}
        for project, lease_until, raw_metadata in rows:
            if project not in counts:
                return {p: _empty("CLAIM_SOURCE_INVALID") for p in projects}
            try:
                expiration = _parse_time(lease_until)
            except (ValueError, TypeError, OverflowError):
                counts[project][2] += 1
                continue
            if expiration <= now:
                continue
            counts[project][0] += 1
            if (not isinstance(raw_metadata, str)
                    or len(raw_metadata) > 65536):
                counts[project][2] += 1
                continue
            try:
                meta = json.loads(raw_metadata)
            except (ValueError, TypeError):
                counts[project][2] += 1
                continue
            if not isinstance(meta, dict):
                counts[project][2] += 1
                continue
            scope = meta.get("conflict_scope")
            if scope is None:
                continue  # regular task claims still count as occupied
            if not isinstance(scope, dict):
                counts[project][2] += 1
                continue
            caps = scope.get("capabilities")
            if (not isinstance(caps, list) or
                    not all(isinstance(cap, str) for cap in caps)):
                counts[project][2] += 1
                continue
            if any(cap in WRITER_CAPABILITIES for cap in caps):
                counts[project][1] += 1
        for project, (active, writers, ambiguous) in counts.items():
            result[project] = {
                **_empty("LOCAL_ACTIVE_CLAIMS_OBSERVED" if active or ambiguous
                         else "NO_ACTIVE_LOCAL_CLAIMS_OBSERVED"),
                "active_local_leases_observed": active,
                "active_local_writers_observed": writers,
                "ambiguous_active_leases": ambiguous,
            }
        return result
    except (sqlite3.Error, ValueError, OSError, TypeError):
        return result  # Notion and other ownership remain unknown, never free
    finally:
        if con is not None:
            con.close()

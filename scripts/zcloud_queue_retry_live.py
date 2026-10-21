"""Read-only, bounded zSentinel retry evidence for the trusted zCloud Activity UI.

No queue admission, lease mutation or implicit refresh. Issue #677 remains a
separate writer-side retry/backoff problem.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3

ACTIVE = ("queued", "claimed", "running", "verifying")
PRIORITY = ("P0", "P1", "P2", "P3")
REQUIRED = {
    "queue_id", "project_id", "priority", "status", "attempts", "eligible",
    "updated_at", "claim_expires",
}


class ObservationUnavailable(ValueError):
    """No trustworthy read-only source; never report an invented zero."""


def snapshot(db_path: Path, canonical_projects: set[str], project_id: str,
             threshold: int = 3, limit: int = 40) -> dict:
    """Read group+sample in one deferred transaction opened without write authority."""
    projects = {
        name for name in canonical_projects
        if isinstance(name, str) and name and len(name) <= 64
    }
    if not projects:
        raise ObservationUnavailable("canonical_projects_missing")
    if project_id != "all" and project_id not in projects:
        raise ObservationUnavailable("unknown_project")
    if not isinstance(threshold, int) or not 1 <= threshold <= 1000:
        raise ObservationUnavailable("invalid_threshold")
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ObservationUnavailable("invalid_limit")
    if not isinstance(db_path, Path) or not db_path.is_file():
        raise ObservationUnavailable("queue_database_missing")
    observed_at = datetime.now(timezone.utc).isoformat()
    uri = db_path.resolve().as_uri().replace("file://", "file:") + "?mode=ro"
    connection = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        connection.execute("BEGIN")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(portfolio_queue)")}
        if not REQUIRED <= columns:
            raise ObservationUnavailable("unknown_queue_schema")
        where = "status IN ('queued','claimed','running','verifying')"
        where_args: list = []
        if project_id != "all":
            where += " AND project_id=?"
            where_args.append(project_id)
        grouped = [dict(row) for row in connection.execute(
            """SELECT project_id,priority,status,COUNT(*) AS items,
                      SUM(CASE WHEN eligible=1 THEN 1 ELSE 0 END) AS eligible_items,
                      SUM(CASE WHEN attempts>=? THEN 1 ELSE 0 END) AS high_attempts,
                      SUM(CASE WHEN attempts>=10 THEN 1 ELSE 0 END) AS ten_plus,
                      MAX(attempts) AS max_attempts,
                      SUM(CASE WHEN status IN ('claimed','running','verifying')
                               AND claim_expires IS NOT NULL AND claim_expires<=?
                          THEN 1 ELSE 0 END) AS expired_lease_rows
               FROM portfolio_queue WHERE """ + where +
            " GROUP BY project_id,priority,status",
            [threshold, observed_at, *where_args],
        )]
        high = [dict(row) for row in connection.execute(
            """SELECT queue_id,project_id,priority,status,attempts,eligible,
                      updated_at,claim_expires
               FROM portfolio_queue WHERE """ + where +
            """ AND attempts>=?
                ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1
                         WHEN 'P2' THEN 2 ELSE 3 END,
                         attempts DESC, updated_at ASC,queue_id ASC
                LIMIT ?""",
            [*where_args, threshold, limit + 1],
        )]
        connection.execute("ROLLBACK")
    except (sqlite3.Error, OSError) as exc:
        raise ObservationUnavailable("queue_read_unavailable") from exc
    finally:
        if connection is not None:
            connection.close()
    for row in grouped + high:
        if row.get("project_id") not in projects or row.get("priority") not in PRIORITY:
            raise ObservationUnavailable("unknown_project_or_priority")
    items = []
    for row in high[:limit]:
        if not isinstance(row["attempts"], int) or row["attempts"] < threshold:
            raise ObservationUnavailable("invalid_attempt_counter")
        raw_id = str(row["queue_id"] or "")
        if not raw_id:
            raise ObservationUnavailable("missing_queue_identity")
        items.append({
            "queue_ref": hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16],
            "project": row["project_id"],
            "priority": row["priority"],
            "status": row["status"],
            "attempts": row["attempts"],
            "eligible": bool(row["eligible"]),
            "updated_at": row["updated_at"],
            "lease_expires_at": row["claim_expires"],
            "operator_action": (
                "Investigate repeat claim/requeue; do not auto-retry."
                if row["status"] == "queued" else
                "Preserve current owner; check lease and worker evidence."
            ),
        })
    groups = [
        {**row, "interpretation": (
            "requires-owner-review: active claim/lease"
            if row["status"] != "queued" else
            "candidate-for-manual-retry-policy-review"
        ) if row["high_attempts"] else "no-high-attempts-observed"}
        for row in grouped
    ]
    groups.sort(key=lambda row: (
        PRIORITY.index(row["priority"]), -row["high_attempts"],
        row["project_id"], row["status"],
    ))
    return {
        "schema": "zsentinel-retry-incident-v1",
        "observed_at_utc": observed_at,
        "scope": {"project": project_id, "minimum_claim_attempts": threshold},
        "coverage": {
            "active_queue_states_only": list(ACTIVE),
            "sample_limit": limit,
            "sample_is_complete": len(high) <= limit,
            "database_read_only": True,
            "safe_to_authorize_retry": False,
            "attempts_count_claims_not_failures": True,
            "requires_release_owner_for_mutations": True,
        },
        "totals": {
            "active_items": sum(row["items"] for row in groups),
            "high_attempt_items": sum(row["high_attempts"] for row in groups),
            "ten_plus_items": sum(row["ten_plus"] for row in groups),
            "expired_active_leases": sum(row["expired_lease_rows"] for row in groups),
            "max_claim_attempts": max((row["max_attempts"] for row in groups), default=0),
        },
        "project_groups": groups,
        "high_attempt_samples": items,
        "operator_note": (
            "Claim attempts are not failures or recovery permission. "
            "Never override active leases or automatic retry/release gates."
        ),
    }

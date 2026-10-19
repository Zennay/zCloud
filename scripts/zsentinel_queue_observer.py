#!/usr/bin/env python3
"""Read-only live queue pressure for zSentinel (#677), without retry authority.

Reads ONE bounded, consistent SQLite snapshot. Attempts count claims, not
failures. No row is updated, dequeued, retried, leased or assigned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_recovery_router import utc_string

ACTIVE = frozenset({"queued", "claimed", "running", "verifying"})
OWNED = frozenset({"claimed", "running", "verifying"})
REQUIRED = frozenset({
    "queue_id", "project_id", "priority", "status", "attempts",
    "eligible", "updated_at", "claim_expires", "title",
})
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_ROWS = 2500
MAX_SAMPLES = 20
MAX_BYTES_PER_TEXT = 180
UTC = timezone.utc


def _read_sqlite(path):
    location = Path(path)
    before = location.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("queue SQLite must be a regular non-symlink file")
    # mode=ro can read an existing WAL but never initialize or migrate DB.
    # Avoid importing server.py, which owns live control-plane initialization.
    uri = location.absolute().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("BEGIN")
        fields = {
            row["name"] for row in connection.execute("PRAGMA table_info(portfolio_queue)")
        }
        if not REQUIRED.issubset(fields):
            raise ValueError("unsupported portfolio_queue schema")
        rows = [dict(row) for row in connection.execute(
            "SELECT queue_id,project_id,priority,status,attempts,eligible,"
            "updated_at,claim_expires,title FROM portfolio_queue "
            "WHERE status IN ('queued','claimed','running','verifying') "
            "LIMIT ?", (MAX_ROWS + 1,)
        )]
        connection.execute("ROLLBACK")
    finally:
        connection.close()
    if len(rows) > MAX_ROWS:
        raise ValueError("active queue exceeds bounded read; coverage unknown")
    return rows


def _stamp(value):
    if not isinstance(value, str) or len(value) > 45:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    except ValueError:
        pass
    return None


def observe(path, now=None):
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must have timezone")
    moment = moment.astimezone(UTC)
    rows = _read_sqlite(path)
    groups = defaultdict(lambda: {
        "queued": 0, "claimed": 0, "running": 0, "verifying": 0,
        "high_claim_attempts": 0, "ten_plus_claim_attempts": 0,
        "expired_owner_lease_rows": 0, "max_claim_attempts": 0,
    })
    samples = []
    totals = {
        "active_items": 0, "queued_items": 0, "owned_items": 0,
        "high_claim_attempt_items": 0, "ten_plus_claim_attempt_items": 0,
        "expired_owner_lease_rows": 0, "invalid_record_rows": 0,
        "max_claim_attempts": 0,
    }
    for row in rows:
        pid, status, priority = row["project_id"], row["status"], row["priority"]
        attempts, eligible = row["attempts"], row["eligible"]
        if (not isinstance(pid, str) or not PROJECT.fullmatch(pid)
                or status not in ACTIVE or priority not in PRIORITY
                or type(attempts) is not int or attempts < 0 or attempts > 10**9
                or eligible not in (0, 1)
                or not isinstance(row["queue_id"], str)
                or not 0 < len(row["queue_id"]) <= 250):
            totals["invalid_record_rows"] += 1
            continue
        bucket = groups[(pid, priority)]
        bucket[status] += 1
        totals["active_items"] += 1
        totals["queued_items" if status == "queued" else "owned_items"] += 1
        totals["max_claim_attempts"] = max(totals["max_claim_attempts"], attempts)
        bucket["max_claim_attempts"] = max(bucket["max_claim_attempts"], attempts)
        high = attempts >= 3
        ten_plus = attempts >= 10
        if high:
            totals["high_claim_attempt_items"] += 1
            bucket["high_claim_attempts"] += 1
        if ten_plus:
            totals["ten_plus_claim_attempt_items"] += 1
            bucket["ten_plus_claim_attempts"] += 1
        expiry = _stamp(row["claim_expires"]) if row["claim_expires"] else None
        expired = status in OWNED and expiry is not None and expiry <= moment
        if expired:
            totals["expired_owner_lease_rows"] += 1
            bucket["expired_owner_lease_rows"] += 1
        # A subset of rows is visible to the operator, but raw queue_id,
        # freeform criteria, worker names and claim tokens never leave SQLite.
        if high or expired:
            title = row["title"]
            safe_title = (" ".join(title.split())[:MAX_BYTES_PER_TEXT]
                          if isinstance(title, str) else "Unlabeled queue item")
            samples.append({
                "queue_ref": hashlib.sha256(row["queue_id"].encode()).hexdigest()[:16],
                "project_id": pid, "priority": priority, "status": status,
                "title": safe_title,
                "claim_attempts": attempts,
                "owner_lease_expired_observed": expired,
                "action": (
                    "Request authorized lease review; do not remove an active claim."
                    if expired else
                    "Inspect repeated claim churn; attempts are NOT failures, no auto-retry."
                ),
            })
    samples.sort(key=lambda row: (
        0 if row["owner_lease_expired_observed"] else 1,
        -row["claim_attempts"], PRIORITY[row["priority"]],
        row["project_id"], row["queue_ref"],
    ))
    table = [
        {"project_id": project, "priority": priority, **values}
        for (project, priority), values in groups.items()
    ]
    table.sort(key=lambda r: (PRIORITY[r["priority"]], -r["high_claim_attempts"], r["project_id"]))
    return {
        "schema": "zsentinel-queue-pressure-v1",
        "observed_at_utc": utc_string(moment),
        "source": "local_sqlite_transaction_query_only",
        "database_read_only": True,
        "sample_is_complete": len(samples) <= MAX_SAMPLES,
        "queue_inventory_complete": totals["invalid_record_rows"] == 0,
        "claim_ownership_verified": False,
        "automatic_retry_authorized": False,
        "queue_write_authorized": False,
        "attempts_are_claims_not_failures": True,
        "totals": totals,
        "project_groups": table,
        "high_churn_samples": samples[:MAX_SAMPLES],
    }



def explicitly_unobserved_queue():
    """No queue database was supplied: preserve UNKNOWN, never fabricate zero.

    Used only by the opt-in GitHub-only operator frontend when the active
    SQLite queue cannot be reached. A caller must not reinterpret this as
    the result of an actual mode=ro SQLite transaction or file lease check.
    """
    return {
        "schema": "zsentinel-queue-pressure-v1",
        "source": "explicitly_unobserved_no_database",
        "database_read_only": False,
        "sample_is_complete": False,
        "queue_inventory_complete": False,
        "claim_ownership_verified": False,
        "automatic_retry_authorized": False,
        "queue_write_authorized": False,
        "attempts_are_claims_not_failures": True,
        "totals": {},
        "project_groups": [],
        "high_churn_samples": [],
        "queue_source_available": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only queue retry/lease observation")
    parser.add_argument("--database", required=True, type=Path,
                        help="existing portfolio queue SQLite; never created")
    args = parser.parse_args()
    try:
        print(json.dumps(observe(args.database), ensure_ascii=True, sort_keys=True))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"zSentinel queue observation unavailable: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

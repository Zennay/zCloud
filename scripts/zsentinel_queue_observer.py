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
    "eligible", "updated_at", "claimed_at", "claim_expires", "title",
})
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_ROWS = 2500
MAX_SAMPLES = 20
MAX_BLOCKED_GROUPS = 250
MAX_BLOCKED_WORK_ITEMS = 24
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
            "updated_at,claimed_at,claim_expires,title FROM portfolio_queue "
            "WHERE status IN ('queued','claimed','running','verifying') "
            "LIMIT ?", (MAX_ROWS + 1,)
        )]
        # Blocked work is a separate category, never folded into active or
        # queued counts. Only aggregate project/priority totals are retained.
        blocked_total = connection.execute(
            "SELECT COUNT(*) AS items FROM portfolio_queue WHERE status='blocked'"
        ).fetchone()["items"]
        blocked_groups = [dict(row) for row in connection.execute(
            "SELECT project_id,priority,COUNT(*) AS items FROM portfolio_queue "
            "WHERE status='blocked' GROUP BY project_id,priority "
            "ORDER BY project_id,priority LIMIT ?", (MAX_BLOCKED_GROUPS + 1,)
        )]
        # Human-only P0/P1-first shortlist. Do NOT ORDER BY across a
        # potentially huge blocked queue: SQLite could spill its temp sort
        # onto the already pressured VPS root mount despite query_only=ON.
        # Four individually LIMIT-ed priority lookups never require temp-sort.
        blocked_work = []
        for priority in ("P0", "P1", "P2", "P3"):
            remaining = MAX_BLOCKED_WORK_ITEMS - len(blocked_work)
            if remaining <= 0:
                break
            blocked_work.extend(dict(row) for row in connection.execute(
                "SELECT queue_id,project_id,priority,attempts,eligible,"
                "updated_at,claimed_at,claim_expires,title FROM portfolio_queue "
                "WHERE status='blocked' AND priority=? LIMIT ?",
                (priority, remaining)
            ))
        connection.execute("ROLLBACK")
    finally:
        connection.close()
    if len(rows) > MAX_ROWS:
        raise ValueError("active queue exceeds bounded read; coverage unknown")
    after = location.lstat()
    if (not stat.S_ISREG(after.st_mode) or
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)):
        raise ValueError("queue source identity changed during read")
    # Opaque comparison key. The resolved/private database path and inode
    # never appear in human-readable reports or any public HTML.
    source_key = hashlib.sha256(
        (os.uname().nodename + "\\0" + str(location.absolute()) + "\\0" +
         str(before.st_dev) + "\\0" + str(before.st_ino)).encode("utf-8")
    ).hexdigest()
    return rows, source_key, {"total_items": blocked_total,
                              "groups": blocked_groups, "work": blocked_work}


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


def _blocked_queue_observation(raw, observed_at):
    total, rows = raw.get("total_items"), raw.get("groups")
    if (type(total) is not int or not 0 <= total <= 10**9
            or type(rows) is not list or len(rows) > MAX_BLOCKED_GROUPS + 1):
        raise ValueError("invalid bounded blocked-queue aggregate")
    truncated = len(rows) > MAX_BLOCKED_GROUPS
    groups = []
    invalid = 0
    for row in rows[:MAX_BLOCKED_GROUPS]:
        project, priority, items = (
            row.get("project_id"), row.get("priority"), row.get("items"))
        if (type(project) is not str or not PROJECT.fullmatch(project)
                or priority not in PRIORITY
                or type(items) is not int or not 1 <= items <= 10**9):
            invalid += 1
            continue
        groups.append({"project_id": project, "priority": priority, "items": items})
    complete = (not truncated and invalid == 0
                and sum(row["items"] for row in groups) == total)
    work_source = raw.get("work")
    if type(work_source) is not list or len(work_source) > MAX_BLOCKED_WORK_ITEMS + 1:
        raise ValueError("invalid bounded blocked-work source")
    work = []
    seen_refs = set()
    invalid_work = 0
    for row in work_source[:MAX_BLOCKED_WORK_ITEMS]:
        if type(row) is not dict:
            invalid_work += 1
            continue
        pid, priority, attempts = row.get("project_id"), row.get("priority"), row.get("attempts")
        queue_id, title = row.get("queue_id"), row.get("title")
        eligible = row.get("eligible")
        if (type(pid) is not str or not PROJECT.fullmatch(pid)
                or priority not in PRIORITY
                or type(queue_id) is not str or not 0 < len(queue_id) <= 250
                or type(attempts) is not int or not 0 <= attempts <= 10**9
                or eligible not in (0, 1)):
            invalid_work += 1
            continue
        queue_ref = hashlib.sha256(queue_id.encode("utf-8")).hexdigest()[:16]
        if queue_ref in seen_refs:
            raise ValueError("repeated blocked-queue reference")
        seen_refs.add(queue_ref)
        title_text = (" ".join(title.split())[:MAX_BYTES_PER_TEXT]
                      if type(title) is str else "Unlabeled queue item")
        # Nonempty claim fields mean a blocked task is NOT automatically safe
        # to resume. Preserve the warning without copying owner identifiers.
        claim_fields_present = any(row.get(key) is not None for key in
                                   ("claimed_at", "claim_expires"))
        work.append({
            "queue_ref": queue_ref, "project_id": pid, "priority": priority,
            "title": title_text, "claim_attempts": attempts,
            "eligible_observed": bool(eligible),
            "claim_fields_present_observed": claim_fields_present,
            "current_owner_verified": False,
            "resume_authorized": False, "automatic_retry_authorized": False,
            "human_next_step": (
                "Blocked task has claim metadata: verify with runtime owner; do not resume or drop."
                if claim_fields_present else
                "Inspect current owner, exact queue status and dependency before authorized resume."),
        })
    work_truncated = total > len(work_source)
    # 0 blocked rows can only be called fully listed if aggregate coverage
    # was complete. A limited top-N sample never proves other rows are absent.
    work_complete = (not work_truncated and invalid_work == 0
                     and complete and len(work) == total)
    return {
        "schema": "zsentinel-blocked-queue-observation-v1",
        "status_filter": "blocked",
        "coverage": "complete" if complete else "partial",
        "observed_at_utc": observed_at,
        "total_items_observed": total,
        "project_groups": groups,
        "work_items": work,
        "work_items_coverage": "complete" if work_complete else "partial",
        "work_items_truncated": work_truncated or invalid_work > 0,
        "invalid_work_rows": invalid_work,
        "sample_truncated": truncated or invalid > 0,
        "invalid_group_rows": invalid,
        "claim_ownership_verified": False,
        "queue_write_authorized": False,
        "automatic_retry_authorized": False,
    }


def observe(path, now=None):
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must have timezone")
    moment = moment.astimezone(UTC)
    rows, source_key, blocked_raw = _read_sqlite(path)
    groups = defaultdict(lambda: {
        "queued": 0, "claimed": 0, "running": 0, "verifying": 0,
        "high_claim_attempts": 0, "ten_plus_claim_attempts": 0,
        "expired_owner_lease_rows": 0, "unverifiable_owner_lease_rows": 0,
        "inconsistent_owner_lease_window_rows": 0,
        "max_claim_attempts": 0,
    })
    samples = []
    totals = {
        "active_items": 0, "queued_items": 0, "owned_items": 0,
        "high_claim_attempt_items": 0, "ten_plus_claim_attempt_items": 0,
        "expired_owner_lease_rows": 0, "unverifiable_owner_lease_rows": 0,
        "inconsistent_owner_lease_window_rows": 0,
        "invalid_record_rows": 0,
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
        claimed = _stamp(row["claimed_at"]) if row["claimed_at"] else None
        # An expiry alone does not certify an active lease. Never recycle an
        # existing worker claim whose start is absent, future or reversed.
        inconsistent = (status in OWNED and expiry is not None and
                        (claimed is None or claimed > moment or claimed >= expiry))
        expired = (status in OWNED and not inconsistent and
                   expiry is not None and expiry <= moment)
        # These cases are disjoint, with uncertain source ahead of expiration.
        unverifiable = status in OWNED and expiry is None
        if expired:
            totals["expired_owner_lease_rows"] += 1
            bucket["expired_owner_lease_rows"] += 1
        if unverifiable:
            totals["unverifiable_owner_lease_rows"] += 1
            bucket["unverifiable_owner_lease_rows"] += 1
        if inconsistent:
            totals["inconsistent_owner_lease_window_rows"] += 1
            bucket["inconsistent_owner_lease_window_rows"] += 1
        # A subset of rows is visible to the operator, but raw queue_id,
        # freeform criteria, worker names and claim tokens never leave SQLite.
        if high or expired or unverifiable or inconsistent:
            title = row["title"]
            safe_title = (" ".join(title.split())[:MAX_BYTES_PER_TEXT]
                          if isinstance(title, str) else "Unlabeled queue item")
            samples.append({
                "queue_ref": hashlib.sha256(row["queue_id"].encode()).hexdigest()[:16],
                "project_id": pid, "priority": priority, "status": status,
                "title": safe_title,
                "claim_attempts": attempts,
                "owner_lease_expired_observed": expired,
                "owner_lease_unverifiable_observed": unverifiable,
                "owner_lease_window_inconsistent_observed": inconsistent,
                "action": (
                    "Request authorized lease review; do not remove an active claim."
                    if expired else
                    "Claim start or expiry window is inconsistent: ask owner to verify both times; never reclaim automatically."
                    if inconsistent else
                    "Lease clock missing or invalid: ask owner to verify claim; never release automatically."
                    if unverifiable else
                    "Inspect repeated claim churn; attempts are NOT failures, no auto-retry."
                ),
            })
    samples.sort(key=lambda row: (
        0 if row["owner_lease_window_inconsistent_observed"] else
        1 if row["owner_lease_expired_observed"] else
        2 if row["owner_lease_unverifiable_observed"] else 3,
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
        "source_file_identity_sha256_private": source_key,
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
        "blocked_queue": _blocked_queue_observation(blocked_raw, utc_string(moment)),
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
        "blocked_queue": {
            "schema": "zsentinel-blocked-queue-observation-v1",
            "status_filter": "blocked",
            "coverage": "not_observed",
            "observed_at_utc": None,
            "total_items_observed": None,
            "project_groups": [],
            "work_items": [], "work_items_coverage": "not_observed",
            "work_items_truncated": True, "invalid_work_rows": None,
            "sample_truncated": True,
            "invalid_group_rows": None,
            "claim_ownership_verified": False,
            "queue_write_authorized": False,
            "automatic_retry_authorized": False,
        },
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

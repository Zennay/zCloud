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
from datetime import datetime, timedelta, timezone
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
CANONICAL_WORK_LINK = re.compile(
    r"^https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/(?:issues|pull)/[1-9][0-9]{0,8}$"
)
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
        # Optional schema coverage: a live worker slot is a claim even when
        # both claim timestamps are absent. Never publish the slot number.
        worker_slot_covered = "worker_slot" in fields
        source_link_column = "source_url" if "source_url" in fields else "NULL"
        worker_slot_predicate = (
            "worker_slot IS NOT NULL" if worker_slot_covered else "NULL")
        # Portfolio-fair blocked task shortlist: one high-volume project
        # must not hide another project's P0 tasks. Reuse the bounded
        # project/priority aggregates already read in this same SQLite
        # snapshot, and interleave each project's existing row order.
        #
        # At most 24 scoped LIMIT queries in total and 24*24 candidate rows.
        # ORDER BY rowid uses the table's natural rowid b-tree, making each
        # unchanged project's sample stable without a temp sort or global sort.
        # There is NO new unbounded global ORDER BY/temp-sort on the VPS root.
        # With >250 project groups the source stays explicitly partial;
        # absent projects must never be reported as healthy.
        blocked_work = []
        valid_groups = {
            (row["project_id"], row["priority"])
            for row in blocked_groups[:MAX_BLOCKED_GROUPS]
            if type(row.get("project_id")) is str
            and PROJECT.fullmatch(row["project_id"])
            and row.get("priority") in PRIORITY
            and type(row.get("items")) is int and row["items"] > 0
        }
        remaining_query_budget = MAX_BLOCKED_WORK_ITEMS
        for priority in ("P0", "P1", "P2", "P3"):
            remaining = MAX_BLOCKED_WORK_ITEMS - len(blocked_work)
            if remaining == 0 or remaining_query_budget == 0:
                break
            projects = sorted(project for project, rank in valid_groups
                              if rank == priority)[:min(remaining, remaining_query_budget)]
            candidates = []
            for project in projects:
                candidates.append([dict(row) for row in connection.execute(
                    "SELECT queue_id,project_id,priority,attempts,eligible,"
                    "updated_at,claimed_at,claim_expires,title," +
                    worker_slot_predicate + " AS worker_slot_present," +
                    source_link_column + " AS source_url_observed FROM portfolio_queue "
                    "WHERE status='blocked' AND priority=? AND project_id=? "
                    "ORDER BY rowid LIMIT ?",
                    (priority, project, remaining)
                )])
                remaining_query_budget -= 1
            # Round robin, without conflating alphabetical project order with
            # global task priority; no queue row is claimed or reordered.
            for index in range(remaining):
                for batch in candidates:
                    if index < len(batch):
                        blocked_work.append(batch[index])
                        if len(blocked_work) == MAX_BLOCKED_WORK_ITEMS:
                            break
                if len(blocked_work) == MAX_BLOCKED_WORK_ITEMS:
                    break
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
                              "groups": blocked_groups, "work": blocked_work,
                              "worker_slot_source_covered": worker_slot_covered}


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
    worker_slot_covered = raw.get("worker_slot_source_covered")
    if type(worker_slot_covered) is not bool:
        raise ValueError("worker slot source coverage missing")
    if type(work_source) is not list or len(work_source) > MAX_BLOCKED_WORK_ITEMS + 1:
        raise ValueError("invalid bounded blocked-work source")
    observation_stamp = _stamp(observed_at)
    if observation_stamp is None:
        raise ValueError("blocked-queue observation timestamp is invalid")
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
        # The original row clock is useful owner context, but malformed or
        # future-dated values must stay unknown instead of looking fresh.
        updated_stamp = _stamp(row.get("updated_at"))
        updated_at = (updated_stamp.isoformat().replace("+00:00", "Z")
                      if updated_stamp is not None else None)
        updated_future = (updated_stamp is not None
                          and updated_stamp > observation_stamp)
        updated_clock_anomaly = (updated_stamp is not None
                                 and updated_stamp > observation_stamp + timedelta(minutes=2))
        age_seconds = (
            int((observation_stamp - updated_stamp).total_seconds())
            if updated_stamp is not None and not updated_future else None
        )
        # An operator may navigate to a canonical GitHub issue/PR, but
        # a queue-supplied link is unverified text, NOT proof of ownership,
        # issue status, successful fix or permission to edit.
        source_url = row.get("source_url_observed")
        safe_source_url = (
            source_url if type(source_url) is str
            and len(source_url) <= 220
            and CANONICAL_WORK_LINK.fullmatch(source_url) else None)
        # Nonempty claim fields mean a blocked task is NOT automatically safe
        # to resume. Preserve the warning without copying owner identifiers.
        worker_slot_present = row.get("worker_slot_present")
        if worker_slot_covered and worker_slot_present not in (0, 1):
            raise ValueError("malformed worker-slot presence observation")
        if not worker_slot_covered and worker_slot_present is not None:
            raise ValueError("unavailable worker slot cannot imply assignment")
        claim_fields_present = (
            any(row.get(key) is not None for key in ("claimed_at", "claim_expires"))
            or worker_slot_present == 1)
        work.append({
            "queue_ref": queue_ref, "project_id": pid, "priority": priority,
            "title": title_text, "claim_attempts": attempts,
            "updated_at_observed_utc": updated_at,
            "age_seconds_at_observation": age_seconds,
            "updated_at_future_observed": updated_future,
            "updated_at_clock_anomaly_observed": updated_clock_anomaly,
            "canonical_github_source_link_unverified": safe_source_url,
            "eligible_observed": bool(eligible),
            "claim_fields_present_observed": claim_fields_present,
            "worker_slot_present_observed": (
                bool(worker_slot_present) if worker_slot_covered else None),
            "worker_slot_source_covered": worker_slot_covered,
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
        "worker_slot_source_covered": worker_slot_covered,
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
            "worker_slot_source_covered": False,
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

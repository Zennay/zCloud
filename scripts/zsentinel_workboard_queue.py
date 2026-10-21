#!/usr/bin/env python3
"""Attach read-only zCloud queue pressure to an offline zSentinel workboard.

Consumes the existing portfolio_overview JSON export. This is a report of
observed local queue rows, not permission to claim, release or retry anything.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import observed_time
from zsentinel_recovery_router import read_json

MAX_BYTES = 1024 * 1024
REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
ROUTE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
COUNTS = ("queued", "owned", "high_claim_attempts", "expired_owner_leases",
          "unverifiable_owner_leases")
COVERAGE = ("complete_active_rows", "partial_unreliable")
EXPECTED_UNKNOWN = (None,)


def read_queue_overview(path: Path):
    """Admit a single stable, bounded JSON inode for the operator workboard.

    The historical path.stat() followed by read_text() could open a different
    replacement inode, or block on a FIFO swapped into place after the check.
    Reuse the recovery router's no-follow, nonblocking, same-inode reader.
    The read stays local and grants no claim/merge/recovery authority.
    """
    if not isinstance(path, Path):
        raise ValueError("existing queue overview Path required")
    return read_json(path, MAX_BYTES, "queue overview")


def attach_queue_pressure(board, source, canonical, now=None):
    if (not isinstance(board, dict)
            or board.get("schema") not in (
                "zsentinel-issue-delivery-board-v1",
                "zsentinel-portfolio-workboard-v1")
            or board.get("writes_performed") is not False
            or board.get("claims_taken") is not False
            or board.get("merge_authorized") is not False
            or type(board.get("projects")) is not list
            or not isinstance(canonical, dict)
            or not isinstance(source, dict)
            or type(source.get("portfolio")) is not dict):
        raise ValueError("untrusted issue/queue data")
    queue = source["portfolio"]
    if (queue.get("schema") != "zsentinel-portfolio-overview-v1"
            or queue.get("queue_owner_authorized") is not False
            or queue.get("actions_performed") is not False
            or queue.get("runner_recovered") is not False
            or queue.get("deployment_verified") is not False
            or type(queue.get("queue_inventory_complete")) is not bool
            or type(queue.get("rows")) is not list
            or not 1 <= len(queue["rows"]) <= 18):
        raise ValueError("queue source cannot grant project work")
    clock = observed_time(queue.get("observed_at_utc"))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("queue clock must be timezone aware")
    now = now.astimezone(timezone.utc)
    if clock > now + timedelta(minutes=2) or clock < now - timedelta(days=30):
        raise ValueError("queue observation is not within accepted historical bounds")
    age = int(max(0, (now - clock).total_seconds()))
    complete = queue["queue_inventory_complete"]
    rows = {}
    for entry in queue["rows"]:
        if (not isinstance(entry, dict)
                or entry.get("project_id") not in canonical
                or entry["project_id"] in rows
                or entry.get("github_repo") != canonical[entry["project_id"]]
                or entry.get("worker_ownership_verified") is not False
                or entry.get("recovery_proven") is not False
                or entry.get("queue_observation") not in COVERAGE
                or type(entry.get("next_action")) is not str
                or not ROUTE.fullmatch(entry["next_action"])):
            raise ValueError("invalid source-bound project queue row")
        if (complete and entry["queue_observation"] != "complete_active_rows"
                or not complete and entry["queue_observation"] != "partial_unreliable"):
            raise ValueError("inconsistent queue inventory coverage")
        counters = {}
        for name in COUNTS:
            value = entry.get(name)
            if complete:
                if type(value) is not int or not 0 <= value <= 2500:
                    raise ValueError("untrusted complete queue count")
            elif value is not None:
                raise ValueError("incomplete queue cannot assert numeric counts")
            counters[name] = value
        rows[entry["project_id"]] = {
            "coverage": entry["queue_observation"],
            **counters,
            "owner_next_action": entry["next_action"],
            "file_scope_verified": False,
            "ownership_verified": False,
            "can_release_lease": False,
            "can_claim_task": False,
            "can_retry_job": False,
        }
    if set(rows) != set(canonical):
        raise ValueError("queue summary lacks canonical registered project coverage")
    chosen = {}
    for pid in board["projects"]:
        if pid not in rows:
            raise ValueError("unregistered project in workboard")
        chosen[pid] = rows[pid]
    board["queue_pressure_observation"] = {
        "source": "existing_readonly_portfolio_overview",
        "observed_at_utc": clock.isoformat(),
        "age_seconds": age,
        "stale_after_10_minutes": age > 600,
        "queue_inventory_complete": complete,
        "projects": chosen,
        "owned_worker_identity_verified": False,
        "can_claim_task": False,
        "can_release_lease": False,
        "can_retry_job": False,
        "writes_performed": False,
    }
    # Optional source metadata only: users still have to inspect current leases
    # and PR/file overlap even when a project has observed "owned=0".
    for item in board["items"]:
        info = chosen[item["project_id"]]
        item["queue_pressure"] = {
            "observed_owned_min": info["owned"],
            "observed_queued": info["queued"],
            "observed_expired_lease_rows": info["expired_owner_leases"],
            "observed_unverifiable_lease_rows": info["unverifiable_owner_leases"],
            "observed_high_claim_attempt_rows": info["high_claim_attempts"],
            "source_stale": age > 600,
            "source_complete": complete,
            "safe_to_claim": False,
        }
    return board

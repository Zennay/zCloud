#!/usr/bin/env python3
"""zSentinel historical queue-pressure comparisons from saved router decisions.

Offline observation only. Claim attempts are not failed retries; decreasing
counts or missing partial source cannot authorize lease release or recovery.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

FIELDS = ("active_items", "queued_items", "owned_items",
          "high_claim_attempt_items", "ten_plus_claim_attempt_items",
          "expired_owner_lease_rows", "unverifiable_owner_lease_rows",
          "invalid_record_rows", "max_claim_attempts")
MAX_VALUE = 10**9
GROUP_FIELDS = ("queued", "claimed", "running", "verifying",
                "high_claim_attempts", "ten_plus_claim_attempts",
                "expired_owner_lease_rows", "unverifiable_owner_lease_rows",
                "max_claim_attempts")
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
PRIORITIES = ("P0", "P1", "P2", "P3")


def _moment(raw):
    if type(raw) is not str or not 1 <= len(raw) <= 45:
        raise ValueError("queue source lacks bounded UTC timestamp")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("queue source has invalid timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("queue source timestamp lacks timezone")
    return dt.astimezone(timezone.utc)


def observed_queue(decision, source_time):
    """Retain only aggregate, non-authorizing source-bound observations."""
    queue = decision.get("live_queue_pressure")
    if type(queue) is not dict:
        raise ValueError("missing canonical queue observation")
    coverage = queue.get("coverage")
    if coverage not in ("not_supplied", "current", "stale", "incomplete"):
        raise ValueError("unknown queue source coverage")
    if queue.get("claim_ownership_verified") is not False:
        raise ValueError("queue history refuses claimed ownership authority")
    groups = queue.get("project_groups")
    if type(groups) is not list or len(groups) > 18:
        raise ValueError("unbounded queue project groups")
    totals = queue.get("totals")
    if type(totals) is not dict:
        raise ValueError("queue totals unavailable")
    if coverage == "not_supplied":
        if totals or groups:
            raise ValueError("unobserved queue cannot contain counts")
        return {
            "coverage": coverage, "sampled_at_utc": None,
            "totals": None, "sample_is_complete": False,
            "project_groups_complete": False, "project_groups": [],
            "claim_authorized": False,
        }
    sampled = _moment(queue.get("observed_at"))
    if sampled > source_time + timedelta(minutes=2):
        raise ValueError("queue source later than its saved router decision")
    clean = {}
    for field in FIELDS:
        value = totals.get(field)
        if type(value) is not int or not 0 <= value <= MAX_VALUE:
            raise ValueError("invalid queue aggregate " + field)
        clean[field] = value
    if (clean["queued_items"] + clean["owned_items"] != clean["active_items"]
            or clean["ten_plus_claim_attempt_items"] > clean["high_claim_attempt_items"]
            or clean["high_claim_attempt_items"] > clean["active_items"]
            or clean["expired_owner_lease_rows"] +
                clean["unverifiable_owner_lease_rows"] > clean["owned_items"]):
        raise ValueError("inconsistent saved queue aggregates")
    source_fresh_when_saved = (
        coverage == "current"
        and -timedelta(minutes=2) <= source_time - sampled <= timedelta(minutes=5))
    if coverage == "current" and not source_fresh_when_saved:
        raise ValueError("queue source incorrectly labelled current")
    sample_complete = queue.get("sample_is_complete")
    truncated = queue.get("project_groups_truncated")
    if type(sample_complete) is not bool or type(truncated) is not bool:
        raise ValueError("ambiguous queue source coverage flags")
    if coverage != "current" and groups:
        raise ValueError("noncurrent queue source cannot contain project groups")
    clean_groups = []
    seen_groups = set()
    for entry in groups:
        if type(entry) is not dict:
            raise ValueError("invalid historical queue group")
        pid, priority = entry.get("project_id"), entry.get("priority")
        if (type(pid) is not str or not PROJECT_ID.fullmatch(pid)
                or priority not in PRIORITIES or (pid, priority) in seen_groups):
            raise ValueError("invalid or duplicated queue group identity")
        seen_groups.add((pid, priority))
        counts = {}
        for field in GROUP_FIELDS:
            value = entry.get(field)
            if type(value) is not int or not 0 <= value <= MAX_VALUE:
                raise ValueError("invalid queue project aggregate")
            counts[field] = value
        active = sum(counts[f] for f in ("queued", "claimed", "running", "verifying"))
        if (counts["high_claim_attempts"] > active
                or counts["ten_plus_claim_attempts"] > counts["high_claim_attempts"]
                or counts["expired_owner_lease_rows"] +
                    counts["unverifiable_owner_lease_rows"] >
                    active - counts["queued"]):
            raise ValueError("incoherent historical queue group")
        clean_groups.append({"project_id": pid, "priority": priority, **counts})
    return {
        "coverage": coverage,
        "sampled_at_utc": sampled.isoformat(),
        "totals": clean,
        "sample_is_complete": sample_complete,
        "project_groups_complete": not truncated and coverage == "current",
        "project_groups": clean_groups,
        "source_fresh_when_saved": source_fresh_when_saved,
        "claim_authorized": False,
    }


def summarize_queue_windows(source_windows):
    """Compare only ADJACENT fully observed aggregate snapshots.

    Unknown snapshots break continuity; trends never imply repaired tasks.
    """
    if type(source_windows) is not list or not 2 <= len(source_windows) <= 8:
        raise ValueError("queue history needs two through eight source windows")
    comparisons = []
    for before, after in zip(source_windows, source_windows[1:]):
        left, right = before["queue_pressure"], after["queue_pressure"]
        comparable = (left["coverage"] == "current"
                      and right["coverage"] == "current"
                      and left.get("source_fresh_when_saved") is True
                      and right.get("source_fresh_when_saved") is True)
        if not comparable:
            comparisons.append({
                "from_window": before["index"], "to_window": after["index"],
                "comparison": "UNKNOWN_source_gap",
                "deltas": None, "project_deltas": None, "claim_authorized": False,
                "owner_review_required": True,
            })
            continue
        differences = {
            field: right["totals"][field] - left["totals"][field]
            for field in FIELDS
        }
        # Project evidence is independently complete only when the original
        # canonical report did not truncate any project+priority group.
        # An absent group in a COMPLETE sample means no active tasks there,
        # but still does not prove a previously observed task was resolved.
        project_deltas = None
        if left["project_groups_complete"] and right["project_groups_complete"]:
            left_groups = {
                (g["project_id"], g["priority"]): g for g in left["project_groups"]
            }
            right_groups = {
                (g["project_id"], g["priority"]): g for g in right["project_groups"]
            }
            project_deltas = []
            for pid, priority in sorted(
                    set(left_groups) | set(right_groups),
                    key=lambda key: (PRIORITIES.index(key[1]), key[0])):
                old, current = left_groups.get((pid, priority), {}), right_groups.get((pid, priority), {})
                project_deltas.append({
                    "project_id": pid,
                    "priority": priority,
                    "queued_delta": current.get("queued", 0) - old.get("queued", 0),
                    "high_claim_attempts_delta": (
                        current.get("high_claim_attempts", 0)
                        - old.get("high_claim_attempts", 0)),
                    "expired_owner_lease_rows_delta": (
                        current.get("expired_owner_lease_rows", 0)
                        - old.get("expired_owner_lease_rows", 0)),
                    "task_recovery_verified": False,
                    "owner_claim_verified": False,
                })
        comparisons.append({
            "from_window": before["index"], "to_window": after["index"],
            "comparison": "observed_aggregate_delta_not_task_recovery",
            "deltas": differences,
            "project_deltas": project_deltas,
            "claim_authorized": False,
            "owner_review_required": (
                differences["high_claim_attempt_items"] > 0
                or differences["ten_plus_claim_attempt_items"] > 0
                or differences["expired_owner_lease_rows"] > 0),
        })
    return {
        "schema": "zsentinel-historical-queue-pressure-v1",
        "windows": [{
            "window": w["index"],
            "observed_at_utc": w["observed_at_utc"],
            "queue": w["queue_pressure"],
        } for w in source_windows],
        "adjacent_comparisons": comparisons,
        "compared_pairs": sum(
            row["deltas"] is not None for row in comparisons),
        "unknown_pairs": sum(
            row["deltas"] is None for row in comparisons),
        "comparisons_are_aggregate_samples_not_retry_counts": True,
        "source_partial": True,
        "owner_claim_verified": False,
        "retry_authorized": False,
        "recovery_verified": False,
        "writes_performed": False,
    }

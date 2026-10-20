#!/usr/bin/env python3
"""Read-only continuity of already captured private zSentinel worker observations.

A new indexed event is NOT a delivered change, resumed worker, valid lease, or
permission to restart/reclaim work. Absent/changed source identity breaks
comparison instead of claiming that a worker disappeared or recovered.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[a-z][a-z0-9_-]{0,90}$")
MAX_ENTRIES = 24
MAX_WINDOWS = 8
ASSIGNMENT_COVERAGE = frozenset((
    "not_currently_allocated", "queue_source_unavailable",
    "multiple_active_claims_owner_review", "no_active_claim_for_slot",
    "bounded_current_claim", "lease_expired_or_invalid_owner_recheck",
))
OWNER_REVIEW_CATEGORIES = frozenset((
    "allocated_paused_with_claimed_queue_owner_gate",
    "allocated_queue_claim_expired_or_invalid_owner_recheck",
    "recent_client_block_event_owner_review",
    "recent_browser_block_without_prompt_context_owner_review",
    "generating_but_progress_unverified_or_stale",
    "worker_event_stale_or_unknown",
    "configured_running_no_event_evidence",
))
OWNER_REVIEW_PRIORITY = {
    "allocated_paused_with_claimed_queue_owner_gate": 0,
    "allocated_queue_claim_expired_or_invalid_owner_recheck": 0,
    "recent_client_block_event_owner_review": 1,
    "recent_browser_block_without_prompt_context_owner_review": 1,
    "generating_but_progress_unverified_or_stale": 2,
    "worker_event_stale_or_unknown": 2,
    "configured_running_no_event_evidence": 2,
}


def _utc(value):
    if type(value) is not str or not 1 <= len(value) <= 55:
        raise ValueError("invalid worker observation timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid worker observation timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("worker observation timestamp has no offset")
    return dt.astimezone(timezone.utc)


def _cursor(row, key):
    value = row.get(key)
    if value is not None and (type(value) is not int or not 1 <= value < 2**53):
        raise ValueError("invalid indexed worker event cursor")
    return value


def observe_saved_workers(decision, source_time):
    """Admit a private worker observation; export only non-sensitive fields."""
    source = decision.get("worker_progress_observation")
    if source is None:
        return {"coverage": "not_supplied", "observed_at_utc": None,
                "source_identity_private": None, "selected_projects": [],
                "entries": [], "owner_claim_verified": False}
    if (type(source) is not dict or source.get("schema") !=
            "zsentinel-worker-progress-observation-v1"
            or source.get("source") !=
            "existing_sqlite_mode_ro_query_only_indexed_runner_events"
            or source.get("coverage") not in (
                "bounded_partial_or_no_events", "selected_project_event_slots_only")
            or any(source.get(k) is not False for k in (
                "live_worker_claims_verified", "worker_resumed_verified",
                "actions_performed", "database_write_authorized",
                "runner_restart_authorized", "event_inventory_complete"))):
        raise ValueError("untrusted or authorizing saved worker observation")
    digest = source.get("source_file_identity_sha256_private")
    if type(digest) is not str or not SHA256.fullmatch(digest):
        raise ValueError("no stable private worker source identity")
    stamp = _utc(source.get("observed_at_utc"))
    if stamp > source_time + timedelta(minutes=2) or source_time - stamp > timedelta(minutes=5):
        raise ValueError("worker evidence is not synchronous with saved decision")
    projects = source.get("selected_projects")
    if (type(projects) is not list or not 1 <= len(projects) <= 3
            or len(set(projects)) != len(projects)
            or any(type(p) is not str or not PROJECT.fullmatch(p) for p in projects)):
        raise ValueError("worker source project scope invalid")
    rows = source.get("entries")
    if type(rows) is not list or len(rows) > MAX_ENTRIES:
        raise ValueError("unbounded worker evidence")
    safe = []
    seen = set()
    for row in rows:
        if type(row) is not dict or any(row.get(k) is not False for k in (
                "owner_claim_verified", "worker_resumed_verified", "repair_authorized")):
            raise ValueError("worker entry authorizes an action")
        pid, slot = row.get("project_id"), row.get("worker_slot")
        state = row.get("classification")
        followup = row.get("safe_followup")
        if (type(pid) is not str or pid not in projects
                or type(slot) is not int or not 1 <= slot <= 16
                or (pid, slot) in seen
                or type(state) is not str or not IDENT.fullmatch(state)
                or type(followup) is not str or not IDENT.fullmatch(followup)
                or type(row.get("queue_lease_current_from_source")) is not bool
                or row.get("desired_state") not in (
                    "unknown", "running", "paused", "draining")
                or row.get("queue_assignment_coverage") not in ASSIGNMENT_COVERAGE
                or row.get("queue_claim_status_observed") not in (
                    None, "claimed", "running", "verifying")):
            raise ValueError("worker identity/status/followup invalid")
        global_slot = row.get("allocated_global_slot")
        if global_slot is not None and (
                type(global_slot) is not int or not 1 <= global_slot <= 32):
            raise ValueError("invalid observed global assignment slot")
        seen.add((pid, slot))
        latest = _cursor(row, "latest_indexed_event_id")
        progress = _cursor(row, "latest_indexed_progress_event_id")
        if progress is not None and (latest is None or progress > latest):
            raise ValueError("worker progress cursor beyond last indexed event")
        safe.append({
            "project_id": pid, "worker_slot": slot,
            "classification": state, "safe_followup": followup,
            "desired_state": row["desired_state"],
            "allocated_global_slot": global_slot,
            "queue_assignment_coverage": row["queue_assignment_coverage"],
            "queue_claim_status_observed": row["queue_claim_status_observed"],
            "latest_indexed_event_id": latest,
            "latest_indexed_progress_event_id": progress,
            "source_queue_lease_current_unverified": row["queue_lease_current_from_source"],
            "owner_claim_verified": False, "product_delivery_verified": False,
        })
    safe.sort(key=lambda x: (x["project_id"], x["worker_slot"]))
    return {
        "coverage": source["coverage"],
        "observed_at_utc": stamp.isoformat(),
        "source_identity_private": digest,
        "selected_projects": projects,
        "entries": safe,
        "owner_claim_verified": False,
    }


def summarize_worker_windows(windows):
    if type(windows) is not list or not 2 <= len(windows) <= MAX_WINDOWS:
        raise ValueError("invalid historical worker window count")
    pairs = []
    for before, after in zip(windows, windows[1:]):
        older, newer = before["worker_progress"], after["worker_progress"]
        baseline = {"from_window": before["index"], "to_window": after["index"],
                    "source_partial": True, "worker_recovered_verified": False,
                    "lease_or_ownership_authorized": False}
        if (older["coverage"] != "selected_project_event_slots_only"
                or newer["coverage"] != "selected_project_event_slots_only"
                or older["source_identity_private"] != newer["source_identity_private"]
                or older["selected_projects"] != newer["selected_projects"]
                or not older["source_identity_private"]):
            pairs.append({**baseline, "comparison": "UNKNOWN_scope_or_source_gap",
                          "workers_compared": 0, "entries": []})
            continue
        old_map = {(e["project_id"], e["worker_slot"]): e for e in older["entries"]}
        new_map = {(e["project_id"], e["worker_slot"]): e for e in newer["entries"]}
        comparable = []
        for key in sorted(old_map.keys() & new_map.keys()):
            old, new = old_map[key], new_map[key]
            last0, last1 = old["latest_indexed_event_id"], new["latest_indexed_event_id"]
            prog0, prog1 = old["latest_indexed_progress_event_id"], new["latest_indexed_progress_event_id"]
            # A rotated/replaced event ledger, missing cursor or changing
            # assignment is a NEW source/owner review, never "stalled".
            if last0 is None or last1 is None or last1 < last0:
                category = "UNKNOWN_event_cursor_or_source_rotation"
            elif (prog0 is not None and prog1 is not None and prog1 < prog0):
                category = "UNKNOWN_progress_cursor_or_source_rotation"
            elif old["desired_state"] != new["desired_state"]:
                category = "UNKNOWN_worker_desired_state_changed"
            elif (old["allocated_global_slot"] != new["allocated_global_slot"]
                  or old["queue_assignment_coverage"] != new["queue_assignment_coverage"]
                  or old["queue_claim_status_observed"] !=
                     new["queue_claim_status_observed"]):
                category = "UNKNOWN_assignment_or_lease_context_changed"
            elif last1 > last0 and prog1 is not None and (prog0 is None or prog1 > prog0):
                category = "new_indexed_generation_signal_not_product_delivery"
            elif last1 > last0:
                category = "new_indexed_event_only_no_product_delivery_proof"
            else:
                category = "no_new_indexed_event_in_two_partial_samples_not_stalled_proof"
            comparable.append({
                "project_id": key[0], "worker_slot": key[1],
                "classification": category,
                "previous_worker_state": old["classification"],
                "later_worker_state": new["classification"],
                "later_owner_followup": new["safe_followup"],
                "later_desired_state": new["desired_state"],
                "assignment_context_same_observed": (
                    old["allocated_global_slot"] == new["allocated_global_slot"]
                    and old["queue_assignment_coverage"] == new["queue_assignment_coverage"]
                    and old["queue_claim_status_observed"] == new["queue_claim_status_observed"]),
                "worker_state_changed": old["classification"] != new["classification"],
                "indexed_event_id_before": last0, "indexed_event_id_after": last1,
                "indexed_progress_id_before": prog0, "indexed_progress_id_after": prog1,
                "owner_claim_verified": False, "worker_recovered_verified": False,
                "product_delivery_verified": False,
            })
        pairs.append({
            **baseline,
            "comparison": ("bounded_same_source_worker_signal_comparison"
                           if set(old_map) == set(new_map)
                           else "partial_overlap_missing_workers_not_recovered"),
            "workers_compared": len(comparable),
            "entries": comparable,
        })
    attention = []
    for pair in pairs:
        for row in pair["entries"]:
            if (row["previous_worker_state"] in OWNER_REVIEW_CATEGORIES
                    and row["later_worker_state"] in OWNER_REVIEW_CATEGORIES
                    and row["assignment_context_same_observed"]
                    and row["classification"] not in (
                        "UNKNOWN_event_cursor_or_source_rotation",
                        "UNKNOWN_progress_cursor_or_source_rotation",
                        "UNKNOWN_worker_desired_state_changed")):
                attention.append({
                    "from_window": pair["from_window"],
                    "to_window": pair["to_window"],
                    "project_id": row["project_id"],
                    "worker_slot": row["worker_slot"],
                    "previous_classification": row["previous_worker_state"],
                    "later_classification": row["later_worker_state"],
                    "owner_followup": row["later_owner_followup"],
                    "priority": OWNER_REVIEW_PRIORITY.get(
                        row["later_worker_state"], 3),
                    "not_a_verified_stall_or_lease": True,
                    "owner_claim_verified": False,
                    "no_automatic_recovery": True,
                })
    attention.sort(key=lambda row: (
        row["priority"], -row["to_window"],
        row["project_id"], row["worker_slot"]))
    return {
        "schema": "zsentinel-historical-worker-continuity-v1",
        "persistent_observed_owner_review": attention[:MAX_ENTRIES * (MAX_WINDOWS - 1)],
        "persistent_observed_owner_review_count": len(attention),
        "source_windows": len(windows),
        "adjacent_comparisons": pairs,
        "unknown_or_partial_pairs": sum(
            p["comparison"] != "bounded_same_source_worker_signal_comparison" for p in pairs),
        "observed_generation_signal_pairs": sum(
            e["classification"] == "new_indexed_generation_signal_not_product_delivery"
            for p in pairs for e in p["entries"]),
        "current_worker_ownership_verified": False,
        "product_delivery_verified": False, "recovery_authorized": False,
        "actions_performed": False,
    }

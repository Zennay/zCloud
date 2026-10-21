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


def _unknown_blocked_queue(coverage="not_supplied"):
    return {
        "coverage": coverage, "aggregate_coverage": coverage,
        "sampled_at_utc": None, "total_items_observed": None,
        "project_groups_complete": False, "project_groups": [],
        "source_fresh_when_saved": False, "claim_authorized": False,
    }


def _observed_blocked_queue(queue, sampled, source_fresh_when_saved):
    raw = queue.get("blocked_queue")
    if raw is None:
        return _unknown_blocked_queue()
    if (type(raw) is not dict
            or raw.get("claim_ownership_verified") is not False
            or raw.get("queue_write_authorized") is not False
            or raw.get("automatic_retry_authorized") is not False):
        raise ValueError("blocked queue history refuses action authority")
    coverage = raw.get("coverage")
    if coverage in ("not_supplied", "not_observed"):
        if (raw.get("total_items_observed") is not None
                or raw.get("project_groups") != []
                or raw.get("sample_truncated") is not True):
            raise ValueError("unobserved blocked queue cannot imply zero")
        return _unknown_blocked_queue(coverage)
    if (coverage not in ("complete", "partial", "stale")
            or raw.get("aggregate_coverage") not in ("complete", "partial")
            or raw.get("source_coverage") not in ("current", "stale", "incomplete")
            or type(raw.get("total_items_observed")) is not int
            or not 0 <= raw["total_items_observed"] <= MAX_VALUE
            or type(raw.get("project_groups")) is not list
            or len(raw["project_groups"]) > 250
            or type(raw.get("sample_truncated")) is not bool
            or type(raw.get("invalid_group_rows")) is not int
            or not 0 <= raw["invalid_group_rows"] <= 251):
        raise ValueError("invalid blocked queue historical source")
    blocked_at = _moment(raw.get("observed_at"))
    if blocked_at != sampled:
        raise ValueError("blocked queue and active queue source moments differ")
    groups, identities = [], set()
    for row in raw["project_groups"]:
        if type(row) is not dict:
            raise ValueError("invalid blocked queue history group")
        pid, priority, count = row.get("project_id"), row.get("priority"), row.get("items")
        if (type(pid) is not str or not PROJECT_ID.fullmatch(pid)
                or priority not in PRIORITIES
                or type(count) is not int or not 1 <= count <= MAX_VALUE
                or (pid, priority) in identities):
            raise ValueError("invalid or duplicated blocked queue group")
        identities.add((pid, priority))
        groups.append({"project_id": pid, "priority": priority, "items": count})
    group_total = sum(row["items"] for row in groups)
    if coverage == "complete":
        if (raw["aggregate_coverage"] != "complete" or raw["sample_truncated"]
                or raw["invalid_group_rows"] != 0
                or group_total != raw["total_items_observed"]):
            raise ValueError("complete blocked queue groups disagree with total")
    elif coverage == "partial":
        if (raw["aggregate_coverage"] != "partial" or not raw["sample_truncated"]
                or group_total > raw["total_items_observed"]):
            raise ValueError("partial blocked queue coverage contradicts groups")
    elif (raw["source_coverage"] != "stale" or groups
          or not raw["sample_truncated"]):
        raise ValueError("stale blocked queue exposes current project detail")
    fresh = (source_fresh_when_saved and raw["source_coverage"] == "current"
             and coverage in ("complete", "partial"))
    effective_coverage = ("stale" if coverage == "stale" else
                          coverage if fresh else "incomplete")
    return {
        "coverage": effective_coverage, "aggregate_coverage": raw["aggregate_coverage"],
        "source_coverage": raw["source_coverage"],
        "sampled_at_utc": blocked_at.isoformat(),
        "total_items_observed": raw["total_items_observed"],
        "project_groups_complete": coverage == "complete" and fresh,
        "project_groups": groups if fresh else [],
        "source_fresh_when_saved": fresh,
        "claim_authorized": False,
    }


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
            "blocked_queue": _unknown_blocked_queue(),
            "claim_authorized": False,
        }
    opaque_identity = queue.get("source_file_identity_sha256_private")
    if (opaque_identity is not None and
            (type(opaque_identity) is not str or
             re.fullmatch(r"[0-9a-f]{64}", opaque_identity) is None)):
        raise ValueError("queue source identity invalid")
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
    # Older saved router decisions may not have the invalid claim window
    # counter. Unknown is not zero. Reject a partially populated receipt.
    window_counter = "inconsistent_owner_lease_window_rows"
    window_present = window_counter in totals
    group_window_flags = [window_counter in group for group in groups]
    if group_window_flags and any(flag != group_window_flags[0] for flag in group_window_flags):
        raise ValueError("mixed lease clock source group coverage")
    if group_window_flags and group_window_flags[0] != window_present:
        raise ValueError("lease clock total and groups coverage mismatch")
    if window_present:
        v = totals[window_counter]
        if type(v) is not int or not 0 <= v <= MAX_VALUE:
            raise ValueError("invalid aggregate inconsistent lease clock")
        if (clean["expired_owner_lease_rows"] +
                clean["unverifiable_owner_lease_rows"] + v >
                clean["owned_items"]):
            raise ValueError("incoherent combined owned lease clocks")
        clean[window_counter] = v
    else:
        clean[window_counter] = None
    source_fresh_when_saved = (
        coverage == "current"
        and -timedelta(minutes=2) <= source_time - sampled <= timedelta(minutes=5))
    if coverage == "current" and not source_fresh_when_saved:
        raise ValueError("queue source incorrectly labelled current")
    blocked_queue = _observed_blocked_queue(queue, sampled, source_fresh_when_saved)
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
        if window_present:
            value = entry[window_counter]
            if type(value) is not int or not 0 <= value <= MAX_VALUE:
                raise ValueError("invalid project inconsistent lease clock")
            counts[window_counter] = value
        else:
            counts[window_counter] = None
        active = sum(counts[f] for f in ("queued", "claimed", "running", "verifying"))
        if (counts["high_claim_attempts"] > active
                or counts["ten_plus_claim_attempts"] > counts["high_claim_attempts"]
                or counts["expired_owner_lease_rows"] +
                    counts["unverifiable_owner_lease_rows"] +
                    (counts[window_counter] or 0) > active - counts["queued"]):
            raise ValueError("incoherent historical queue group")
        clean_groups.append({"project_id": pid, "priority": priority, **counts})
    if window_present and not truncated and coverage == "current":
        if sum(g[window_counter] for g in clean_groups) != clean[window_counter]:
            raise ValueError("inconsistent lease clocks mismatch project totals")
    saved_rows = queue.get("samples")
    if type(saved_rows) is not list or len(saved_rows) > 20:
        raise ValueError("missing bounded positive queue row observations")
    positive_rows, seen_refs = [], set()
    for row in saved_rows:
        if type(row) is not dict:
            raise ValueError("malformed private queue row")
        ref, pid, priority = (row.get("queue_ref"), row.get("project_id"),
                              row.get("priority"))
        count = row.get("claim_attempts")
        if (type(ref) is not str or re.fullmatch(r"[a-f0-9]{16}", ref) is None
                or ref in seen_refs or type(pid) is not str
                or PROJECT_ID.fullmatch(pid) is None or priority not in PRIORITIES
                or type(count) is not int or not 0 <= count <= MAX_VALUE):
            raise ValueError("invalid duplicated or unbounded queue reference")
        seen_refs.add(ref)
        positive_rows.append({"queue_ref": ref, "project_id": pid,
                              "priority": priority, "claim_attempts": count})
    if coverage != "current" and saved_rows:
        raise ValueError("stale queue contains current item detail")
    return {
        "coverage": coverage,
        "sampled_at_utc": sampled.isoformat(),
        "positive_queue_refs": positive_rows,
        "totals": clean,
        "sample_is_complete": sample_complete,
        "source_file_identity_sha256_private": opaque_identity,
        "inconsistent_lease_window_coverage": (
            "observed" if window_present else "unknown_legacy"),
        "project_groups_complete": not truncated and coverage == "current",
        "project_groups": clean_groups,
        "blocked_queue": blocked_queue,
        "source_fresh_when_saved": source_fresh_when_saved,
        "claim_authorized": False,
    }


def _blocked_queue_delta(left, right):
    unknown = {
        "coverage": "UNKNOWN_source_gap",
        "from_total": None, "to_total": None, "delta": None,
        "project_deltas": None, "count_change_is_not_resolution_proof": True,
        "claim_authorized": False, "retry_authorized": False,
    }
    if (type(left) is not dict or type(right) is not dict
            or left.get("source_fresh_when_saved") is not True
            or right.get("source_fresh_when_saved") is not True
            or type(left.get("total_items_observed")) is not int
            or type(right.get("total_items_observed")) is not int):
        return unknown
    from_total, to_total = left["total_items_observed"], right["total_items_observed"]
    project_deltas = None
    if (left.get("project_groups_complete") is True
            and right.get("project_groups_complete") is True):
        before = {(row["project_id"], row["priority"]): row["items"]
                  for row in left["project_groups"]}
        after = {(row["project_id"], row["priority"]): row["items"]
                 for row in right["project_groups"]}
        project_deltas = [{
            "project_id": project, "priority": priority,
            "blocked_rows_delta_not_resolution_proof":
                after.get((project, priority), 0) - before.get((project, priority), 0),
            "task_recovery_verified": False, "claim_authorized": False,
        } for project, priority in sorted(
            set(before) | set(after),
            key=lambda key: (PRIORITIES.index(key[1]), key[0]))]
    return {
        "coverage": "complete_groups" if project_deltas is not None else "total_only",
        "from_total": from_total, "to_total": to_total,
        "delta": to_total - from_total, "project_deltas": project_deltas,
        "count_change_is_not_resolution_proof": True,
        "claim_authorized": False, "retry_authorized": False,
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
                      and left.get("source_file_identity_sha256_private") is not None
                      and left.get("source_file_identity_sha256_private") ==
                          right.get("source_file_identity_sha256_private")
                      and left.get("source_fresh_when_saved") is True
                      and right.get("source_fresh_when_saved") is True)
        if not comparable:
            comparisons.append({
                "from_window": before["index"], "to_window": after["index"],
                "comparison": "UNKNOWN_source_gap",
                "deltas": None, "project_deltas": None, "claim_authorized": False,
                "observed_increased_queue_references": [],
                "observed_increased_refs_total": None,
                "observed_increased_refs_truncated": False,
                "blocked_queue_comparison": _blocked_queue_delta(
                    left.get("blocked_queue"), right.get("blocked_queue")),
                "positive_refs_only_not_an_exhaustive_retry_inventory": True,
                "owner_review_required": True,
            })
            continue
        differences = {
            field: right["totals"][field] - left["totals"][field]
            for field in FIELDS
        }
        # Invalid lease windows are not equivalent to expired leases.
        earlier = left["totals"].get("inconsistent_owner_lease_window_rows")
        later = right["totals"].get("inconsistent_owner_lease_window_rows")
        differences["inconsistent_owner_lease_window_rows"] = (
            later - earlier if earlier is not None and later is not None else None)
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
                    "inconsistent_owner_lease_window_rows_delta": (
                        current.get("inconsistent_owner_lease_window_rows", 0) -
                        old.get("inconsistent_owner_lease_window_rows", 0)
                        if (current.get("inconsistent_owner_lease_window_rows") is not None
                            and old.get("inconsistent_owner_lease_window_rows") is not None)
                        else None),
                    "task_recovery_verified": False,
                    "owner_claim_verified": False,
                })
        before_refs = {r["queue_ref"]: r for r in left["positive_queue_refs"]}
        after_refs = {r["queue_ref"]: r for r in right["positive_queue_refs"]}
        observed_same_ref = []
        for ref in sorted(set(before_refs) & set(after_refs)):
            old, new = before_refs[ref], after_refs[ref]
            # A changed project/prio is not a scoped continuity proof.
            if (old["project_id"], old["priority"]) != (
                    new["project_id"], new["priority"]):
                continue
            increase = new["claim_attempts"] - old["claim_attempts"]
            if increase > 0:
                observed_same_ref.append({
                    "private_queue_ref": ref,
                    "project_id": new["project_id"],
                    "priority": new["priority"],
                    "previous_attempts": old["claim_attempts"],
                    "current_attempts": new["claim_attempts"],
                    "observed_attempt_increase": increase,
                    "same_reference_not_same_claim_proof": True,
                    "retry_authorized": False,
                })
        observed_same_ref.sort(key=lambda row: (
            PRIORITIES.index(row["priority"]), -row["observed_attempt_increase"],
            row["project_id"], row["private_queue_ref"]))
        comparisons.append({
            "from_window": before["index"], "to_window": after["index"],
            "blocked_queue_comparison": _blocked_queue_delta(
                left.get("blocked_queue"), right.get("blocked_queue")),
            "observed_increased_queue_references": observed_same_ref[:12],
            "observed_increased_refs_total": len(observed_same_ref),
            "observed_increased_refs_truncated": len(observed_same_ref) > 12,
            "positive_refs_only_not_an_exhaustive_retry_inventory": True,
            "comparison": "observed_aggregate_delta_not_task_recovery",
            "deltas": differences,
            "project_deltas": project_deltas,
            "claim_authorized": False,
            "owner_review_required": (
                differences["high_claim_attempt_items"] > 0
                or differences["ten_plus_claim_attempt_items"] > 0
                or differences["expired_owner_lease_rows"] > 0
                or (differences["inconsistent_owner_lease_window_rows"] is not None
                    and differences["inconsistent_owner_lease_window_rows"] > 0)),
        })
    # Three consecutive *complete, same-identity project samples* expose
    # persistent owner-attention signals. Counts may refer to different jobs;
    # neither continuity of an individual lease nor a failed retry is proven.
    sustained = []
    eligible_triples = 0
    for first, middle, last in zip(source_windows, source_windows[1:],
                                    source_windows[2:]):
        times = [_moment(w["observed_at_utc"]) for w in (first, middle, last)]
        if (not all(w["queue_pressure"]["coverage"] == "current"
                    and w["queue_pressure"].get("source_fresh_when_saved") is True
                    and w["queue_pressure"].get("project_groups_complete") is True
                    # High-churn display samples may be truncated even if
                    # the authoritative totals and project groups are complete.
                    # Do not confuse sample visibility with aggregate coverage.
                    for w in (first, middle, last))
                or first["queue_pressure"].get("source_file_identity_sha256_private") is None
                or len({w["queue_pressure"].get("source_file_identity_sha256_private")
                        for w in (first, middle, last)}) != 1
                or not times[0] < times[1] < times[2]
                or times[2] - times[0] > timedelta(minutes=45)):
            continue
        eligible_triples += 1
        groups = [{
            (g["project_id"], g["priority"]): g
            for g in w["queue_pressure"]["project_groups"]
        } for w in (first, middle, last)]
        for key in sorted(set(groups[0]) & set(groups[1]) & set(groups[2]),
                          key=lambda x: (PRIORITIES.index(x[1]), x[0])):
            rows = [g[key] for g in groups]
            signal = ("persistent_inconsistent_lease_clock_counts"
                      if all(r["inconsistent_owner_lease_window_rows"] is not None
                             and r["inconsistent_owner_lease_window_rows"] > 0 for r in rows)
                      else "persistent_expired_lease_row_counts"
                      if all(r["expired_owner_lease_rows"] > 0 for r in rows)
                      else "persistent_high_claim_attempt_counts"
                      if all(r["high_claim_attempts"] > 0 for r in rows)
                      else None)
            if signal:
                sustained.append({
                    "project_id": key[0], "priority": key[1],
                    "first_window": first["index"], "last_window": last["index"],
                    "signal": signal,
                    "observed_counts": [
                        r["inconsistent_owner_lease_window_rows"] if
                        signal == "persistent_inconsistent_lease_clock_counts" else
                        r["high_claim_attempts"] if signal.startswith("persistent_high")
                        else r["expired_owner_lease_rows"] for r in rows],
                    "original_task_identity_verified": False,
                    "retry_or_lease_mutation_authorized": False,
                    "owner_next_step": (
                        "Reconcile exact current queue row and active lease with its "
                        "existing owner; do not reset, retry or take over work."),
                })
    # Deduplicate overlapping triples by project+priority+signal. Preserve
    # latest interval as *observation*, never as a current authority claim.
    latest = {}
    for row in sustained:
        latest[(row["project_id"], row["priority"], row["signal"])] = row
    sustained = sorted(latest.values(), key=lambda row: (
        PRIORITIES.index(row["priority"]), row["project_id"], row["signal"]))
    visible = sustained[:12]
    return {
        "schema": "zsentinel-historical-queue-pressure-v1",
        "windows": [{
            "window": w["index"],
            "observed_at_utc": w["observed_at_utc"],
            "queue": w["queue_pressure"],
        } for w in source_windows],
        "adjacent_comparisons": comparisons,
        "sustained_owner_attention": visible,
        "sustained_owner_attention_total": len(sustained),
        "sustained_owner_attention_truncated": len(sustained) > len(visible),
        "eligible_complete_three_window_sequences": eligible_triples,
        "sustained_signal_is_not_same_task_proof": True,
        "compared_pairs": sum(
            row["deltas"] is not None for row in comparisons),
        "unknown_pairs": sum(
            row["deltas"] is None for row in comparisons),
        "blocked_queue_compared_pairs": sum(
            row["blocked_queue_comparison"]["delta"] is not None for row in comparisons),
        "blocked_queue_unknown_pairs": sum(
            row["blocked_queue_comparison"]["delta"] is None for row in comparisons),
        "comparisons_are_aggregate_samples_not_retry_counts": True,
        "source_partial": True,
        "owner_claim_verified": False,
        "retry_authorized": False,
        "recovery_verified": False,
        "writes_performed": False,
    }

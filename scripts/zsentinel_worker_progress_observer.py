#!/usr/bin/env python3
"""Read-only observed browser-worker progress for zSentinel.

Uses existing indexed runner_events metadata, never the live server GET
handlers (which may reconcile/mutate DB state). No target/chat URL, prompt,
title, user message, raw reason, error, or event payload is exported.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

UTC = timezone.utc
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
BLOCKED = (
    "assignment-invalid", "send-blocked",
    "assignment-refresh-failed", "thinking-effort-unavailable",
)
SAFE_EVENT = frozenset((
    "heartbeat", "generation-started", "generation-finished",
    "prompt-sent", "runner-paused", "runner-drained", "runner-restarted",
    "generation-progress", "assignment-invalid", "send-blocked",
    "assignment-refresh-failed", "thinking-effort-unavailable",
))
REQUIRED_COLUMNS = frozenset((
    "id", "project_id", "worker_slot", "ts", "event",
    "generating", "sending", "progress_at", "assistant_chars",
))
MAX_PROJECTS = 3
MAX_SLOTS = 8
MAX_TIMELINE_EVENTS = 12
MAX_WORKER_EVENT_LOOKBACK = 240


def _source_current(moment):
    """Display-time freshness is advisory, never queue/worker authorization."""
    age = (datetime.now(UTC) - moment.astimezone(UTC)).total_seconds()
    return -120 <= age <= 600


def _age(value, now):
    """Return age only for a valid event timestamp that is not in the future."""
    timestamp = _utc_datetime(value)
    if timestamp is None:
        return None
    seconds = (now.astimezone(UTC) - timestamp).total_seconds()
    # Future event clocks are untrusted; never clamp them to age zero.
    if seconds < 0:
        return None
    return int(seconds)


def _timestamp_status(value, now):
    """Expose only a bounded timestamp-quality enum, never the raw event clock."""
    timestamp = _utc_datetime(value)
    if timestamp is None:
        return "UNKNOWN"
    return "FUTURE" if timestamp > now.astimezone(UTC) else "OBSERVED"


def _utc_datetime(value):
    if type(value) is not str or not 1 <= len(value) <= 55:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp.astimezone(UTC)


def _in_claim_window(value, start, expires, now):
    if (not isinstance(start, datetime) or not isinstance(expires, datetime)
            or not isinstance(now, datetime) or now.tzinfo is None):
        return None
    timestamp = _utc_datetime(value)
    if timestamp is None or timestamp > now.astimezone(UTC):
        # Invalid/future event timestamps cannot correlate to the current claim.
        return None
    return bool(start <= timestamp < expires)

def _bounded_timeline(con, project, slot, last_id, prompt_id, now,
                      claim_start=None, claim_expires=None):
    """Minimal indexed worker event chronology, no payloads or ownership claim.

    The recent history is an explicitly bounded window. Fewer observed
    blocking events is NOT proof of no other failures in the full DB.
    """
    rows = con.execute(
        "SELECT id,ts,event,generating,sending FROM runner_events "
        "INDEXED BY runner_events_project_worker_id "
        "WHERE project_id=? AND worker_slot=? AND id<=? "
        "ORDER BY id DESC LIMIT ?",
        (project, slot, last_id, MAX_TIMELINE_EVENTS + 1),
    ).fetchall()
    truncated = len(rows) > MAX_TIMELINE_EVENTS
    claim_window_available = (
        isinstance(claim_start, datetime) and isinstance(claim_expires, datetime)
        and claim_start < claim_expires
    )
    events = []
    recent_blocks = 0
    recent_progress = 0
    after_prompt_seen = 0
    claim_window_events = 0
    claim_window_blocks = 0
    claim_window_progress = 0
    for ident, stamp, kind, generating, sending in rows[:MAX_TIMELINE_EVENTS]:
        if type(ident) is not int or not 1 <= ident <= last_id:
            raise ValueError("invalid worker timeline event identifier")
        timestamp_status = _timestamp_status(stamp, now)
        age = _age(stamp, now)
        safe_kind = kind if isinstance(kind, str) and kind in SAFE_EVENT else "other_event"
        in_claim_window = (
            _in_claim_window(stamp, claim_start, claim_expires, now)
            if claim_window_available else None
        )
        if in_claim_window is True:
            claim_window_events += 1
            if safe_kind in BLOCKED and age is not None and age <= 900:
                claim_window_blocks += 1
            if (safe_kind in ("generation-progress", "generation-finished")
                    and age is not None and age <= 900):
                claim_window_progress += 1
        after_prompt = (
            prompt_id is not None and ident > prompt_id
            and (not claim_window_available or in_claim_window is True)
        )
        if after_prompt:
            after_prompt_seen += 1
        if safe_kind in BLOCKED and after_prompt and age is not None and age <= 900:
            recent_blocks += 1
        if (safe_kind in ("generation-progress", "generation-finished")
                and after_prompt and age is not None and age <= 900):
            recent_progress += 1
        events.append({
            "event": safe_kind,
            "age_seconds": age,
            "timestamp_status": timestamp_status,
            "after_latest_prompt": bool(after_prompt),
            "within_current_claim_window": in_claim_window,
            "browser_generating": generating == 1,
            "browser_sending": sending == 1,
        })
    return {
        "recent_events_newest_first": events,
        "timeline_truncated": truncated,
        "timeline_scope": "last_12_indexed_events_not_complete_history",
        "events_after_last_prompt_in_sample": after_prompt_seen,
        "recent_block_events_after_last_prompt_in_sample": recent_blocks,
        "recent_progress_events_after_last_prompt_in_sample": recent_progress,
        "claim_window_correlation": (
            "time_window_only_not_identity_bound" if claim_window_available
            else "unavailable_no_valid_current_lease"
        ),
        "claim_window_event_count_in_sample": claim_window_events if claim_window_available else None,
        "claim_window_recent_block_count_in_sample": claim_window_blocks if claim_window_available else None,
        "claim_window_recent_progress_count_in_sample": claim_window_progress if claim_window_available else None,
        "claim_window_counts_sample_only": True,
        "claim_epoch_bound": False,
        "progress_is_product_delivery_proof": False,
    }


def _safe_followup(state, assignment):
    """Decision support only; NEVER infer claim/lease/recovery authority."""
    if state == "allocated_paused_with_claimed_queue_owner_gate":
        return "owner_check_paused_worker_with_current_queue_lease"
    if state == "allocated_queue_claim_expired_or_invalid_owner_recheck":
        return "owner_reconcile_stale_queue_claim_without_claim_transfer"
    if state == "allocated_no_active_claim_owner_review":
        return "owner_verify_unclaimed_allocated_slot"
    if state == "allocated_multiple_active_claims_owner_review":
        return "owner_reconcile_duplicate_active_queue_claims"
    if state == "allocated_queue_source_unavailable_owner_review":
        return "owner_recheck_queue_observer_source_scope"
    if state == "recent_client_block_event_owner_review":
        return "owner_inspect_current_assignment_and_recent_browser_block"
    if state == "recent_browser_block_without_prompt_context_owner_review":
        return "owner_verify_orphan_browser_block_without_prompt_identity"
    if state == "post_block_progress_signal_owner_verification_required":
        return "owner_verify_product_continuation_after_block_signal"
    if state in ("generating_but_progress_unverified_or_stale",
                 "worker_event_stale_or_unknown",
                 "configured_running_no_event_evidence"):
        return "owner_verify_current_lease_and_real_worker_progress"
    if state == "generation_progress_timestamp_observed":
        return "continue_read_only_observation_without_takeover"
    if state == "generation_finish_event_observed_not_product_delivery":
        return "owner_verify_actual_product_commit_or_handoff"
    if state == "configured_draining_owner_hold":
        return "respect_draining_owner_hold"
    if state in ("configured_paused_not_incident",
                 "configured_not_allocated_not_a_stalled_worker"):
        return "no_stalled_worker_recovery_from_this_evidence"
    return "owner_recheck_current_source_without_recovery"

def _active_queue_assignment(con, project, global_slot, now, queue_available):
    """Observe claim/lease state for one actual allocated global slot.

    No queue IDs, titles, raw timestamps or owner identity escape. The hashed
    fingerprint identifies the observed queue claim window across snapshots;
    runner events have no queue ID, so event correlation remains time-window
    only and never becomes claim-epoch or recovery authority.
    """
    empty = {
        "coverage": "not_currently_allocated",
        "current_lease": False,
        "status": None,
        "claim_window_start": None,
        "claim_window_expires": None,
        "claim_window_fingerprint": None,
    }
    if global_slot is None:
        return empty
    if not queue_available:
        return {**empty, "coverage": "queue_source_unavailable"}
    rows = con.execute(
        "SELECT queue_id,status,claimed_at,claim_expires,attempts FROM portfolio_queue "
        "WHERE project_id=? AND worker_slot=? "
        "AND status IN ('claimed','running','verifying') "
        "ORDER BY updated_at DESC LIMIT 2",
        (project, global_slot)).fetchall()
    if len(rows) > 1:
        return {**empty, "coverage": "multiple_active_claims_owner_review"}
    if not rows:
        return {**empty, "coverage": "no_active_claim_for_slot"}
    queue_id, status, claimed_at, expires_at, attempts = rows[0]
    claim_start = _utc_datetime(claimed_at)
    claim_expires = _utc_datetime(expires_at)
    queue_id_valid = (
        type(queue_id) is str and 1 <= len(queue_id) <= 160
        and not any(ord(char) < 32 for char in queue_id)
    )
    attempts_valid = type(attempts) is int and 0 <= attempts <= 1000000
    duration = (
        (claim_expires - claim_start).total_seconds()
        if claim_start is not None and claim_expires is not None else None
    )
    current = bool(
        queue_id_valid and attempts_valid
        and status in ("claimed", "running", "verifying")
        and claim_start is not None and claim_expires is not None
        and claim_start <= now < claim_expires
        and duration is not None and 0 < duration <= 7 * 86400
    )
    fingerprint = None
    if current:
        # Exclude the rolling expiry so lease renewals do not masquerade as a
        # new claim epoch. Queue ID + claimed_at distinguish slot reuse without
        # exposing either value in the operator artifact.
        fingerprint = hashlib.sha256("\\0".join((
            project, str(global_slot), queue_id, str(attempts),
            claim_start.isoformat(timespec="microseconds"),
        )).encode("utf-8")).hexdigest()
    return {
        "coverage": ("bounded_current_claim" if current
                     else "lease_expired_or_invalid_owner_recheck"),
        "current_lease": current,
        "status": status if status in ("claimed", "running", "verifying") else None,
        "claim_window_start": claim_start if current else None,
        "claim_window_expires": claim_expires if current else None,
        "claim_window_fingerprint": fingerprint,
    }


def _observe_slot(con, project, slot, now, desired_state, global_slot, queue_available):
    args = (project, slot)
    assignment = _active_queue_assignment(
        con, project, global_slot, now, queue_available)
    claim_window_start = assignment.get("claim_window_start")
    claim_window_expires = assignment.get("claim_window_expires")
    claim_window_available = (
        assignment["current_lease"]
        and isinstance(claim_window_start, datetime)
        and isinstance(claim_window_expires, datetime)
    )
    claim_window_fingerprint = assignment.get("claim_window_fingerprint")
    paused_with_claim = bool(
        desired_state == "paused" and global_slot is not None
        and assignment["current_lease"] and assignment["status"] is not None)
    stale_queue_claim = bool(
        global_slot is not None and assignment["status"] is not None
        and assignment["coverage"] == "lease_expired_or_invalid_owner_recheck")
    # Browser telemetry never establishes an active queue task. A reused
    # browser tab can emit progress after an old claim has disappeared.
    queue_context_review = {
        "no_active_claim_for_slot": "allocated_no_active_claim_owner_review",
        "multiple_active_claims_owner_review": "allocated_multiple_active_claims_owner_review",
        "queue_source_unavailable": "allocated_queue_source_unavailable_owner_review",
    }.get(assignment["coverage"]) if global_slot is not None else None
    last = con.execute(
        "SELECT id,ts,event,generating,sending,progress_at,assistant_chars "
        "FROM runner_events WHERE project_id=? AND worker_slot=? "
        "ORDER BY id DESC LIMIT 1", args).fetchone()
    if last is None:
        return {
            "project_id": project, "worker_slot": slot,
            "desired_state": desired_state,
            "desired_state_source_observed": desired_state != "unknown",
            "allocated_global_slot": global_slot,
            "queue_claim_status_observed": assignment["status"],
            "queue_assignment_coverage": assignment["coverage"],
            "queue_lease_current_from_source": assignment["current_lease"],
            "claim_window_fingerprint": claim_window_fingerprint,
            "claim_window_correlation": (
                "time_window_only_not_identity_bound" if claim_window_available
                else "unavailable_no_valid_current_lease"
            ),
            "latest_event_in_current_claim_window": False if claim_window_available else None,
            "latest_progress_event_in_current_claim_window": False if claim_window_available else None,
            "last_event_type": None, "event_age_seconds": None,
            "event_timestamp_status": "NOT_OBSERVED",
            "progress_timestamp_status": "NOT_OBSERVED",
            "latest_indexed_event_id": None, "latest_indexed_progress_event_id": None,
            "progress_age_seconds": None, "assistant_character_count": None,
            "browser_generating": False, "browser_sending": False,
            "recent_block_event_after_last_prompt": False,
            "recent_block_event_without_prompt_context": False,
            "later_progress_signal_after_recent_block": False,
            "classification": (
                "allocated_paused_with_claimed_queue_owner_gate"
                if paused_with_claim else
                "allocated_queue_claim_expired_or_invalid_owner_recheck"
                if stale_queue_claim else
                queue_context_review
                if desired_state == "running" and queue_context_review is not None else
                "configured_running_no_event_evidence"
                if desired_state == "running" and global_slot is not None
                else "configured_not_allocated_not_a_stalled_worker"
                if global_slot is None and desired_state == "running"
                else "configured_paused_not_incident"
                if desired_state == "paused"
                else "configured_draining_owner_hold"
                if desired_state == "draining"
                else "desired_state_unknown_no_automatic_action"),
            "event_source": "indexed_local_runner_events_only",
            "owner_claim_verified": False, "worker_resumed_verified": False,
            "repair_authorized": False,
            "safe_followup": _safe_followup(
                "allocated_paused_with_claimed_queue_owner_gate"
                if paused_with_claim else
                "allocated_queue_claim_expired_or_invalid_owner_recheck"
                if stale_queue_claim else
                queue_context_review
                if desired_state == "running" and queue_context_review is not None else
                "configured_running_no_event_evidence"
                if desired_state == "running" and global_slot is not None else
                "configured_not_allocated_not_a_stalled_worker"
                if global_slot is None and desired_state == "running" else
                "configured_paused_not_incident"
                if desired_state == "paused" else
                "configured_draining_owner_hold"
                if desired_state == "draining" else
                "desired_state_unknown_no_automatic_action", assignment),
            "timeline": {
                "recent_events_newest_first": [],
                "timeline_truncated": False,
                "timeline_scope": "no_events_observed",
                "events_after_last_prompt_in_sample": 0,
                "recent_block_events_after_last_prompt_in_sample": 0,
                "recent_progress_events_after_last_prompt_in_sample": 0,
                "claim_window_correlation": (
                    "time_window_only_not_identity_bound" if claim_window_available
                    else "unavailable_no_valid_current_lease"
                ),
                "claim_window_event_count_in_sample": 0 if claim_window_available else None,
                "claim_window_recent_block_count_in_sample": 0 if claim_window_available else None,
                "claim_window_recent_progress_count_in_sample": 0 if claim_window_available else None,
                "claim_window_counts_sample_only": True,
                "claim_epoch_bound": False,
                "progress_is_product_delivery_proof": False,
            },
        }
    last_id, timestamp, event, generating, sending, progress, chars = last
    if type(last_id) is not int or not 1 <= last_id <= 2**53 - 1:
        raise ValueError("invalid worker event row identity")
    event_age = _age(timestamp, now)
    if type(generating) is not int or generating not in (0, 1):
        generating = 0
    if type(sending) is not int or sending not in (0, 1):
        sending = 0
    if chars is not None and (type(chars) is not int or not 0 <= chars <= 2000000):
        chars = None
    # Heartbeats can repeat a browser's old progress_at indefinitely.
    # Keep latest event id separately from the most recent explicit
    # generation-progress/finish event. Neither identifies a claim epoch,
    # but both are stable indexed SQLite cursor evidence for comparisons.
    progress_row = con.execute(
        "SELECT id,ts,progress_at,assistant_chars FROM runner_events "
        "INDEXED BY runner_events_project_worker_event_id "
        "WHERE project_id=? AND worker_slot=? AND id>=? "
        "AND event IN ('generation-progress','generation-finished') "
        "ORDER BY id DESC LIMIT 1",
        (project, slot, max(1, last_id-MAX_WORKER_EVENT_LOOKBACK))).fetchone()
    latest_progress_id = None
    progress = None
    progress_timestamp_status = "NOT_OBSERVED"
    progress_event_in_window = False if claim_window_available else None
    progress_timestamp_in_window = False if claim_window_available else None
    if progress_row is not None:
        candidate_id, event_ts, reported_progress, progress_chars = progress_row
        if (type(candidate_id) is not int
                or not 1 <= candidate_id <= last_id):
            raise ValueError("invalid indexed progress event identity")
        progress_event_in_window = (
            _in_claim_window(event_ts, claim_window_start, claim_window_expires, now)
            if claim_window_available else None
        )
        if not claim_window_available or progress_event_in_window is True:
            progress_signal = reported_progress if reported_progress is not None else event_ts
            progress_timestamp_status = _timestamp_status(progress_signal, now)
            progress_timestamp_in_window = (
                _in_claim_window(progress_signal, claim_window_start, claim_window_expires, now)
                if claim_window_available else None
            )
            if not claim_window_available or progress_timestamp_in_window is True:
                latest_progress_id = candidate_id
                progress = progress_signal
                if chars is None:
                    chars = progress_chars
    progress_age = _age(progress, now)
    prompt = con.execute(
        "SELECT id,ts FROM runner_events WHERE project_id=? "
        "AND worker_slot=? AND event='prompt-sent' ORDER BY id DESC LIMIT 1",
        args).fetchone()
    blocked = con.execute(
        "SELECT id,ts,event FROM runner_events WHERE project_id=? "
        "AND worker_slot=? AND event IN (?,?,?,?) ORDER BY id DESC LIMIT 1",
        args + BLOCKED).fetchone()
    block_age = _age(blocked[1], now) if blocked is not None else None
    prompt_in_window = (
        _in_claim_window(prompt[1], claim_window_start, claim_window_expires, now)
        if prompt is not None and claim_window_available else None
    )
    blocked_in_window = (
        _in_claim_window(blocked[1], claim_window_start, claim_window_expires, now)
        if blocked is not None and claim_window_available else None
    )
    # Old prompt/block receipts cannot be attributed to a newer queue claim
    # merely because the same local worker slot was reused. runner_events has
    # no queue ID, so this is timestamp-window correlation, not exact binding.
    timeline_prompt_id = (
        prompt[0] if prompt is not None and
        (not claim_window_available or prompt_in_window is True) else None
    )
    if claim_window_available:
        blocked_after_prompt = bool(
            blocked is not None and blocked_in_window is True
            and prompt is not None and prompt_in_window is True
            and blocked[0] > prompt[0]
            and block_age is not None and block_age <= 900
        )
        unbound_recent_block = bool(
            blocked is not None and blocked_in_window is True
            and (prompt is None or prompt_in_window is not True)
            and block_age is not None and block_age <= 900
        )
    else:
        blocked_after_prompt = bool(
            blocked is not None and prompt is not None
            and blocked[0] > prompt[0]
            and block_age is not None and block_age <= 900
        )
        unbound_recent_block = bool(
            blocked is not None and prompt is None
            and block_age is not None and block_age <= 900
        )
    # A later progress/finish event is NOT product delivery, but an old
    # block must not remain displayed as an unanswered current failure.
    # Retain the original failure receipt and require owner verification.
    later_progress = None
    if blocked_after_prompt:
        later_progress = con.execute(
            "SELECT id,ts FROM runner_events "
            "INDEXED BY runner_events_project_worker_event_id "
            "WHERE project_id=? AND worker_slot=? "
            "AND event IN ('generation-progress','generation-finished') "
            "AND id>? ORDER BY id DESC LIMIT 1",
            (project, slot, blocked[0]),
        ).fetchone()
    later_progress_in_window = (
        _in_claim_window(later_progress[1], claim_window_start, claim_window_expires, now)
        if later_progress is not None and claim_window_available else None
    )
    newer_signal = bool(
        later_progress is not None and type(later_progress[0]) is int
        and later_progress[0] > blocked[0]
        and _age(later_progress[1], now) is not None
        and _age(later_progress[1], now) <= 900
        and (not claim_window_available or later_progress_in_window is True)
    )
    timeline = _bounded_timeline(
        con, project, slot, last_id, timeline_prompt_id, now,
        claim_window_start if claim_window_available else None,
        claim_window_expires if claim_window_available else None)
    event_in_window = (
        _in_claim_window(timestamp, claim_window_start, claim_window_expires, now)
        if claim_window_available else None
    )
    if paused_with_claim:
        state = "allocated_paused_with_claimed_queue_owner_gate"
    elif stale_queue_claim:
        # Queue status is still claimed/running/verifying but its lease
        # cannot be treated as live. Require the canonical queue owner,
        # NEVER auto-release or reassign this slot from stale metadata.
        state = "allocated_queue_claim_expired_or_invalid_owner_recheck"
    elif desired_state == "paused":
        state = "configured_paused_not_incident"
    elif desired_state == "draining":
        state = "configured_draining_owner_hold"
    elif desired_state != "running":
        state = "desired_state_unknown_no_automatic_action"
    elif global_slot is None:
        # A lingering running configuration without an assigned global slot
        # is not an active, stalled worker, regardless of old events.
        state = "configured_not_allocated_not_a_stalled_worker"
    elif queue_context_review is not None:
        # Current queue context is not independently trustworthy; an event
        # must not mask missing or duplicate leases. No automatic repair.
        state = queue_context_review
    elif blocked_after_prompt and newer_signal:
        state = "post_block_progress_signal_owner_verification_required"
    elif blocked_after_prompt:
        state = "recent_client_block_event_owner_review"
    elif unbound_recent_block:
        state = "recent_browser_block_without_prompt_context_owner_review"
    elif claim_window_available and event_in_window is not True:
        # Recent heartbeats/events from a previous queue task are not evidence
        # of progress for the current lease window.
        state = "worker_event_stale_or_unknown"
    elif (generating and event_age is not None and event_age <= 180
          and progress_age is not None and progress_age <= 180
          and (not claim_window_available or
               (progress_event_in_window is True
                and progress_timestamp_in_window is True))):
        state = "generation_progress_timestamp_observed"
    elif generating and event_age is not None and event_age <= 180:
        state = "generating_but_progress_unverified_or_stale"
    elif event_age is None or event_age > 600:
        state = "worker_event_stale_or_unknown"
    elif event == "generation-finished":
        state = "generation_finish_event_observed_not_product_delivery"
    else:
        state = "recent_event_only_no_product_progress_proof"
    return {
        "project_id": project, "worker_slot": slot,
        "desired_state": desired_state,
        "desired_state_source_observed": desired_state != "unknown",
        "allocated_global_slot": global_slot,
        "queue_claim_status_observed": assignment["status"],
        "queue_assignment_coverage": assignment["coverage"],
        "queue_lease_current_from_source": assignment["current_lease"],
        "claim_window_fingerprint": claim_window_fingerprint,
        "claim_window_correlation": (
            "time_window_only_not_identity_bound" if claim_window_available
            else "unavailable_no_valid_current_lease"
        ),
        "latest_event_in_current_claim_window": event_in_window,
        "latest_progress_event_in_current_claim_window": progress_event_in_window,
        "progress_timestamp_in_current_claim_window": progress_timestamp_in_window,
        "last_event_type": event if event in SAFE_EVENT else "other_event",
        "event_age_seconds": event_age,
        "event_timestamp_status": _timestamp_status(timestamp, now),
        "progress_timestamp_status": progress_timestamp_status,
        "latest_indexed_event_id": last_id,
        "latest_indexed_progress_event_id": latest_progress_id,
        "progress_age_seconds": progress_age,
        "assistant_character_count": chars if type(chars) is int and 0 <= chars <= 2000000 else None,
        "browser_generating": bool(generating),
        "browser_sending": bool(sending),
        "recent_block_event_after_last_prompt": blocked_after_prompt,
        "recent_block_event_without_prompt_context": unbound_recent_block,
        "later_progress_signal_after_recent_block": newer_signal,
        "classification": state,
        "event_source": "indexed_local_runner_events_only",
        "owner_claim_verified": False,
        "worker_resumed_verified": False,
        "repair_authorized": False,
        "safe_followup": _safe_followup(state, assignment),
        "timeline": timeline,
    }


def observe_worker_progress(database, projects, *, now=None):
    """One bounded immutable SQLite read; no browser GET, no DB mutations."""
    if (not isinstance(projects, list) or not 1 <= len(projects) <= MAX_PROJECTS
            or len(projects) != len(set(projects))
            or any(type(pid) is not str or not PROJECT.fullmatch(pid)
                   for pid in projects)):
        raise ValueError("select one to three canonical project IDs")
    path = Path(database)
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("existing nonempty worker events database required")
    before = path.stat()
    # Private cross-snapshot comparison token; never publish the path,
    # device/inode or hostname in the public worker cards.
    source_identity = hashlib.sha256(
        (os.uname().nodename + "\\0" + str(path.resolve()) + "\\0" +
         str(before.st_dev) + "\\0" + str(before.st_ino)).encode("utf-8")
    ).hexdigest()
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    entries = []
    missing = []
    truncated = []
    con = sqlite3.connect(uri, uri=True, timeout=1.5, isolation_level=None)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=1500")
        columns = {r[1] for r in con.execute("PRAGMA table_info(runner_events)")}
        if not REQUIRED_COLUMNS.issubset(columns):
            raise ValueError("runner_events source schema absent or incomplete; no migrations")
        indices = {r[1] for r in con.execute("PRAGMA index_list(runner_events)")}
        if not {"runner_events_project_worker_id", "runner_events_project_worker_event_id"}.issubset(indices):
            raise ValueError("indexed read-only worker source unavailable; refusing full-table scan")
        worker_columns = {r[1] for r in con.execute("PRAGMA table_info(runner_workers)")}
        if not {"project_id", "worker_slot", "desired_state"}.issubset(worker_columns):
            raise ValueError("worker desired_state source missing; cannot classify paused workers")
        slot_columns = {r[1] for r in con.execute("PRAGMA table_info(ai_global_slots)")}
        if not {"slot", "project_id", "worker_slot"}.issubset(slot_columns):
            raise ValueError("global slot allocation source missing; no active-worker claim")
        queue_cols = {r[1] for r in con.execute("PRAGMA table_info(portfolio_queue)")}
        # Check every field queried below; older/partial schemas degrade
        # instead of throwing during a live read-only observation.
        queue_available = {"queue_id", "project_id", "worker_slot", "status",
                           "claimed_at", "claim_expires", "attempts",
                           "updated_at"}.issubset(queue_cols)
        con.execute("BEGIN")
        # This is the existing *slot assignment*, not an active queue lease.
        # Limit the total cardinality and never follow unowned conversation IDs.
        allocations = con.execute(
            "SELECT slot,project_id,worker_slot FROM ai_global_slots "
            "ORDER BY slot LIMIT 33").fetchall()
        if len(allocations) > 32:
            raise ValueError("global worker assignment source exceeds bounded cardinality")
        global_slots = {}
        for global_slot, pid, worker_slot in allocations:
            if (type(global_slot) is not int or not 1 <= global_slot <= 32
                    or type(pid) is not str or not PROJECT.fullmatch(pid)
                    or type(worker_slot) is not int or not 1 <= worker_slot <= 16
                    or (pid, worker_slot) in global_slots):
                raise ValueError("invalid current global slot assignment source")
            global_slots[(pid, worker_slot)] = global_slot
        for pid in projects:
            configured = con.execute(
                "SELECT worker_slot,desired_state FROM runner_workers "
                "WHERE project_id=? AND worker_slot BETWEEN 1 AND 16 "
                "ORDER BY worker_slot LIMIT ?", (pid, MAX_SLOTS+1)).fetchall()
            events = con.execute(
                "SELECT DISTINCT worker_slot FROM runner_events "
                "INDEXED BY runner_events_project_worker_id "
                "WHERE project_id=? AND worker_slot BETWEEN 1 AND 16 "
                "ORDER BY worker_slot LIMIT ?", (pid, MAX_SLOTS+1)).fetchall()
            desired = {}
            for slot, value in configured:
                if type(slot) is int and 1 <= slot <= 16:
                    desired[slot] = (value if value in ("running", "paused", "draining")
                                     else "unknown")
            slots = sorted(
                set(desired)
                | {slot for (slot,) in events if type(slot) is int and 1 <= slot <= 16}
                | {slot for (project, slot) in global_slots if project == pid}
            )
            if not slots:
                missing.append(pid)
            if len(slots) > MAX_SLOTS or len(configured) > MAX_SLOTS or len(events) > MAX_SLOTS:
                truncated.append(pid)
            for slot in slots[:MAX_SLOTS]:
                entries.append(_observe_slot(
                    con, pid, slot, observed, desired.get(slot, "unknown"),
                    global_slots.get((pid, slot)), queue_available))
        con.execute("COMMIT")
    finally:
        con.close()
    after = path.stat()
    if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or path.is_symlink()):
        raise ValueError("worker event source identity changed during observation")
    entries.sort(key=lambda row: (row["project_id"], row["worker_slot"]))
    # Only concrete operator-attention classes, not every stale or paused
    # worker, belong in the bounded intervention inbox. These are never
    # claim transfers, dispatch instructions or restart authorizations.
    actionable = {
        "allocated_paused_with_claimed_queue_owner_gate",
        "allocated_queue_claim_expired_or_invalid_owner_recheck",
        "allocated_no_active_claim_owner_review",
        "allocated_multiple_active_claims_owner_review",
        "allocated_queue_source_unavailable_owner_review",
        "recent_client_block_event_owner_review",
        "recent_browser_block_without_prompt_context_owner_review",
        "post_block_progress_signal_owner_verification_required",
        "generating_but_progress_unverified_or_stale",
        "worker_event_stale_or_unknown",
        "configured_running_no_event_evidence",
    }
    followups = []
    for row in sorted(entries, key=_operator_worker_priority):
        if row["classification"] not in actionable:
            continue
        followups.append({
            "project_id": row["project_id"],
            "worker_slot": row["worker_slot"],
            "classification": row["classification"],
            "safe_followup": row["safe_followup"],
            "current_queue_lease_observed": row["queue_lease_current_from_source"],
            "recent_block_events_after_prompt_in_sample":
                row["timeline"]["recent_block_events_after_last_prompt_in_sample"],
            "recent_progress_events_after_prompt_in_sample":
                row["timeline"]["recent_progress_events_after_last_prompt_in_sample"],
            "timeline_truncated": row["timeline"]["timeline_truncated"],
            "owner_claim_verified": False,
            "recovery_authorized": False,
            "continuation_verified": False,
        })
    return {
        "schema": "zsentinel-worker-progress-observation-v1",
        "owner_followup_candidates": followups[:MAX_PROJECTS * MAX_SLOTS],
        "followup_inventory_exhaustive": False,
        "observed_at_utc": observed.isoformat(timespec="seconds"),
        "coverage": ("bounded_partial_or_no_events" if missing or truncated
                     else "selected_project_event_slots_only"),
        "source": "existing_sqlite_mode_ro_query_only_indexed_runner_events",
        "source_file_identity_sha256_private": source_identity,
        "selected_projects": list(projects),
        "entries": entries,
        "unobserved_project_ids": missing,
        "truncated_project_ids": truncated,
        "event_inventory_complete": False,
        "global_slot_assignment_observed": True,
        "queue_claim_source_available": bool(queue_available),
        "live_worker_claims_verified": False,
        "worker_resumed_verified": False,
        "actions_performed": False,
        "database_write_authorized": False,
        "runner_restart_authorized": False,
    }


def operator_text(source):
    if (not isinstance(source, dict)
            or source.get("schema") != "zsentinel-worker-progress-observation-v1"
            or source.get("actions_performed") is not False
            or source.get("worker_resumed_verified") is not False):
        raise ValueError("invalid read-only worker source")
    observed = source.get("observed_at_utc")
    coverage = source.get("coverage")
    projects = source.get("selected_projects")
    if (type(observed) is not str or len(observed) > 45
            or coverage not in ("bounded_partial_or_no_events", "selected_project_event_slots_only")
            or type(projects) is not list or not 1 <= len(projects) <= MAX_PROJECTS
            or len(set(projects)) != len(projects)
            or any(type(pid) is not str or not PROJECT.fullmatch(pid) for pid in projects)):
        raise ValueError("invalid worker source timestamp/coverage")
    try:
        moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid worker source timestamp") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("worker source timestamp must have timezone")
    lines = ["WORKER VOORTGANG · bronmoment " + observed +
             " · dekking " + coverage + " · projecten " + ",".join(projects) +
             " · read-only eventbewijs; GEEN bewezen product-/claimhervatting."]
    if not _source_current(moment):
        lines.append("HISTORISCHE/ONBETROUWBARE BRONKLOK · ouder dan tien minuten "
                     "of >2 minuten in de toekomst. Geen actuele eigenaaractie "
                     "bewezen; vernieuw eerst de read-only bron.")
    for row in source.get("owner_followup_candidates", []):
        lines.append(
            ("  HISTORISCH / geen actuele eigenaaractie" if not _source_current(moment)
             else "  PRIORITEIT eigenaarcontrole") +
            f" · {row['project_id']} worker {row['worker_slot']}"
            f" · {row['classification']} · actie={row['safe_followup']}"
            f" · recente_blocks={row['recent_block_events_after_prompt_in_sample']}"
            f" · claim_niet_geverifieerd")
    for row in source["entries"]:
        lines.append(
            f"  {row['project_id']} worker {row['worker_slot']} · {row['classification']}"
            f" · desired_state={row['desired_state']}"
            f" · global_slot={row['allocated_global_slot'] if row['allocated_global_slot'] is not None else 'NIET TOEGEWEZEN'}"
            f" · queue_claim={row['queue_claim_status_observed'] or 'ONBEKEND/GEEN'}"
            f" · current_lease={row['queue_lease_current_from_source']}"
            f" · claim_window={row.get('claim_window_correlation', 'ONBEKEND')}"
            f" · event_in_claim_window={row.get('latest_event_in_current_claim_window', 'ONBEKEND')}"
            f" · progress_event_in_claim_window={row.get('latest_progress_event_in_current_claim_window', 'ONBEKEND')}"
            f" · event_age_s={row['event_age_seconds']}"
            f" · event_clock={row.get('event_timestamp_status', 'UNKNOWN')}"
            f" · progress_age_s={row['progress_age_seconds']}"
            f" · progress_clock={row.get('progress_timestamp_status', 'UNKNOWN')}
            f" · indexed_event_cursor={row.get('latest_indexed_event_id', 'ONBEKEND')}"
            f" · indexed_progress_event_cursor={row.get('latest_indexed_progress_event_id', 'ONBEKEND')}"
            f" · last_12_blocks_after_prompt={(row.get('timeline') or {}).get('recent_block_events_after_last_prompt_in_sample', 'ONBEKEND')}"
            f" · last_12_progress_after_prompt={(row.get('timeline') or {}).get('recent_progress_events_after_last_prompt_in_sample', 'ONBEKEND')}"
            f" · followup={row.get('safe_followup', 'owner_recheck_current_source_without_recovery')}"
            f" · claim=ONBEKEND")
    for pid in source["unobserved_project_ids"]:
        lines.append(f"  {pid} · GEEN eventbron voor gekozen project, geen bewijs van nul workers.")
    return "\n".join(lines)



def _operator_worker_priority(row):
    """Read-only urgency order, never ownership or recovery authorization."""
    if row.get("classification") == "allocated_paused_with_claimed_queue_owner_gate":
        return (0, row.get("project_id", ""), row.get("worker_slot", 0))
    if row.get("classification") == "allocated_queue_claim_expired_or_invalid_owner_recheck":
        return (1, row.get("project_id", ""), row.get("worker_slot", 0))
    if row.get("classification") in (
            "allocated_no_active_claim_owner_review",
            "allocated_multiple_active_claims_owner_review",
            "allocated_queue_source_unavailable_owner_review"):
        return (1, row.get("project_id", ""), row.get("worker_slot", 0))
    if (row.get("classification") == "recent_client_block_event_owner_review"
            and isinstance(row.get("timeline"), dict)
            and type(row["timeline"].get("recent_block_events_after_last_prompt_in_sample")) is int
            and row["timeline"]["recent_block_events_after_last_prompt_in_sample"] >= 2):
        return (1, row.get("project_id", ""), row.get("worker_slot", 0))
    if row.get("classification") == "recent_client_block_event_owner_review":
        return (2, row.get("project_id", ""), row.get("worker_slot", 0))
    if row.get("classification") == "recent_browser_block_without_prompt_context_owner_review":
        return (3, row.get("project_id", ""), row.get("worker_slot", 0))
    if row.get("classification") == "post_block_progress_signal_owner_verification_required":
        return (3, row.get("project_id", ""), row.get("worker_slot", 0))
    if row.get("classification") in (
            "generating_but_progress_unverified_or_stale",
            "worker_event_stale_or_unknown", "configured_running_no_event_evidence"):
        return (3, row.get("project_id", ""), row.get("worker_slot", 0))
    return (4, row.get("project_id", ""), row.get("worker_slot", 0))


def worker_progress_html(source):
    """Script-free, bounded view of index-backed event activity, never claims."""
    if (not isinstance(source, dict)
            or source.get("schema") != "zsentinel-worker-progress-observation-v1"
            or source.get("source") != "existing_sqlite_mode_ro_query_only_indexed_runner_events"
            or source.get("actions_performed") is not False
            or source.get("worker_resumed_verified") is not False
            or source.get("live_worker_claims_verified") is not False
            or source.get("database_write_authorized") is not False
            or not isinstance(source.get("entries"), list)
            or len(source["entries"]) > MAX_PROJECTS * MAX_SLOTS):
        raise ValueError("untrusted browser worker event evidence")
    from html import escape
    observed = source.get("observed_at_utc")
    coverage = source.get("coverage")
    projects = source.get("selected_projects")
    if (type(observed) is not str or len(observed) > 45
            or coverage not in ("bounded_partial_or_no_events", "selected_project_event_slots_only")
            or type(projects) is not list or not 1 <= len(projects) <= MAX_PROJECTS
            or len(set(projects)) != len(projects)
            or any(type(pid) is not str or not PROJECT.fullmatch(pid) for pid in projects)):
        raise ValueError("invalid worker source timestamp/coverage")
    try:
        moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid worker source timestamp") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("worker source timestamp must have timezone")
    source_current = _source_current(moment)
    # Archived v1 decisions may predate the additive chronology fields.
    # Preserve their historical cards while clearly marking newer evidence
    # UNKNOWN. Never interpret missing inbox data as zero problems.
    legacy_snapshot = "owner_followup_candidates" not in source
    inbox = source.get("owner_followup_candidates", [])
    if (type(inbox) is not list or len(inbox) > MAX_PROJECTS * MAX_SLOTS
            or (not legacy_snapshot and
                source.get("followup_inventory_exhaustive") is not False)):
        raise ValueError("untrusted bounded worker followup inbox")
    known = {(x.get("project_id"), x.get("worker_slot")): x
             for x in source["entries"] if isinstance(x, dict)}
    inbox_lines = []
    already = set()
    for entry in inbox:
        if type(entry) is not dict:
            raise ValueError("invalid worker owner followup candidate")
        key = (entry.get("project_id"), entry.get("worker_slot"))
        matching = known.get(key)
        if (matching is None or key in already
                or entry.get("classification") != matching.get("classification")
                or entry.get("safe_followup") != matching.get("safe_followup")
                or entry.get("owner_claim_verified") is not False
                or entry.get("recovery_authorized") is not False
                or entry.get("continuation_verified") is not False
                or entry.get("current_queue_lease_observed") is not
                    matching.get("queue_lease_current_from_source")):
            raise ValueError("inconsistent worker owner followup identity")
        already.add(key)
        count = entry.get("recent_block_events_after_prompt_in_sample")
        progress = entry.get("recent_progress_events_after_prompt_in_sample")
        if (type(count) is not int or not 0 <= count <= MAX_TIMELINE_EVENTS
                or type(progress) is not int or not 0 <= progress <= MAX_TIMELINE_EVENTS
                or type(entry.get("timeline_truncated")) is not bool
                or not isinstance(entry.get("project_id"), str)
                or not PROJECT.fullmatch(entry["project_id"])
                or type(entry.get("worker_slot")) is not int
                or not 1 <= entry["worker_slot"] <= 16):
            raise ValueError("invalid owner followup metrics")
        inbox_lines.append(
            "<li><strong>" + escape(entry["project_id"]) +
            " · worker " + str(entry["worker_slot"]) + "</strong> · " +
            escape(entry["safe_followup"]) +
            " · blokkades binnen beperkte sample: " + str(count) +
            " · voortgangssignalen: " + str(progress) +
            (" · HISTORISCHE BRON; huidige blokkade ONBEKEND" if not source_current else "") +
            " · claim NIET geverifieerd.</li>"
        )
    inbox_html = (
        '<aside class="panel warn"><h3>' +
        ('Historische signalen · geen actuele incidentenlijst' if not source_current
         else 'Eigenaarcontrole · begrensde actielijst') + '</h3>'
        '<p>Advies, geen claim/lease/restart-toestemming. Niet getoonde workers'
        ' zijn niet bewezen gezond. Huidige sample is onvolledig.</p>'
        + ("<ol>" + "".join(inbox_lines) + "</ol>" if inbox_lines else
           "<p>HISTORISCHE BRON: huidige eigenaarcontroles ONBEKEND;"
           " vernieuw de bron.</p>" if legacy_snapshot else
           "<p>Geen specifieke eigenaarcontrole in de beperkte sample.</p>")
        + "</aside>"
    )
    status = {
        "recent_client_block_event_owner_review": "Recent browserblokkadesignaal na opdracht; eigenaarcontrole",
        "recent_browser_block_without_prompt_context_owner_review":
            "Recent browserfoutsignaal zonder zichtbare prompt; opdrachttoewijzing ONBEKEND",
        "post_block_progress_signal_owner_verification_required":
            "Na eerdere blokkade een nieuwer generatie-event; herstel niet bewezen",
        "generation_progress_timestamp_observed": "Recente generatievoortgang waargenomen",
        "generating_but_progress_unverified_or_stale": "Genereren gemeld, werkelijke voortgang onbekend/stale",
        "worker_event_stale_or_unknown": "Geen recent worker-event; geen herstelbewijs",
        "generation_finish_event_observed_not_product_delivery": "Voltooide generatie gemeld; productresultaat onbekend",
        "recent_event_only_no_product_progress_proof": "Recent browser-event; geen productvoortgang bewezen",
        "configured_paused_not_incident": "Opzettelijk gepauzeerde worker; GEEN herstelincident",
        "allocated_paused_with_claimed_queue_owner_gate": "P0 EIGENAARCONTROLE: actieve queueclaim op toegewezen maar gepauzeerde worker",
        "allocated_queue_claim_expired_or_invalid_owner_recheck":
            "P1 EIGENAARCONTROLE: queueclaim bestaat maar lease is verlopen of onbetrouwbaar; GEEN vrijgavebewijs",
        "allocated_no_active_claim_owner_review":
            "EIGENAARCONTROLE: toegewezen worker zonder actieve queueclaim; browseractiviteit is geen bewijs van taakwerk",
        "allocated_multiple_active_claims_owner_review":
            "EIGENAARCONTROLE: meerdere actieve queueclaims op hetzelfde worker-slot; eigenaar moet dubbele claim beoordelen",
        "allocated_queue_source_unavailable_owner_review":
            "EIGENAARCONTROLE: queuebron onbeschikbaar of schema onvolledig; actieve claim ONBEKEND",
        "configured_not_allocated_not_a_stalled_worker": "Worker heeft geen globale toewijzing; oude config/events zijn GEEN actieve storing",
        "configured_draining_owner_hold": "Worker wordt door eigenaar gedraineerd; geen automatische herstart",
        "configured_running_no_event_evidence": "Worker staat op running maar heeft geen eventbron; eerst eigendom en start controleren",
        "desired_state_unknown_no_automatic_action": "Gewenste workerstatus onbekend; geen automatische actie",
    }
    rows = []
    # Show operator-attention candidates first, without hiding or
    # reassigning intentionally paused, draining or unallocated workers.
    for row in sorted(source["entries"], key=_operator_worker_priority):
        pid = row.get("project_id")
        slot = row.get("worker_slot")
        state = row.get("classification")
        desired = row.get("desired_state")
        global_slot = row.get("allocated_global_slot")
        claimed = row.get("queue_claim_status_observed")
        lease = row.get("queue_lease_current_from_source")
        if (claimed not in (None, "claimed", "running", "verifying")
                or type(lease) is not bool):
            raise ValueError("untrusted queue claim/lease evidence")
        if (global_slot is not None and
                (type(global_slot) is not int or not 1 <= global_slot <= 32)):
            raise ValueError("untrusted observed global worker slot")
        if (desired not in ("running", "paused", "draining", "unknown")
                or row.get("desired_state_source_observed") is not
                   (desired != "unknown")):
            raise ValueError("invalid worker desired state evidence")
        if (type(pid) is not str or not PROJECT.fullmatch(pid)
                or type(slot) is not int or not 1 <= slot <= 16
                or state not in status
                or row.get("owner_claim_verified") is not False
                or row.get("worker_resumed_verified") is not False
                or row.get("repair_authorized") is not False):
            raise ValueError("invalid browser progress state receipt")
        # Optional additive IDs allow safe before/after observations,
        # but are never authorizing claim/epoch tokens.
        for name in ("latest_indexed_event_id", "latest_indexed_progress_event_id"):
            if name in row:
                cursor = row[name]
                if cursor is not None and (
                        type(cursor) is not int or not 1 <= cursor <= 2**53 - 1):
                    raise ValueError("invalid worker event cursor")
        latest_id = row.get("latest_indexed_event_id")
        progress_id = row.get("latest_indexed_progress_event_id")
        if latest_id is not None and progress_id is not None and progress_id > latest_id:
            raise ValueError("progress cursor newer than last event")
        claim_fingerprint = row.get("claim_window_fingerprint")
        if (claim_fingerprint is not None and
                (type(claim_fingerprint) is not str
                 or not re.fullmatch(r"[0-9a-f]{64}", claim_fingerprint))):
            raise ValueError("invalid private claim-window fingerprint")
        for name in ("latest_event_in_current_claim_window",
                     "latest_progress_event_in_current_claim_window",
                     "progress_timestamp_in_current_claim_window"):
            if name in row and row[name] is not None and type(row[name]) is not bool:
                raise ValueError("invalid claim-window event flag")
        age = row.get("event_age_seconds")
        progress = row.get("progress_age_seconds")
        if any(value is not None and (type(value) is not int or value < 0)
               for value in (age, progress)):
            raise ValueError("invalid source age")
        legacy_row = "timeline" not in row and legacy_snapshot
        timeline = row.get("timeline") if not legacy_row else {
            "timeline_scope": "legacy_snapshot_no_event_chronology",
            "timeline_truncated": False,
            "claim_epoch_bound": False,
            "progress_is_product_delivery_proof": False,
            "recent_events_newest_first": [],
            "events_after_last_prompt_in_sample": 0,
            "recent_block_events_after_last_prompt_in_sample": 0,
            "recent_progress_events_after_last_prompt_in_sample": 0,
            "claim_window_correlation": "unavailable_no_valid_current_lease",
            "claim_window_event_count_in_sample": None,
            "claim_window_recent_block_count_in_sample": None,
            "claim_window_recent_progress_count_in_sample": None,
            "claim_window_counts_sample_only": True,
        }
        if (type(timeline) is not dict
                or timeline.get("timeline_scope") not in (
                    "last_12_indexed_events_not_complete_history",
                    "no_events_observed",
                    "legacy_snapshot_no_event_chronology")
                or type(timeline.get("timeline_truncated")) is not bool
                or timeline.get("claim_epoch_bound") is not False
                or timeline.get("progress_is_product_delivery_proof") is not False
                or type(timeline.get("recent_events_newest_first")) is not list
                or len(timeline["recent_events_newest_first"]) > MAX_TIMELINE_EVENTS):
            raise ValueError("untrusted bounded worker event chronology")
        claim_correlation = timeline.get(
            "claim_window_correlation", "unavailable_no_valid_current_lease")
        if claim_correlation not in (
                "time_window_only_not_identity_bound",
                "unavailable_no_valid_current_lease"):
            raise ValueError("invalid claim-window correlation")
        window_counts = (
            timeline.get("claim_window_event_count_in_sample"),
            timeline.get("claim_window_recent_block_count_in_sample"),
            timeline.get("claim_window_recent_progress_count_in_sample"),
        )
        if (timeline.get("claim_window_counts_sample_only") is not True
                or any(v is not None and
                       (type(v) is not int or not 0 <= v <= MAX_TIMELINE_EVENTS)
                       for v in window_counts)
                or (claim_correlation == "time_window_only_not_identity_bound"
                    and any(type(v) is not int for v in window_counts))
                or (claim_correlation == "unavailable_no_valid_current_lease"
                    and any(v is not None for v in window_counts))):
            raise ValueError("invalid bounded claim-window coverage")
        counts = (
            timeline.get("events_after_last_prompt_in_sample"),
            timeline.get("recent_block_events_after_last_prompt_in_sample"),
            timeline.get("recent_progress_events_after_last_prompt_in_sample"),
        )
        if any(type(v) is not int or not 0 <= v <= MAX_TIMELINE_EVENTS
               for v in counts):
            raise ValueError("invalid bounded timeline counters")
        followups = {
            "owner_check_paused_worker_with_current_queue_lease":
                "Actieve claim op gepauzeerde worker: eigenaarcontrole nodig.",
            "owner_reconcile_stale_queue_claim_without_claim_transfer":
                "Claimstatus bestaat maar lease is verlopen/ongeldig; laat bevoegde eigenaar handmatig toestand reconciliëren, zonder overname.",
            "owner_verify_unclaimed_allocated_slot":
                "Controleer huidige slottoewijzing en queue-eigenaar; zonder actieve claim geen bewijs dat dit slot aan deze taak werkt.",
            "owner_reconcile_duplicate_active_queue_claims":
                "Laat bevoegde queue-eigenaar dubbele actieve claim voor één slot beoordelen; geen reclaim, vrijgave of herverdeling.",
            "owner_recheck_queue_observer_source_scope":
                "Queue-bron ontbreekt of vereiste kolommen ontbreken; laat broneigenaar read-only schema/toegang beoordelen, geen restart.",
            "owner_inspect_current_assignment_and_recent_browser_block":
                "Open de recente browserblokkade bij de bevoegde workereigenaar.",
            "owner_verify_orphan_browser_block_without_prompt_identity":
                "Recent browserfoutsignaal maar geen opdrachtbron; verifieer eerst huidige prompt/toewijzing bij eigenaar.",
            "owner_verify_product_continuation_after_block_signal":
                "Ná blokkade is een nieuwer generatie-event gezien; laat eigenaar productcontinuïteit verifiëren.",
            "owner_verify_current_lease_and_real_worker_progress":
                "Controleer actuele lease en werkelijke worker-voortgang met eigenaar.",
            "continue_read_only_observation_without_takeover":
                "Voortgangssignaal gezien; blijf observeren zonder overname.",
            "owner_verify_actual_product_commit_or_handoff":
                "Controleer daadwerkelijke productcommit of overdracht met eigenaar.",
            "respect_draining_owner_hold":
                "Draining is een eigenaarbesluit; niet herstarten.",
            "no_stalled_worker_recovery_from_this_evidence":
                "Geen gerechtvaardigde vastgelopen-worker-recovery op basis van deze bron.",
            "owner_recheck_current_source_without_recovery":
                "Verifieer huidige bron bij eigenaar; geen herstelautorisatie.",
        }
        safe_followup = row.get(
            "safe_followup",
            "owner_recheck_current_source_without_recovery" if legacy_row else None)
        if safe_followup not in followups:
            raise ValueError("untrusted worker followup decision")
        event_items = []
        for point in timeline["recent_events_newest_first"]:
            if (type(point) is not dict or point.get("event") not in
                    SAFE_EVENT | {"other_event"}
                    or type(point.get("after_latest_prompt")) is not bool
                    or type(point.get("browser_generating")) is not bool
                    or type(point.get("browser_sending")) is not bool
                    or point.get("within_current_claim_window") not in (True, False, None)
                    or (point.get("age_seconds") is not None and (
                        type(point["age_seconds"]) is not int
                        or point["age_seconds"] < 0))):
                raise ValueError("invalid worker chronology entry")
            timestamp_status = point.get("timestamp_status", "UNKNOWN")
            if timestamp_status not in ("OBSERVED", "FUTURE", "UNKNOWN"):
                raise ValueError("invalid event timestamp status")
            ago = (str(point["age_seconds"]) + " sec geleden"
                   if point["age_seconds"] is not None else "leeftijd onbekend")
            window_label = (
                "binnen huidig claimvenster" if point["within_current_claim_window"] is True
                else "buiten huidig claimvenster" if point["within_current_claim_window"] is False
                else "claimvenster onbekend"
            )
            event_items.append("<li><strong>" + escape(point["event"]) +
                               "</strong> · " + ago +
                               " · tijdklok=" + escape(timestamp_status) +
                               (" · na laatste prompt" if point["after_latest_prompt"]
                                else " · vóór laatste prompt / geen prompt") +
                               " · " + window_label + "</li>")
        timeline_html = (
            '<details><summary>Recente worker-events · maximaal twaalf'
            + (" · afgekapt" if timeline["timeline_truncated"] else "")
            + '</summary><p class="small">Claim-windowcorrelatie: '
            + escape(claim_correlation) + '. Events worden alleen op tijdvenster'
            ' vergeleken; runner_events bevat geen queue-ID, dus dit is geen'
            ' exacte claimepoch-binding. Een afwezig event betekent niet dat'
            ' eerdere blokkades ontbraken. Venster-events/blokkades/voortgang'
            ' in sample: '
            + escape(" / ".join(str(v) if v is not None else "ONBEKEND" for v in window_counts))
            + '.</p><ol>'
            + "".join(event_items or ["<li>Geen events waargenomen.</li>"])
            + '</ol></details>'
        )
        if legacy_row:
            timeline_html = (
                '<p class="banner">HISTORISCHE BRON: geen tijdlijn beschikbaar.'
                ' Huidige blokkades of voortgang ONBEKEND; lees'
                ' de geïndexeerde bron opnieuw voor actueel bewijs.</p>'
            )
        rows.append(
            '<article class="panel ' +
            ('warn' if state in (
                "allocated_paused_with_claimed_queue_owner_gate",
                "allocated_queue_claim_expired_or_invalid_owner_recheck",
                "allocated_no_active_claim_owner_review",
                "allocated_multiple_active_claims_owner_review",
                "allocated_queue_source_unavailable_owner_review") else 'read') +
            '"><h3>' + escape(pid) + ' · worker ' +
            str(slot) + '</h3><p>' + escape(status[state]) +
            '</p><p class="small">Gewenste status: ' + escape(desired) +
            ' · Globale toewijzing: ' +
            (str(global_slot) if global_slot is not None else 'GEEN') +
            ' · Queueclaim: ' + escape(claimed or 'GEEN / ONBEKEND') +
            ' · Lease momenteel aantoonbaar geldig: ' +
            ('JA' if lease else 'ONBEKEND/NEE') +
            ' · Event in huidig claimvenster: ' +
            ('JA' if row.get("latest_event_in_current_claim_window") is True
             else 'NEE' if row.get("latest_event_in_current_claim_window") is False
             else 'ONBEKEND') +
            ' · Voortgangsevent in huidig claimvenster: ' +
            ('JA' if row.get("latest_progress_event_in_current_claim_window") is True
             else 'NEE' if row.get("latest_progress_event_in_current_claim_window") is False
             else 'ONBEKEND') +
            ' · Eventleeftijd: ' +
            (str(age) + ' sec' if age is not None else 'ONBEKEND') +
            ' · Laatste bronvoortgang: ' +
            (str(progress) + ' sec' if progress is not None else 'ONBEKEND') +
            ' · Laatste indexcursor/progress-eventcursor: ' +
            (str(row["latest_indexed_event_id"])
             if type(row.get("latest_indexed_event_id")) is int else 'ONBEKEND') +
            ' / ' +
            (str(row["latest_indexed_progress_event_id"])
             if type(row.get("latest_indexed_progress_event_id")) is int else 'ONBEKEND') +
            ' (browsersignaal; geen claimepoch)' +
            ' · Actuele sample: ' +
            ('ONBEKEND (historisch venster)' if legacy_row else
             str(counts[1]) + ' blokkeersignalen, ' +
             str(counts[2]) + ' voortgangssignalen na laatste prompt' +
             (' (afgekapt)' if timeline["timeline_truncated"] else '')) +
            '.</p><p><strong>Veilige vervolgstap:</strong> ' +
            escape(followups[safe_followup]) +
            '</p><p class="small">Actueel claimrecht: ONBEKEND · '
            'werkhervatting NIET bewezen.</p>' +
            timeline_html + '</article>')
    if not rows:
        rows.append('<p class="empty">Geen eventbron in gekozen projectvenster;'
                    ' dat bewijst GEEN nul actieve workers.</p>')
    return (
        '<section aria-label="Read-only browserworker voortgang">'
        '<h2>Browserworkers · werkelijk eventbewijs</h2>'
        '<p>Bronmoment: ' + escape(observed) + ' · dekking: ' + escape(coverage) +
        ' · projecten: ' + escape(", ".join(projects)) + '.</p>' +
        ('<p class="banner">HISTORISCHE OF ONBETROUWBARE BRONKLOK: ouder '
         'dan tien minuten of meer dan twee minuten in de toekomst. '
         'Actuele blokkades/claims zijn ONBEKEND; ververs de read-only bron.</p>'
         if not source_current else '') +
        ('<p>Historische volgorde van signalen; eerst een nieuwe read-only '
         'bronobservatie vóór eigenaar-escalatie. Iedere worker'
         if not source_current else
         '<p>Dringende eigenaarcontrole eerst: gepauzeerd met claim, herhaalde'
         ' actuele browserblokkades en ontbreken van voortgang. Iedere worker')
        ' bevat maximaal 12 recente metadata-events en een begrensde'
        ' vervolgstap. Geen claim-epoch-binding, browserstatus-GET,'
        ' herstart of werkafrondingsbewijs. Oude of ontbrekende events'
        ' bewijzen geen daadwerkelijke storing of herstel.</p>'
        + inbox_html + '<div class="grid">' + ''.join(rows) + '</div></section>'
    )


def main():
    parser = argparse.ArgumentParser(description="zSentinel indexed read-only browser worker progress")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument(
        "--format", choices=("text", "json", "html", "markdown"), default="text",
        help="standalone offline HTML/Markdown operator handoff without GitHub or server GET"
    )
    parser.add_argument("--output", type=Path,
                        help="create-only private .html/.md/.json report, never overwrite")
    parser.add_argument("--previous", type=Path,
                        help="optional earlier private --format json snapshot for before/after handoff")
    args = parser.parse_args()
    if args.output is not None and args.format not in ("html", "markdown", "json"):
        parser.error("--output requires --format html, markdown or json")
    if args.previous is not None and args.format not in ("html", "markdown"):
        parser.error("--previous requires --format html or markdown")
    try:
        receipt = observe_worker_progress(args.database, args.project)
        from zsentinel_worker_handoff import write_private_handoff
        if args.format in ("html", "markdown"):
            from zsentinel_worker_handoff import (
                render_html, render_markdown, load_private_previous
            )
            try:
                prior = load_private_previous(args.previous) if args.previous else None
            except OSError as exc:
                raise ValueError("private previous worker snapshot unavailable") from exc
            content = (render_html(receipt, previous=prior) if args.format == "html"
                       else render_markdown(receipt, previous=prior))
        elif args.format == "json":
            content = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
        else:
            content = operator_text(receipt)
        if args.output is not None:
            try:
                write_private_handoff(args.output, content, format=args.format)
            except OSError as exc:
                # Keep operator-private report paths out of CLI/log errors.
                raise ValueError(
                    "private report cannot be created (already exists or directory rights denied)"
                ) from exc
            print("Privé worker-handoff veilig aangemaakt (create-only; niets overschreven).")
        else:
            print(content)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, "zSentinel worker source unavailable: " + str(exc)[:180] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

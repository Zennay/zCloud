"""Pure, non-authorizing classification of zCloud worker evidence.

This module never accesses services or performs recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def classify_worker_evidence(
    record: dict[str, Any], *, now: datetime, max_age_seconds: int = 180
) -> dict[str, Any]:
    """Return conservative state; reject stale, ambiguous, or cross-assignment evidence."""
    if now.tzinfo is None or max_age_seconds <= 0:
        raise ValueError("timezone-aware now and positive max_age_seconds required")
    now = now.astimezone(timezone.utc)
    worker = record.get("worker_id")
    assignment = record.get("assignment_id")
    if not isinstance(worker, str) or not worker.strip() or not isinstance(assignment, str) or not assignment.strip():
        return {"state": "unknown", "reason": "missing_identity", "recovery_authorized": False}
    events = record.get("events")
    if not isinstance(events, list):
        return {"state": "unknown", "reason": "missing_events", "recovery_authorized": False}
    rank = {"prompt-sent": 1, "generation-started": 2, "generation-completed": 3}
    latest: tuple[datetime, int] | None = None
    conflicting_at_latest = False
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("worker_id") != worker or event.get("assignment_id") != assignment:
            continue
        kind = event.get("event")
        if kind not in rank:
            continue
        try:
            ts = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if ts.tzinfo is None:
            continue
        age = (now - ts.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= max_age_seconds:
            continue
        candidate = (ts.astimezone(timezone.utc), rank[kind])
        if latest is None or candidate[0] > latest[0]:
            latest = candidate
            conflicting_at_latest = False
        elif candidate[0] == latest[0] and candidate[1] != latest[1]:
            conflicting_at_latest = True
    if latest is None:
        return {"state": "unknown", "reason": "no_fresh_correlated_events", "recovery_authorized": False}
    if conflicting_at_latest:
        return {"state": "unknown", "reason": "ambiguous_same_timestamp", "recovery_authorized": False}
    best = latest[1]
    if best == 1:
        return {"state": "attempted", "reason": "prompt_only", "recovery_authorized": False}
    if best == 3:
        return {"state": "completed", "reason": "generation_completed_not_materiality", "recovery_authorized": False}
    return {"state": "generating", "reason": "generation_observed", "recovery_authorized": False}

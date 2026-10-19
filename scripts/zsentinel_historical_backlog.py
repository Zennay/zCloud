#!/usr/bin/env python3
"""Read-only historical blocker handoff from zSentinel's private incident ledger.

A missing incident in a later partial source read is NOT resolved. This module
renders a bounded historical investigation queue, never new live incidents,
notifications, claims, CI retries or permission-bearing recovery actions.
"""
from __future__ import annotations

from datetime import datetime, timezone

MAX_ROWS = 30
SEVERITY = {"P0": 0, "P1": 1}
SAFE_STATUS = "not_observed_needs_reconciliation"


def unresolved_backlog(state, now, *, max_rows=MAX_ROWS):
    """Return a bounded *historical* operator queue, retaining full count.

    Caller must first run load_state(), preserving the versioned ledger's
    structural checks and exact GitHub job identity. Missing older context
    remains explicitly UNKNOWN instead of fabricated from a partial read.
    """
    if not isinstance(state, dict) or not 1 <= max_rows <= MAX_ROWS:
        raise ValueError("invalid bounded historical incident state")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("historical view needs aware UTC time")
    current = now.astimezone(timezone.utc)
    rows = []
    for key, entry in state.items():
        if entry["status"] != SAFE_STATUS:
            continue
        context = entry.get("source_context")
        source_identity = entry.get("source_identity")
        if context is None:
            # Old v1 ledgers did not retain context; this remains a genuine
            # unknown, never inferred from a hash or guessed by project.
            project, reason, severity, human = ("unknown", "unattributed_historical_incident", "P1", False)
        else:
            project = context["project_id"]
            reason = context["reason"]
            severity = context["severity"]
            human = context["human_escalation"]
        first = datetime.fromisoformat(entry["first_seen"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(entry["last_seen"].replace("Z", "+00:00"))
        if last > current.replace(microsecond=0) and (last-current).total_seconds() > 120:
            raise ValueError("future-dated historical source")
        age = max(0, int((current-last.astimezone(timezone.utc)).total_seconds()//60))
        exact_job = None
        if source_identity is not None:
            exact_job = {
                "project_id": source_identity["project_id"],
                "run_id": source_identity["run_id"],
                "job_id": source_identity["job_id"],
                "head_sha": source_identity["head_sha"],
            }
        rows.append({
            "incident_id": key, "project_id": project,
            "severity": severity, "problem": reason,
            "human_escalation_historical": human,
            "first_seen": first.astimezone(timezone.utc).isoformat(),
            "last_seen": last.astimezone(timezone.utc).isoformat(),
            "minutes_since_last_observed": age,
            "source_status": SAFE_STATUS,
            "current_source_observed": False,
            "action": "Verify this exact historical source with an authorized owner; absence from a partial source is not recovery.",
            "exact_github_job": exact_job,
            "missing_historical_context": context is None,
            "mutation_authorized": False,
        })
    rows.sort(key=lambda x: (
        SEVERITY[x["severity"]], not x["human_escalation_historical"],
        -x["minutes_since_last_observed"], x["incident_id"]))
    return {
        "schema": "zsentinel-historical-backlog-v1",
        "total_unresolved": len(rows),
        "shown": rows[:max_rows],
        "truncated": len(rows) > max_rows,
        "source_partial": True,
        "action_authorized": False,
    }

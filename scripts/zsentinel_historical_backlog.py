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


def pin_historical_jobs(bundle, prior, registry, *, limit=3):
    """Opt-in pins for exact read-only GitHub refresh, never CI reruns.

    A historical pin can only fill a project with *no current GitHub job
    evidence*. It cannot replace a newer live job, owner receipt or claim.
    The existing refresh subsequently makes at most two read-only GitHub
    GETs per selected exact run/job, with canonical repo/head checking.
    """
    import copy

    if (type(limit) is not int or not 1 <= limit <= 3
            or not isinstance(bundle, dict) or not isinstance(registry, dict)):
        raise ValueError("invalid bounded historical recheck request")
    raw_evidence = bundle.get("evidence")
    projects = raw_evidence.get("projects") if isinstance(raw_evidence, dict) else None
    if not isinstance(projects, list) or len(projects) > 200:
        raise ValueError("historical job recheck requires a canonical source bundle")
    current = {}
    for item in projects:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("invalid recheck project")
        pid = item["id"]
        if pid in current:
            raise ValueError("duplicate project in historical recheck source")
        current[pid] = item
    if not isinstance(prior, dict):
        raise ValueError("historical recheck requires private prior ledger")
    # The caller must use load_state() first; fail closed on malformed
    # entries and never read tokens, ownership or arbitrary GitHub URLs here.
    output = copy.deepcopy(bundle)
    dest = {p["id"]: p for p in output["evidence"]["projects"]}
    candidates = unresolved_backlog(prior, datetime.now(timezone.utc), max_rows=MAX_ROWS)
    # Do not count projects with current pinned GitHub data as eligible.
    picked = []
    for row in candidates["shown"]:
        pin = row.get("exact_github_job")
        pid = row["project_id"]
        if pin is None or pid not in registry or pid not in current:
            continue
        if pid in (x["project_id"] for x in picked):
            continue
        source = current[pid].get("github", {})
        if type(source) is not dict:
            raise ValueError("malformed current GitHub observation")
        if source:
            # A newer job could be the actual incident. Never overwrite it.
            continue
        dest[pid]["github"] = {
            "run_id": pin["run_id"],
            "job_id": pin["job_id"],
            "head_sha": pin["head_sha"],
        }
        picked.append({
            "project_id": pid, "run_id": pin["run_id"],
            "job_id": pin["job_id"], "head_sha": pin["head_sha"],
            "historical_incident_id": row["incident_id"],
        })
        if len(picked) >= limit:
            break
    if not picked:
        raise ValueError("no historical exact jobs fit free project slots; current source pins untouched")
    return output, {
        "schema": "zsentinel-historical-recheck-selection-v1",
        "selected": picked,
        "selected_count": len(picked),
        "max_readonly_github_get": len(picked) * 2,
        "runner_restart_authorized": False,
        "ci_retry_authorized": False,
        "owner_claim_authorized": False,
        "source_partial": True,
    }

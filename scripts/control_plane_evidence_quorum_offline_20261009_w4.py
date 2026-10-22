"""Offline, non-authorizing conflicting-observation classifier.

This module cannot validate origins, grant action authority or mutate state.
"""
from collections.abc import Mapping

ALLOWED = frozenset(("running", "stopped", "unknown"))
MAX_SOURCES = 32

def assess(observations):
    """Classify independently supplied statuses without ever authorizing action.

    Each item must be a plain dict with a nonempty ASCII source (max 64 chars)
    and a bounded enumerated status. Duplicate sources and malformed evidence
    fail closed. An all-running observation is *not* execution proof.
    """
    denied = {"classification": "invalid", "authorizes_action": False,
              "source_count": 0}
    if type(observations) is not list or not 1 <= len(observations) <= MAX_SOURCES:
        return denied
    seen = set()
    statuses = set()
    for entry in observations:
        if type(entry) is not dict or set(entry) != {"source", "status"}:
            return denied
        source, status = entry["source"], entry["status"]
        if (type(source) is not str or not 1 <= len(source) <= 64
                or not all(c.isascii() and (c.isalnum() or c in "._-") for c in source)
                or type(status) is not str or status not in ALLOWED
                or source in seen):
            return denied
        seen.add(source)
        statuses.add(status)
    if "running" in statuses and "stopped" in statuses:
        classification = "contradictory"
    elif "unknown" in statuses:
        classification = "incomplete"
    elif statuses == {"running"}:
        classification = "unverified_running"
    else:
        classification = "unverified_stopped"
    return {"classification": classification, "authorizes_action": False,
            "source_count": len(seen)}

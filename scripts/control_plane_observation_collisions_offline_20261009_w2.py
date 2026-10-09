"""Offline-only worker observation identity collision detector.

This is an untrusted-data diagnostic, NEVER an authorization or liveness source.
Does not import or integrate with the production control plane.
"""
import re

_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_MAX = 256


def classify_observation_batch(observations):
    """Return a stable, non-authorizing verdict for a bounded plain-list batch.

    No normalization or coercion is applied to identifiers. Duplicate identities
    cannot be reconciled by last-writer-wins, even for identical observations.
    """
    denied = {"valid": False, "reason": "malformed_batch", "authorizes_action": False}
    if type(observations) is not list or not 1 <= len(observations) <= _MAX:
        return denied
    seen = set()
    for item in observations:
        if type(item) is not dict or set(item) != {"worker_id", "state"}:
            return denied
        worker_id, state = item["worker_id"], item["state"]
        if type(worker_id) is not str or not _ID.fullmatch(worker_id):
            return denied
        if type(state) is not str or state not in ("running", "stopped", "unknown"):
            return denied
        if worker_id in seen:
            return {"valid": False, "reason": "duplicate_worker_id", "authorizes_action": False}
        seen.add(worker_id)
    return {"valid": True, "reason": "distinct_untrusted_observations", "authorizes_action": False}

"""Offline, non-authorizing control-plane tri-state permission reference.

This module deliberately never approves actions. It rejects attempts to interpret
missing, string, numeric, or contradictory values as trusted authorization.
It is NOT wired into production and supplies no permission to mutate anything.
"""
from collections.abc import Mapping

EXPECTED = ("restart_allowed", "queue_write_allowed", "deploy_allowed")


def classify_permission_claim(claim):
    """Return a denial-only classification suitable for untrusted snapshots."""
    if not isinstance(claim, Mapping):
        return {"classification": "invalid", "authorizes_action": False}
    if set(claim) != set(EXPECTED):
        return {"classification": "invalid", "authorizes_action": False}
    if any(type(claim[k]) is not bool for k in EXPECTED):
        return {"classification": "invalid", "authorizes_action": False}
    if any(claim[k] for k in EXPECTED):
        return {"classification": "claimed_permission_untrusted", "authorizes_action": False}
    return {"classification": "explicit_denial", "authorizes_action": False}
